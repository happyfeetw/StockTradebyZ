from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import time
from typing import Any, Protocol

from stocktrade.domain.review import result_review_key

from ..storage.sqlite import ROOT
from .review_provider_runs import ReviewProviderInput, ReviewProviderItem, ReviewProviderValidationError

GEMINI_CLI_IMAGE_LIMIT_BYTES = 7 * 1024 * 1024

RATE_LIMIT_MARKERS = (
    "rate limit",
    "ratelimit",
    "quota",
    "too many requests",
    "resource exhausted",
    "exceeded",
    "daily limit",
    "no capacity available",
    "resource_exhausted",
)
RATE_LIMIT_PATTERNS = (
    re.compile(r"\b(?:http\s*)?429\b"),
    re.compile(r"\bstatus(?:\s+code)?\s*[:=]?\s*429\b"),
)
TRANSIENT_ERROR_MARKERS = (
    "premature close",
    "err_stream_premature_close",
    "econnreset",
    "socket hang up",
    "etimedout",
    "socket disconnected",
    "timed out",
    "timeout",
    "network",
)
CREDENTIAL_ERROR_MARKERS = (
    "oauth_creds.json",
    ".gemini",
    "eperm",
    "operation not permitted",
)


class GeminiCliProviderError(ReviewProviderValidationError):
    pass


class GeminiCliRateLimitError(GeminiCliProviderError):
    pass


class GeminiCliCredentialError(GeminiCliProviderError):
    pass


class GeminiCliTransientError(GeminiCliProviderError):
    pass


@dataclass(frozen=True)
class GeminiCliCompleted:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GeminiCliConfig:
    gemini_bin: str
    model: str
    output_format: str
    prompt: str
    timeout_seconds: int
    request_delay: float
    batch_size: int
    retry_backoff_seconds: list[float]
    retry_jitter_ratio: float
    fallback_to_single_on_batch_error: bool
    save_raw_cli_io: bool
    raw_log_dir: Path
    checkpoint_path: Path
    result_cache_dir: Path
    skip_existing: bool
    max_requests_per_run: int | None
    daily_request_budget: int | None
    usage_file: Path
    validate_gemini_bin: bool


class GeminiCliRunner(Protocol):
    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        prompt_text: str,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> GeminiCliCompleted:
        ...


