"""
gemini_cli_review.py
~~~~~~~~~~~~~~~~~~~~
使用本机 Gemini CLI 对候选股票进行图表分析评分。

用法：
    python agent/gemini_cli_review.py
    python agent/gemini_cli_review.py --config config/gemini_cli_review.yaml

前置条件：
    gemini CLI 已安装，并已在本机完成账号登录。

输出：
    ./data/review/{pick_date}/{code}.json
    ./data/review/{pick_date}/suggestion.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import shlex
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml
from PIL import Image

from base_reviewer import BaseReviewer

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "gemini_cli_review.yaml"
_TMP_ASSET_DIR = _ROOT / ".gemini_cli_tmp"
GEMINI_CLI_IMAGE_LIMIT = 3000
GEMINI_CLI_BATCH_TARGET_RATIO = 0.90
MAX_BATCH_SIZE = int(GEMINI_CLI_IMAGE_LIMIT * GEMINI_CLI_BATCH_TARGET_RATIO)
MAX_IMAGE_BYTES = 7 * 1024 * 1024
GEMINI_CONTEXT_LIMIT_TOKENS = 1_048_576
MAX_CONTEXT_TOKENS = int(GEMINI_CONTEXT_LIMIT_TOKENS * GEMINI_CLI_BATCH_TARGET_RATIO)
IMAGE_TILE_SIZE = 384
ESTIMATED_TOKENS_PER_TILE = 290
ESTIMATED_OUTPUT_TOKENS_PER_STOCK = 800
ESTIMATED_PROMPT_TOKEN_RESERVE = 20_000
DEFAULT_BATCH_SIZE = 10

DEFAULT_CONFIG: dict[str, Any] = {
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review",
    "prompt_path": "agent/prompt.md",
    "gemini_bin": "gemini",
    "model": "gemini-3.1-pro-preview",
    "output_format": "json",
    "timeout_seconds": 900,
    "request_delay": 10,
    "batch_size": DEFAULT_BATCH_SIZE,
    "fallback_to_single_on_batch_error": True,
    "retry_backoff_seconds": [30, 90, 180, 480, 900],
    "retry_jitter_ratio": 0.2,
    "max_requests_per_run": 50,
    "daily_request_budget": 80,
    "usage_file": "data/review/.gemini_cli_usage.json",
    "stop_on_rate_limit": False,
    "rate_limit_backoff_seconds": 300,
    "skip_existing": True,
    "suggest_min_score": 4.0,
}

RATE_LIMIT_MARKERS = (
    "rate limit",
    "ratelimit",
    "quota",
    "too many requests",
    "resource exhausted",
    "429",
    "exceeded",
    "per minute",
    "daily limit",
    "no capacity available",
    "capacity available",
    "resource_exhausted",
)

TRANSIENT_ERROR_MARKERS = (
    "premature close",
    "err_stream_premature_close",
    "econnreset",
    "etimedout",
    "socket disconnected",
    "socket hang up",
    "tls connection",
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


class GeminiCliError(RuntimeError):
    pass


class GeminiCliRateLimitError(GeminiCliError):
    pass


class GeminiCliCredentialError(GeminiCliError):
    pass


def _resolve_cfg_path(path_like: str | Path, base_dir: Path = _ROOT) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else (base_dir / p)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _is_rate_limit_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


def _is_credential_error_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in CREDENTIAL_ERROR_MARKERS)


def _is_transient_error_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in TRANSIENT_ERROR_MARKERS) or _is_rate_limit_text(text)


def _optional_float_list(value: Any) -> list[float]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [float(item.strip()) for item in value.split(",") if item.strip()]
    return [float(item) for item in value]


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


def _unwrap_cli_output(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        raise GeminiCliError("Gemini CLI 返回空 stdout")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text

    payload = _find_text_payload(parsed)
    if not payload:
        raise GeminiCliError(f"无法从 Gemini CLI JSON 输出中找到模型正文：{text[:500]}")
    return payload


def _extract_json_array(text: str) -> list[dict]:
    payload = _strip_json_fence(text)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("[")
        end = payload.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError(f"未能在模型输出中找到 JSON 数组:\n{payload}")
        parsed = json.loads(payload[start:end])

    if isinstance(parsed, dict):
        for key in ("results", "items", "stocks", "reviews"):
            value = parsed.get(key)
            if isinstance(value, list):
                parsed = value
                break

    if not isinstance(parsed, list):
        raise ValueError(f"模型输出不是 JSON 数组:\n{payload}")
    if not all(isinstance(item, dict) for item in parsed):
        raise ValueError(f"模型输出数组中存在非对象元素:\n{payload}")
    return parsed


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    cfg_path = config_path or _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"找不到配置文件：{cfg_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = {**DEFAULT_CONFIG, **raw}
    cfg["candidates"] = _resolve_cfg_path(cfg["candidates"])
    cfg["kline_dir"] = _resolve_cfg_path(cfg["kline_dir"])
    cfg["output_dir"] = _resolve_cfg_path(cfg["output_dir"])
    cfg["prompt_path"] = _resolve_cfg_path(cfg["prompt_path"])
    cfg["usage_file"] = _resolve_cfg_path(cfg["usage_file"])
    cfg["max_requests_per_run"] = _optional_int(cfg.get("max_requests_per_run"))
    cfg["daily_request_budget"] = _optional_int(cfg.get("daily_request_budget"))
    cfg["timeout_seconds"] = int(cfg.get("timeout_seconds", 900))
    cfg["rate_limit_backoff_seconds"] = int(cfg.get("rate_limit_backoff_seconds", 300))
    cfg["retry_backoff_seconds"] = _optional_float_list(cfg.get("retry_backoff_seconds", [30, 90, 180, 480, 900]))
    cfg["retry_jitter_ratio"] = float(cfg.get("retry_jitter_ratio", 0.2))
    cfg["request_delay"] = float(cfg.get("request_delay", 10))
    cfg["batch_size"] = int(cfg.get("batch_size", DEFAULT_BATCH_SIZE))
    if cfg["batch_size"] < 1 or cfg["batch_size"] > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size 必须在 1 到 {MAX_BATCH_SIZE} 之间")

    output_format = str(cfg.get("output_format", "json")).strip().lower()
    if output_format not in {"text", "json"}:
        raise ValueError("output_format 只能是 text 或 json")
    cfg["output_format"] = output_format

    return cfg


def estimate_image_tokens(path: Path) -> int:
    with Image.open(path) as img:
        width, height = img.size
    tiles = max(1, ((width + IMAGE_TILE_SIZE - 1) // IMAGE_TILE_SIZE) * ((height + IMAGE_TILE_SIZE - 1) // IMAGE_TILE_SIZE))
    return int(tiles * ESTIMATED_TOKENS_PER_TILE)


class DailyUsageTracker:
    def __init__(self, path: Path, budget: int | None):
        self.path = path
        self.budget = budget
        self.today = date.today().isoformat()
        self.count = self._load_count()

    def _load_count(self) -> int:
        if not self.path.exists():
            return 0
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return 0
        if data.get("date") != self.today:
            return 0
        return int(data.get("count", 0))

    def can_consume(self) -> bool:
        return self.budget is None or self.count < self.budget

    def remaining(self) -> int | None:
        if self.budget is None:
            return None
        return max(self.budget - self.count, 0)

    def consume(self) -> None:
        self.count += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"date": self.today, "count": self.count}, f, ensure_ascii=False, indent=2)


class GeminiCliReviewer(BaseReviewer):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.gemini_bin = str(config.get("gemini_bin", "gemini"))
        self.max_requests_per_run: int | None = config.get("max_requests_per_run")
        self.requests_this_run = 0
        self.checkpoint_path: Path | None = None
        self.usage = DailyUsageTracker(
            path=Path(config["usage_file"]),
            budget=config.get("daily_request_budget"),
        )
        self._validate_gemini_bin()

    def _validate_gemini_bin(self) -> None:
        if os.sep in self.gemini_bin:
            if not Path(self.gemini_bin).exists():
                raise FileNotFoundError(f"找不到 gemini CLI：{self.gemini_bin}")
            return
        if shutil.which(self.gemini_bin) is None:
            raise FileNotFoundError(f"找不到 gemini CLI：{self.gemini_bin}")

    def _can_start_request(self) -> tuple[bool, str]:
        if self.max_requests_per_run is not None and self.requests_this_run >= self.max_requests_per_run:
            return False, f"已达到单次运行请求上限 max_requests_per_run={self.max_requests_per_run}"
        if not self.usage.can_consume():
            return False, f"已达到每日请求预算 daily_request_budget={self.config.get('daily_request_budget')}"
        return True, ""

    @staticmethod
    def _iter_pending_codes(candidates: Iterable[dict]) -> list[str]:
        return [str(candidate.get("code", "")) for candidate in candidates if candidate.get("code")]

    def _copy_chart_for_cli(self, source: Path, code: str) -> tuple[Path, str]:
        image_size = source.stat().st_size
        if image_size > MAX_IMAGE_BYTES:
            raise GeminiCliError(
                f"{code} 图片超过 Gemini CLI 单图大小限制 7MB：{image_size / 1024 / 1024:.2f}MB"
            )
        suffix = source.suffix.lower() or ".jpg"
        _TMP_ASSET_DIR.mkdir(parents=True, exist_ok=True)
        target = _TMP_ASSET_DIR / f"{code}_day{suffix}"
        shutil.copy2(source, target)
        rel = target.relative_to(_ROOT).as_posix()
        return target, f"@{rel}"

    def _build_prompt(self, *, code: str, chart_ref: str, prompt: str) -> str:
        return (
            f"{prompt}\n\n"
            "---\n\n"
            f"股票代码：{code}\n"
            f"日线图：{chart_ref}\n\n"
            "请读取上面的日线图，严格按照评分规则完成复评。"
            "只输出一个 JSON 对象，不要输出 Markdown、解释文字或额外字段。"
        )

    def _build_batch_prompt(self, *, items: list[dict[str, Any]], prompt: str) -> str:
        lines = [
            f"{prompt}",
            "",
            "---",
            "",
            f"本批需要复评 {len(items)} 支股票。每张日线图与股票代码严格一一对应：",
        ]
        for index, item in enumerate(items, 1):
            lines.append(f"{index}. 股票代码：{item['code']}")
            lines.append(f"   日线图：{item['chart_ref']}")

        lines.extend(
            [
                "",
                "请分别读取每张日线图，严格按照评分规则逐只完成复评。",
                "本批输出格式覆盖上方单股输出格式：只输出一个 JSON 数组，不要输出 Markdown、解释文字或额外字段。",
                "数组长度必须等于本批股票数量，顺序必须与上方股票列表一致。",
                "数组中每个对象必须包含 code 字段，并保留单股输出格式里的所有字段。",
            ]
        )
        return "\n".join(lines)

    def _build_command(self) -> list[str]:
        cmd = [
            self.gemini_bin,
            "--skip-trust",
            "--approval-mode",
            "plan",
            "--output-format",
            self.config.get("output_format", "json"),
            "--prompt",
            "",
        ]
        model = str(self.config.get("model", "") or "").strip()
        if model:
            cmd[1:1] = ["--model", model]
        return cmd

    @staticmethod
    def _send_process_signal(proc: subprocess.Popen, sig: int) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, sig)
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            return

    def _terminate_cli_process(self, proc: subprocess.Popen) -> tuple[str, str]:
        self._send_process_signal(proc, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self._send_process_signal(proc, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=5)
        return stdout or "", stderr or ""

    def _run_cli_command(self, cmd: list[str], env: dict[str, str], prompt_text: str) -> subprocess.CompletedProcess[str]:
        model = str(self.config.get("model", "") or "").strip() or "(Gemini CLI default)"
        print(f"[Command] Gemini CLI 实际命令: {shlex.join(cmd)}")
        print(f"[INFO] Gemini CLI model: {model}")
        popen_kwargs: dict[str, Any] = {
            "cwd": str(_ROOT),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "env": env,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)
        timeout = self.config.get("timeout_seconds", 900)
        try:
            stdout, stderr = proc.communicate(input=prompt_text, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = self._terminate_cli_process(proc)
            detail = f"{stdout}\n{stderr}".strip()
            if _is_credential_error_text(detail):
                raise GeminiCliCredentialError(
                    "Gemini CLI 凭证不可访问：无法打开 ~/.gemini/oauth_creds.json。"
                    "请从普通终端启动 workbench，或让当前运行环境具备 ~/.gemini 读写权限。"
                ) from exc
            suffix = f": {detail[:500]}" if detail else ""
            raise GeminiCliError(f"Gemini CLI 超时（{timeout} 秒）{suffix}") from exc

        return subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
        )

    def _consume_cli_request(self, cmd: list[str], env: dict[str, str], prompt_text: str) -> subprocess.CompletedProcess[str]:
        ok, reason = self._can_start_request()
        if not ok:
            raise GeminiCliError(reason)
        self.requests_this_run += 1
        self.usage.consume()
        return self._run_cli_command(cmd, env, prompt_text)

    def review_stock(self, code: str, day_chart: Path, prompt: str) -> dict:
        tmp_chart, chart_ref = self._copy_chart_for_cli(day_chart, code)
        prompt_text = self._build_prompt(code=code, chart_ref=chart_ref, prompt=prompt)
        cmd = self._build_command()
        env = {**os.environ, "NO_COLOR": "1"}

        try:
            result = self._consume_cli_request(cmd, env, prompt_text)
        finally:
            try:
                tmp_chart.unlink()
            except FileNotFoundError:
                pass

        combined_output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode != 0:
            if _is_credential_error_text(combined_output):
                raise GeminiCliCredentialError(
                    "Gemini CLI 凭证不可访问：无法打开 ~/.gemini/oauth_creds.json。"
                    "请从普通终端启动 workbench，或让当前运行环境具备 ~/.gemini 读写权限。"
                )
            if _is_rate_limit_text(combined_output):
                raise GeminiCliRateLimitError(combined_output)
            raise GeminiCliError(f"Gemini CLI 退出码 {result.returncode}: {combined_output[:1200]}")

        response_text = _unwrap_cli_output(result.stdout)
        if _is_rate_limit_text(response_text):
            raise GeminiCliRateLimitError(response_text)

        parsed = self.extract_json(response_text)
        parsed["code"] = code
        parsed["reviewer"] = "gemini-cli"
        return parsed

    def review_batch(self, items: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
        tmp_charts: list[Path] = []
        prompt_items: list[dict[str, Any]] = []
        for item in items:
            tmp_chart, chart_ref = self._copy_chart_for_cli(item["day_chart"], item["code"])
            tmp_charts.append(tmp_chart)
            prompt_items.append({"code": item["code"], "chart_ref": chart_ref})

        prompt_text = self._build_batch_prompt(items=prompt_items, prompt=prompt)
        cmd = self._build_command()
        env = {**os.environ, "NO_COLOR": "1"}

        try:
            result = self._consume_cli_request(cmd, env, prompt_text)
        finally:
            for tmp_chart in tmp_charts:
                try:
                    tmp_chart.unlink()
                except FileNotFoundError:
                    pass

        combined_output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode != 0:
            if _is_credential_error_text(combined_output):
                raise GeminiCliCredentialError(
                    "Gemini CLI 凭证不可访问：无法打开 ~/.gemini/oauth_creds.json。"
                    "请从普通终端启动 workbench，或让当前运行环境具备 ~/.gemini 读写权限。"
                )
            if _is_rate_limit_text(combined_output):
                raise GeminiCliRateLimitError(combined_output)
            raise GeminiCliError(f"Gemini CLI 退出码 {result.returncode}: {combined_output[:1200]}")

        response_text = _unwrap_cli_output(result.stdout)
        if _is_rate_limit_text(response_text):
            raise GeminiCliRateLimitError(response_text)

        parsed_items = _extract_json_array(response_text)
        expected_codes = [item["code"] for item in items]
        if len(parsed_items) != len(expected_codes):
            raise GeminiCliError(
                f"Gemini CLI 批量返回数量不匹配：期望 {len(expected_codes)}，实际 {len(parsed_items)}"
            )

        results: list[dict[str, Any]] = []
        for expected_code, parsed in zip(expected_codes, parsed_items):
            actual_code = str(parsed.get("code", "") or "")
            if actual_code and actual_code != expected_code:
                raise GeminiCliError(f"Gemini CLI 批量返回代码顺序不匹配：期望 {expected_code}，实际 {actual_code}")
            parsed["code"] = expected_code
            parsed["reviewer"] = "gemini-cli"
            results.append(parsed)
        return results

    @staticmethod
    def _format_result_status(result: dict[str, Any]) -> str:
        verdict = result.get("verdict", "?")
        score = result.get("total_score", "?")
        return f"verdict={verdict}, score={score}"

    @staticmethod
    def _write_stock_result(item: dict[str, Any], result: dict[str, Any]) -> None:
        with open(item["out_file"], "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def _write_checkpoint(
        self,
        *,
        status: str,
        codes: list[str] | None = None,
        message: str = "",
        attempt: int | None = None,
        next_delay: float | None = None,
    ) -> None:
        if self.checkpoint_path is None:
            return
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "codes": codes or [],
            "message": message[:1200],
            "attempt": attempt,
            "next_delay_seconds": next_delay,
            "requests_this_run": self.requests_this_run,
            "daily_usage_count": self.usage.count,
        }
        tmp = self.checkpoint_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.checkpoint_path)

    def _retry_delays(self) -> list[float]:
        return [float(item) for item in self.config.get("retry_backoff_seconds", [])]

    def _jittered_delay(self, base_delay: float) -> float:
        jitter_ratio = max(0.0, float(self.config.get("retry_jitter_ratio", 0.2)))
        if base_delay <= 0 or jitter_ratio == 0:
            return base_delay
        low = max(0.0, base_delay * (1 - jitter_ratio))
        high = base_delay * (1 + jitter_ratio)
        return round(random.uniform(low, high), 1)

    def _sleep_before_retry(
        self,
        seconds: float,
        message: str,
        *,
        codes: list[str] | None = None,
        attempt: int | None = None,
    ) -> None:
        self._write_checkpoint(
            status="retry_wait",
            codes=codes,
            message=message,
            attempt=attempt,
            next_delay=seconds,
        )
        if seconds <= 0:
            print(message)
            return
        print(f"{message}，{seconds} 秒后重试。")
        time.sleep(seconds)

    @staticmethod
    def _codes(items: list[dict[str, Any]]) -> list[str]:
        return [str(item["code"]) for item in items]

    def _review_single_items(
        self,
        items: list[dict[str, Any]],
        total_candidates: int,
    ) -> tuple[list[dict[str, Any]], list[str], str]:
        all_results: list[dict[str, Any]] = []
        failed_codes: list[str] = []
        stop_reason = ""

        request_delay = self.config.get("request_delay", 10)
        retry_delays = self._retry_delays()

        for offset, item in enumerate(items):
            code = item["code"]
            attempt = 0

            while True:
                ok, reason = self._can_start_request()
                if not ok:
                    stop_reason = reason
                    print(f"[STOP] {reason}")
                    break

                print(f"[{item['index']}/{total_candidates}] {code} — Gemini CLI 正在分析 ...", end=" ", flush=True)
                try:
                    result = self.review_stock(code=code, day_chart=item["day_chart"], prompt=self.prompt)
                    self._write_stock_result(item, result)
                    all_results.append(result)
                    print(f"完成 — {self._format_result_status(result)}")
                    self._write_checkpoint(status="stock_done", codes=[code], message=self._format_result_status(result))
                    break
                except GeminiCliRateLimitError as exc:
                    print(f"限流/容量错误 — {str(exc)[:500]}")
                    if attempt < len(retry_delays):
                        delay = self._jittered_delay(retry_delays[attempt])
                        attempt += 1
                        self._sleep_before_retry(
                            delay,
                            f"[INFO] {code} 命中限流/容量不足，重试 {attempt}/{len(retry_delays)}",
                            codes=[code],
                            attempt=attempt,
                        )
                        continue
                    failed_codes.append(code)
                    if self.config.get("stop_on_rate_limit", True):
                        stop_reason = "Gemini CLI 命中限流或额度限制"
                    else:
                        print(f"[WARN] {code} 达到限流重试上限，跳过该股并继续。")
                    break
                except GeminiCliCredentialError as exc:
                    failed_codes.append(code)
                    stop_reason = str(exc)
                    print(f"凭证错误 — {stop_reason}")
                    break
                except Exception as exc:
                    message = str(exc)
                    if _is_transient_error_text(message) and attempt < len(retry_delays):
                        delay = self._jittered_delay(retry_delays[attempt])
                        attempt += 1
                        print(f"失败 — {exc}")
                        self._sleep_before_retry(
                            delay,
                            f"[INFO] {code} 瞬时错误，重试 {attempt}/{len(retry_delays)}",
                            codes=[code],
                            attempt=attempt,
                        )
                        continue
                    print(f"失败 — {exc}")
                    failed_codes.append(code)
                    break

            if stop_reason:
                break

            if offset < len(items) - 1:
                time.sleep(request_delay)

        return all_results, failed_codes, stop_reason

    def _review_batch_items(
        self,
        items: list[dict[str, Any]],
        total_candidates: int,
    ) -> tuple[list[dict[str, Any]], list[str], str]:
        if not items:
            return [], [], ""
        if len(items) == 1 or self.config.get("batch_size", 1) == 1:
            return self._review_single_items(items, total_candidates)

        start_index = items[0]["index"]
        end_index = items[-1]["index"]
        codes = self._codes(items)
        retry_delays = self._retry_delays()

        for attempt in range(len(retry_delays) + 1):
            ok, reason = self._can_start_request()
            if not ok:
                print(f"[STOP] {reason}")
                return [], [], reason

            action = "批量分析" if attempt == 0 else f"批量重试 {attempt}/{len(retry_delays)}"
            print(
                f"[{start_index}-{end_index}/{total_candidates}] "
                f"{','.join(codes)} — Gemini CLI {action} {len(items)} 张图 ...",
                end=" ",
                flush=True,
            )

            try:
                results = self.review_batch(items=items, prompt=self.prompt)
                for item, result in zip(items, results):
                    self._write_stock_result(item, result)
                print("完成")
                for result in results:
                    print(f"    {result['code']} — {self._format_result_status(result)}")
                self._write_checkpoint(status="batch_done", codes=codes, message=f"批量完成 {len(items)} 张图")
                return results, [], ""
            except GeminiCliRateLimitError as exc:
                print(f"限流/容量错误 — {str(exc)[:500]}")
                if attempt < len(retry_delays):
                    delay = self._jittered_delay(retry_delays[attempt])
                    self._sleep_before_retry(
                        delay,
                        f"[INFO] 本批命中限流/容量不足，重试 {attempt + 1}/{len(retry_delays)}",
                        codes=codes,
                        attempt=attempt + 1,
                    )
                    continue
                if self.config.get("stop_on_rate_limit", True) and not self.config.get(
                    "fallback_to_single_on_batch_error", True
                ):
                    return [], codes, "Gemini CLI 命中限流或额度限制"
                break
            except GeminiCliCredentialError as exc:
                reason = str(exc)
                print(f"凭证错误 — {reason}")
                return [], codes, reason
            except Exception as exc:
                print(f"批量失败 — {exc}")
                if _is_transient_error_text(str(exc)) and attempt < len(retry_delays):
                    delay = self._jittered_delay(retry_delays[attempt])
                    self._sleep_before_retry(
                        delay,
                        f"[INFO] 本批瞬时错误，重试 {attempt + 1}/{len(retry_delays)}",
                        codes=codes,
                        attempt=attempt + 1,
                    )
                    continue
                break

        if not self.config.get("fallback_to_single_on_batch_error", True):
            return [], codes, ""

        delay = self.config.get("request_delay", 10)
        if len(items) > 2:
            mid = len(items) // 2
            if delay:
                print(f"[INFO] 批量失败，{delay} 秒后拆分为 {mid}+{len(items) - mid} 继续。")
                time.sleep(delay)
            else:
                print(f"[INFO] 批量失败，拆分为 {mid}+{len(items) - mid} 继续。")
            left_results, left_failed, left_reason = self._review_batch_items(items[:mid], total_candidates)
            if left_reason:
                return left_results, left_failed + self._codes(items[mid:]), left_reason
            right_results, right_failed, right_reason = self._review_batch_items(items[mid:], total_candidates)
            return left_results + right_results, left_failed + right_failed, right_reason

        if delay:
            print(f"[INFO] 小批量仍失败，{delay} 秒后降级为逐只复评。")
            time.sleep(delay)
        else:
            print("[INFO] 小批量仍失败，降级为逐只复评。")
        return self._review_single_items(items, total_candidates)

    def run(self):
        candidates_data = self.load_candidates(Path(self.config["candidates"]))
        pick_date: str = candidates_data["pick_date"]
        candidates: list[dict] = candidates_data["candidates"]
        batch_size = int(self.config.get("batch_size", 1))
        print(f"[INFO] pick_date={pick_date}，候选股票数={len(candidates)}")
        print(
            "[INFO] Gemini CLI 限额："
            f"本次最多 {self.max_requests_per_run if self.max_requests_per_run is not None else '不限'} 次，"
            f"今日剩余 {self.usage.remaining() if self.usage.remaining() is not None else '不限'} 次，"
            f"每批最多 {batch_size} 张图"
        )
        print(
            "[INFO] 上下文预算："
            f"按 {GEMINI_CONTEXT_LIMIT_TOKENS:,} tokens 的 {int(GEMINI_CLI_BATCH_TARGET_RATIO * 100)}% "
            f"估算切批，单批目标不超过 {MAX_CONTEXT_TOKENS:,} tokens"
        )

        out_dir = self.output_dir / pick_date
        out_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = out_dir / "gemini_cli_review_checkpoint.json"
        self._write_checkpoint(status="started", message=f"pick_date={pick_date}, candidates={len(candidates)}")

        all_results: list[dict] = []
        failed_codes: list[str] = []
        stop_reason = ""
        review_batch: list[dict[str, Any]] = []
        review_batch_estimated_tokens = ESTIMATED_PROMPT_TOKEN_RESERVE

        for i, candidate in enumerate(candidates, 1):
            code: str = candidate["code"]
            out_file = out_dir / f"{code}.json"

            if self.config.get("skip_existing", False) and out_file.exists():
                with open(out_file, encoding="utf-8") as f:
                    result = json.load(f)
                if result.get("reviewer") == "gemini-cli":
                    print(f"[{i}/{len(candidates)}] {code} — 已存在，跳过。")
                    all_results.append(result)
                    self._write_checkpoint(status="skip_existing", codes=[code], message="已存在 gemini-cli 结果")
                    continue
                print(f"[{i}/{len(candidates)}] {code} — 已有非 gemini-cli 结果，重新复评。")

            day_chart = self.find_chart_images(pick_date, code)
            if day_chart is None:
                print(f"[{i}/{len(candidates)}] {code} — 缺少日线图，跳过。")
                failed_codes.append(code)
                continue
            try:
                item_estimated_tokens = estimate_image_tokens(day_chart) + ESTIMATED_OUTPUT_TOKENS_PER_STOCK
            except Exception as exc:
                print(f"[{i}/{len(candidates)}] {code} — 图片 token 估算失败，跳过：{exc}")
                failed_codes.append(code)
                continue

            if review_batch and review_batch_estimated_tokens + item_estimated_tokens > MAX_CONTEXT_TOKENS:
                print(
                    f"[INFO] 当前批次估算 {review_batch_estimated_tokens:,} tokens，"
                    f"达到 {int(GEMINI_CLI_BATCH_TARGET_RATIO * 100)}% 上下文预算，提前提交。"
                )
                results, failed, reason = self._review_batch_items(review_batch, len(candidates))
                all_results.extend(results)
                failed_codes.extend(failed)
                review_batch = []
                review_batch_estimated_tokens = ESTIMATED_PROMPT_TOKEN_RESERVE
                if reason:
                    stop_reason = reason
                    break
                ok, next_reason = self._can_start_request()
                if not ok:
                    stop_reason = next_reason
                    print(f"[STOP] {next_reason}")
                    break
                time.sleep(self.config.get("request_delay", 10))

            review_batch.append(
                {
                    "index": i,
                    "code": code,
                    "day_chart": day_chart,
                    "out_file": out_file,
                }
            )
            review_batch_estimated_tokens += item_estimated_tokens
            if len(review_batch) < batch_size:
                continue

            results, failed, reason = self._review_batch_items(review_batch, len(candidates))
            all_results.extend(results)
            failed_codes.extend(failed)
            review_batch = []
            review_batch_estimated_tokens = ESTIMATED_PROMPT_TOKEN_RESERVE
            if reason:
                stop_reason = reason
                break
            ok, next_reason = self._can_start_request()
            if not ok:
                stop_reason = next_reason
                print(f"[STOP] {next_reason}")
                break
            if i < len(candidates):
                time.sleep(self.config.get("request_delay", 10))

        if review_batch and not stop_reason:
            results, failed, reason = self._review_batch_items(review_batch, len(candidates))
            all_results.extend(results)
            failed_codes.extend(failed)
            if reason:
                stop_reason = reason

        print(f"\n[INFO] 评分完成：成功 {len(all_results)} 支，失败/跳过 {len(failed_codes)} 支")
        if failed_codes:
            print(f"[WARN] 未处理股票：{failed_codes}")
        if stop_reason:
            print(f"[WARN] 提前停止：{stop_reason}")
        self._write_checkpoint(
            status="finished",
            codes=failed_codes,
            message=f"success={len(all_results)}, failed_or_skipped={len(failed_codes)}, stop_reason={stop_reason}",
        )

        if not all_results:
            print("[ERROR] 没有可用的评分结果，跳过汇总。")
            return

        min_score = self.config.get("suggest_min_score", 4.0)
        suggestion = self.generate_suggestion(
            pick_date=pick_date,
            all_results=all_results,
            min_score=min_score,
        )
        reviewed_codes = {str(item.get("code")) for item in all_results}
        pending_codes = [code for code in self._iter_pending_codes(candidates) if code not in reviewed_codes]
        suggestion.update(
            {
                "reviewer": "gemini-cli",
                "review_complete": not stop_reason and not pending_codes,
                "total_candidates": len(candidates),
                "failed_or_skipped": failed_codes,
                "pending": pending_codes,
                "stop_reason": stop_reason,
            }
        )

        suggestion_file = out_dir / "suggestion.json"
        with open(suggestion_file, "w", encoding="utf-8") as f:
            json.dump(suggestion, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 汇总推荐已写入: {suggestion_file}")
        print(f"       推荐股票数（score≥{min_score}）: {len(suggestion['recommendations'])}")

        print("\n✅ 全部完成。")
        print(f"   输出目录: {out_dir}")


def main():
    parser = argparse.ArgumentParser(description="Gemini CLI 图表复评")
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG_PATH),
        help="配置文件路径（默认 config/gemini_cli_review.yaml）",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    reviewer = GeminiCliReviewer(config)
    reviewer.run()


if __name__ == "__main__":
    main()