def default_gemini_cli_runner(
    cmd: list[str],
    *,
    cwd: Path,
    prompt_text: str,
    timeout_seconds: int,
    env: dict[str, str],
) -> GeminiCliCompleted:
    completed = subprocess.run(
        cmd,
        input=prompt_text,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout_seconds,
        check=False,
    )
    return GeminiCliCompleted(
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


class GeminiCliReviewProviderExecutor:
    provider_name = "gemini-cli"

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        runner: GeminiCliRunner = default_gemini_cli_runner,
        sleeper=time.sleep,
    ) -> None:
        self.artifact_root = _resolve_root(artifact_root)
        self.runner = runner
        self.sleeper = sleeper
        self.requests_this_run = 0

    def run(self, request: ReviewProviderInput) -> list[dict[str, Any]]:
        if request.provider != self.provider_name:
            raise GeminiCliProviderError(f"unsupported Gemini CLI provider: {request.provider}")
        config = _build_config(
            request.provider_config,
            artifact_root=self.artifact_root,
            batch_id=request.candidate_batch_id,
        )
        self.requests_this_run = 0
        usage = DailyUsageTracker(config.usage_file, config.daily_request_budget)
        if config.validate_gemini_bin:
            _validate_gemini_bin(config.gemini_bin)

        config.result_cache_dir.mkdir(parents=True, exist_ok=True)
        if config.save_raw_cli_io:
            config.raw_log_dir.mkdir(parents=True, exist_ok=True)
        config.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        _write_checkpoint(
            config,
            status="started",
            message=f"candidate_batch_id={request.candidate_batch_id}, items={len(request.items)}",
        )

        cached_results: dict[str, dict[str, Any]] = {}
        pending: list[ReviewProviderItem] = []
        for item in request.items:
            cache_path = _result_cache_path(config, item.review_key)
            if config.skip_existing and cache_path.is_file():
                cached_results[item.review_key] = _read_json_object(cache_path)
                _write_checkpoint(
                    config,
                    status="skip_existing",
                    codes=[item.review_key],
                    message="cached gemini-cli result",
                )
            else:
                pending.append(item)

        fresh_results: list[dict[str, Any]] = []
        for index in range(0, len(pending), config.batch_size):
            batch = pending[index : index + config.batch_size]
            if not batch:
                continue
            fresh_results.extend(self._review_batch_with_fallback(batch, config=config, usage=usage))
            if index + config.batch_size < len(pending) and config.request_delay > 0:
                self.sleeper(config.request_delay)

        by_key = {result_review_key(result): result for result in fresh_results}
        by_key.update(cached_results)
        missing = [item.review_key for item in request.items if item.review_key not in by_key]
        if missing:
            _write_checkpoint(
                config,
                status="failed",
                codes=missing,
                message="missing provider results after Gemini CLI execution",
            )
            raise GeminiCliProviderError(
                "Gemini CLI provider did not produce all requested results: " + ", ".join(missing)
            )

        ordered = [by_key[item.review_key] for item in request.items]
        _write_checkpoint(config, status="finished", message=f"success={len(ordered)}, cached={len(cached_results)}")
        return ordered

    def _review_batch_with_fallback(
        self,
        items: list[ReviewProviderItem],
        *,
        config: GeminiCliConfig,
        usage: "DailyUsageTracker",
    ) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        retry_delays = config.retry_backoff_seconds
        for attempt in range(len(retry_delays) + 1):
            try:
                return self._review_batch_once(items, config=config, usage=usage)
            except (GeminiCliRateLimitError, GeminiCliTransientError) as exc:
                last_error = exc
                if attempt >= len(retry_delays):
                    break
                delay = _jittered_delay(retry_delays[attempt], config.retry_jitter_ratio)
                _write_checkpoint(
                    config,
                    status="retry_wait",
                    codes=[item.review_key for item in items],
                    message=str(exc),
                    attempt=attempt + 1,
                    next_delay=delay,
                )
                if delay > 0:
                    self.sleeper(delay)
            except GeminiCliCredentialError:
                raise
            except GeminiCliProviderError as exc:
                last_error = exc
                break

        if config.fallback_to_single_on_batch_error and len(items) > 1:
            if len(items) > 2:
                middle = len(items) // 2
                left = self._review_batch_with_fallback(items[:middle], config=config, usage=usage)
                right = self._review_batch_with_fallback(items[middle:], config=config, usage=usage)
                return left + right
            return [
                result
                for item in items
                for result in self._review_batch_with_fallback([item], config=config, usage=usage)
            ]

        assert last_error is not None
        raise last_error

    def _review_batch_once(
        self,
        items: list[ReviewProviderItem],
        *,
        config: GeminiCliConfig,
        usage: "DailyUsageTracker",
    ) -> list[dict[str, Any]]:
        _assert_request_budget(config, usage, self.requests_this_run)
        chart_paths = [_resolve_chart_path(item.chart_path, self.artifact_root) for item in items]
        for item, chart_path in zip(items, chart_paths):
            _validate_chart_for_cli(chart_path, item.review_key)
        cwd = chart_paths[0].parent
        prompt_items = [
            {
                "code": item.code,
                "strategy": item.strategy,
                "review_key": item.review_key,
                "chart_ref": _chart_ref_for_cli(chart_path, cwd),
            }
            for item, chart_path in zip(items, chart_paths)
        ]
        prompt_text = _build_batch_prompt(items=prompt_items, prompt=config.prompt)
        cmd = _build_command(config)
        raw_dir = _next_raw_call_dir(config, [item.review_key for item in items])
        _write_raw_request(
            config,
            raw_dir=raw_dir,
            prompt_text=prompt_text,
            cmd=cmd,
            cwd=cwd,
            items=items,
            chart_paths=chart_paths,
        )

        self.requests_this_run += 1
        usage.consume()
        try:
            completed = self.runner(
                cmd,
                cwd=cwd,
                prompt_text=prompt_text,
                timeout_seconds=config.timeout_seconds,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _bytes_or_text(exc.stdout)
            stderr = _bytes_or_text(exc.stderr)
            _write_raw_response(config, raw_dir=raw_dir, stdout=stdout, stderr=stderr, status="timeout", exit_code=None)
            raise GeminiCliTransientError(f"Gemini CLI timed out after {config.timeout_seconds} seconds") from exc

        _write_raw_response(
            config,
            raw_dir=raw_dir,
            stdout=completed.stdout,
            stderr=completed.stderr,
            status="finished",
            exit_code=completed.returncode,
        )
        combined = f"{completed.stdout}\n{completed.stderr}".strip()
        if completed.returncode != 0:
            _raise_for_cli_error(combined, returncode=completed.returncode)
        response_text = _unwrap_cli_output(completed.stdout, config.output_format)
        if _is_rate_limit_text(response_text):
            raise GeminiCliRateLimitError(response_text[:1200])

        parsed_items = _extract_json_array(response_text)
        if len(parsed_items) != len(items):
            raise GeminiCliProviderError(
                f"Gemini CLI batch result count mismatch: expected {len(items)}, got {len(parsed_items)}"
            )

        results: list[tuple[ReviewProviderItem, dict[str, Any]]] = []
        for item, parsed in zip(items, parsed_items):
            actual_code = str(parsed.get("code") or "")
            if actual_code and actual_code != item.code:
                raise GeminiCliProviderError(
                    f"Gemini CLI batch result order mismatch: expected {item.code}, got {actual_code}"
                )
            result = dict(parsed)
            result["code"] = item.code
            result["strategy"] = item.strategy
            result["review_key"] = item.review_key
            result["reviewer"] = self.provider_name
            if raw_dir is not None:
                result["provider_raw_log_dir"] = str(raw_dir)
            result["provider_model"] = config.model
            result["provider_output_format"] = config.output_format
            results.append((item, result))
        for item, result in results:
            _write_json_object(_result_cache_path(config, item.review_key), result)
        _write_checkpoint(
            config,
            status="batch_done",
            codes=[item.review_key for item in items],
            message=f"completed {len(items)} items",
        )
        return [result for _item, result in results]


class DailyUsageTracker:
    def __init__(self, path: Path, budget: int | None):
        self.path = path
        self.budget = budget
        self.today = date.today().isoformat()
        self.count = self._load_count()

    def can_consume(self) -> bool:
        return self.budget is None or self.count < self.budget

    def consume(self) -> None:
        self.count += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_object(self.path, {"date": self.today, "count": self.count})

    def _load_count(self) -> int:
        if not self.path.is_file():
            return 0
        try:
            payload = _read_json_object(self.path)
        except GeminiCliProviderError:
            return 0
        if payload.get("date") != self.today:
            return 0
        return int(payload.get("count") or 0)


def _build_config(provider_config: dict[str, Any], *, artifact_root: Path, batch_id: str) -> GeminiCliConfig:
    state_root = _resolve_optional_path(
        provider_config.get("state_dir"),
        default=artifact_root / "review-provider" / batch_id / "gemini-cli",
    )
    prompt = str(provider_config.get("prompt") or "").strip()
    if not prompt:
        prompt_path = _resolve_optional_path(provider_config.get("prompt_path"), default=ROOT / "agent" / "prompt.md")
        prompt = prompt_path.read_text(encoding="utf-8")

    output_format = str(provider_config.get("output_format") or "stream-json").strip().lower()
    if output_format not in {"text", "json", "stream-json"}:
        raise GeminiCliProviderError("output_format must be text, json, or stream-json")

    return GeminiCliConfig(
        gemini_bin=str(provider_config.get("gemini_bin") or "gemini"),
        model=str(provider_config.get("model") or "gemini-3.1-pro-preview"),
        output_format=output_format,
        prompt=prompt,
        timeout_seconds=int(provider_config.get("timeout_seconds") or 900),
        request_delay=float(provider_config.get("request_delay") or 0),
        batch_size=max(1, int(provider_config.get("batch_size") or 5)),
        retry_backoff_seconds=_float_list(
            provider_config.get("retry_backoff_seconds"),
            default=[30, 90, 180, 480, 900],
        ),
        retry_jitter_ratio=float(
            provider_config.get("retry_jitter_ratio")
            if provider_config.get("retry_jitter_ratio") is not None
            else 0.2
        ),
        fallback_to_single_on_batch_error=bool(provider_config.get("fallback_to_single_on_batch_error", True)),
        save_raw_cli_io=bool(provider_config.get("save_raw_cli_io", True)),
        raw_log_dir=_resolve_optional_path(provider_config.get("raw_log_dir"), default=state_root / "runs"),
        checkpoint_path=_resolve_optional_path(
            provider_config.get("checkpoint_path"),
            default=state_root / "gemini_cli_review_checkpoint.json",
        ),
        result_cache_dir=_resolve_optional_path(
            provider_config.get("result_cache_dir"),
            default=state_root / "results",
        ),
        skip_existing=bool(provider_config.get("skip_existing", True)),
        max_requests_per_run=_optional_int(provider_config.get("max_requests_per_run")),
        daily_request_budget=_optional_int(provider_config.get("daily_request_budget")),
        usage_file=_resolve_optional_path(
            provider_config.get("usage_file"),
            default=state_root / ".gemini_cli_usage.json",
        ),
        validate_gemini_bin=bool(provider_config.get("validate_gemini_bin", True)),
    )


def _build_batch_prompt(*, items: list[dict[str, str]], prompt: str) -> str:
    lines = [
        prompt,
        "",
        "---",
        "",
        f"Review {len(items)} stock charts. Each chart reference belongs to the code on the same numbered line:",
    ]
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. code: {item['code']}")
        if item.get("strategy"):
            lines.append(f"   strategy: {item['strategy']}")
        lines.append(f"   chart: {item['chart_ref']}")
    lines.extend(
        [
            "",
            "Return exactly one JSON array and no Markdown.",
            "The array length must match the input order.",
            "Each object must include code, signal_type, comment, and scores.",
        ]
    )
    return "\n".join(lines)


def _build_command(config: GeminiCliConfig) -> list[str]:
    cmd = [
        config.gemini_bin,
        "--skip-trust",
        "--approval-mode",
        "plan",
        "--output-format",
        config.output_format,
        "--prompt",
        "",
    ]
    if config.model:
        cmd[1:1] = ["--model", config.model]
    return cmd


def _raise_for_cli_error(text: str, *, returncode: int) -> None:
    if _is_credential_error_text(text):
        raise GeminiCliCredentialError("Gemini CLI credential files are not accessible")
    if _is_rate_limit_text(text):
        raise GeminiCliRateLimitError(text[:1200])
    if _is_transient_error_text(text):
        raise GeminiCliTransientError(text[:1200])
    raise GeminiCliProviderError(f"Gemini CLI exited with code {returncode}: {text[:1200]}")


def _unwrap_cli_output(stdout: str, output_format: str) -> str:
    text = stdout.strip()
    if not text:
        raise GeminiCliProviderError("Gemini CLI returned empty stdout")
    if output_format == "stream-json":
        return _unwrap_stream_json_output(text)
    if output_format == "text":
        return text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    payload = _find_text_payload(parsed)
    if not payload:
        raise GeminiCliProviderError(f"unable to find model text in Gemini CLI JSON output: {text[:500]}")
    return payload


def _unwrap_stream_json_output(stdout: str) -> str:
    payloads: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            role = event.get("role")
            if role and role != "assistant":
                continue
            if role == "assistant":
                payloads.extend(_collect_text_payloads(event.get("content")))
                continue
            if event.get("type") in {"init", "result"}:
                continue
        payloads.extend(_collect_text_payloads(event))
    joined = "".join(payloads).strip()
    if joined:
        return joined
    return _unwrap_cli_output(stdout, "json")


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    payload = _strip_json_fence(text)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("[")
        end = payload.rfind("]") + 1
        if start == -1 or end == 0:
            raise GeminiCliProviderError(f"unable to find JSON array in Gemini CLI output: {payload[:500]}")
        parsed = json.loads(payload[start:end])
    if isinstance(parsed, dict):
        for key in ("results", "items", "stocks", "reviews"):
            value = parsed.get(key)
            if isinstance(value, list):
                parsed = value
                break
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise GeminiCliProviderError(f"Gemini CLI output is not a JSON object array: {payload[:500]}")
    return parsed


def _collect_text_payloads(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        payloads: list[str] = []
        for item in value:
            payloads.extend(_collect_text_payloads(item))
        return payloads
    if isinstance(value, dict):
        payloads: list[str] = []
        text_keys = ("response", "text", "content", "delta", "value", "output", "result", "message")
        for key in text_keys:
            if key in value:
                payloads.extend(_collect_text_payloads(value[key]))
        for key, item in value.items():
            if key in text_keys or key in {"type", "role", "index", "id"}:
                continue
            if isinstance(item, (dict, list)):
                payloads.extend(_collect_text_payloads(item))
        return payloads
    return []


def _find_text_payload(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("response", "text", "content", "message", "output", "result"):
            payload = _find_text_payload(value.get(key))
            if payload:
                return payload
        for payload in value.values():
            found = _find_text_payload(payload)
            if found and "{" in found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_text_payload(item)
            if found and "{" in found:
                return found
        for item in value:
            found = _find_text_payload(item)
            if found:
                return found
    return None


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _resolve_chart_path(path: str | None, artifact_root: Path) -> Path:
    if not path:
        raise GeminiCliProviderError("provider review item is missing chart path")
    raw_path = Path(path)
    if raw_path.is_absolute():
        resolved = raw_path.resolve(strict=False)
    elif len(raw_path.parts) >= 2 and raw_path.parts[:2] == ("var", "artifacts"):
        resolved = (ROOT / raw_path).resolve(strict=False)
    else:
        resolved = (artifact_root / raw_path).resolve(strict=False)
    if not resolved.is_file():
        raise GeminiCliProviderError(f"chart artifact file not found: {resolved}")
    return resolved


def _validate_chart_for_cli(path: Path, review_key: str) -> None:
    image_size = path.stat().st_size
    if image_size > GEMINI_CLI_IMAGE_LIMIT_BYTES:
        raise GeminiCliProviderError(f"{review_key} chart exceeds Gemini CLI 7MB image limit")


def _chart_ref_for_cli(path: Path, cwd: Path) -> str:
    try:
        relative = path.resolve(strict=False).relative_to(cwd.resolve(strict=False))
        return f"@{relative.as_posix()}"
    except ValueError:
        return f"@{path.resolve(strict=False).as_posix()}"


def _next_raw_call_dir(config: GeminiCliConfig, review_keys: list[str]) -> Path | None:
    if not config.save_raw_cli_io:
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if len(review_keys) <= 2:
        raw_name = "-".join(review_keys)
    else:
        raw_name = f"{review_keys[0]}-{review_keys[-1]}_{len(review_keys)}"
    safe_name = _safe_name(raw_name)
    path = config.raw_log_dir / f"{timestamp}_{safe_name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_raw_request(
    config: GeminiCliConfig,
    *,
    raw_dir: Path | None,
    prompt_text: str,
    cmd: list[str],
    cwd: Path,
    items: list[ReviewProviderItem],
    chart_paths: list[Path],
) -> None:
    if raw_dir is None:
        return
    (raw_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
    _write_json_object(
        raw_dir / "meta.json",
        {
            "status": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "command": cmd,
            "cwd": str(cwd),
            "model": config.model,
            "output_format": config.output_format,
            "review_keys": [item.review_key for item in items],
            "chart_paths": [str(path) for path in chart_paths],
        },
    )


def _write_raw_response(
    config: GeminiCliConfig,
    *,
    raw_dir: Path | None,
    stdout: str,
    stderr: str,
    status: str,
    exit_code: int | None,
) -> None:
    if raw_dir is None:
        return
    (raw_dir / "stdout.jsonl").write_text(stdout, encoding="utf-8")
    (raw_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    meta_path = raw_dir / "meta.json"
    meta = _read_json_object(meta_path) if meta_path.is_file() else {}
    meta.update(
        {
            "status": status,
            "ended_at": datetime.now().isoformat(timespec="seconds"),
            "exit_code": exit_code,
        }
    )
    _write_json_object(meta_path, meta)


def _write_checkpoint(
    config: GeminiCliConfig,
    *,
    status: str,
    codes: list[str] | None = None,
    message: str = "",
    attempt: int | None = None,
    next_delay: float | None = None,
) -> None:
    _write_json_object(
        config.checkpoint_path,
        {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "codes": codes or [],
            "message": message[:1200],
            "attempt": attempt,
            "next_delay_seconds": next_delay,
        },
    )


def _assert_request_budget(config: GeminiCliConfig, usage: DailyUsageTracker, requests_this_run: int) -> None:
    if config.max_requests_per_run is not None and requests_this_run >= config.max_requests_per_run:
        raise GeminiCliProviderError(f"max_requests_per_run reached: {config.max_requests_per_run}")
    if not usage.can_consume():
        raise GeminiCliProviderError(f"daily_request_budget reached: {config.daily_request_budget}")


def _result_cache_path(config: GeminiCliConfig, review_key: str) -> Path:
    return config.result_cache_dir / f"{_safe_name(review_key)}.json"


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return safe[:120] or "unknown"


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeminiCliProviderError(f"failed to read JSON object: {path}") from exc
    if not isinstance(payload, dict):
        raise GeminiCliProviderError(f"JSON payload is not an object: {path}")
    return payload


def _write_json_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _resolve_root(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if not root.is_absolute():
        root = ROOT / root
    return root.resolve(strict=False)


def _resolve_optional_path(value: Any, *, default: Path) -> Path:
    if value is None or str(value).strip() == "":
        return default.resolve(strict=False)
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve(strict=False)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _float_list(value: Any, *, default: list[float]) -> list[float]:
    if value is None or value == "":
        return list(default)
    if isinstance(value, str):
        return [float(item.strip()) for item in value.split(",") if item.strip()]
    return [float(item) for item in value]


def _jittered_delay(base_delay: float, jitter_ratio: float) -> float:
    if base_delay <= 0 or jitter_ratio <= 0:
        return base_delay
    low = max(0.0, base_delay * (1 - jitter_ratio))
    high = base_delay * (1 + jitter_ratio)
    return round(random.uniform(low, high), 1)


def _validate_gemini_bin(gemini_bin: str) -> None:
    if os.sep in gemini_bin:
        if not Path(gemini_bin).exists():
            raise GeminiCliProviderError(f"Gemini CLI binary not found: {gemini_bin}")
        return
    if shutil.which(gemini_bin) is None:
        raise GeminiCliProviderError(f"Gemini CLI binary not found: {gemini_bin}")


def _bytes_or_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _is_rate_limit_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS) or any(
        pattern.search(lower) for pattern in RATE_LIMIT_PATTERNS
    )


def _is_credential_error_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in CREDENTIAL_ERROR_MARKERS)


def _is_transient_error_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in TRANSIENT_ERROR_MARKERS) or _is_rate_limit_text(text)
