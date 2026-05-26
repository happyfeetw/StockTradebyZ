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
    ./data/review/{pick_date}/{code}_{strategy}.json
    ./data/review/{pick_date}/suggestion.json
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import random
import re
import signal
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml
from PIL import Image

from base_reviewer import BaseReviewer

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "gemini_cli_review.yaml"
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
DEFAULT_BATCH_SIZE = 5

DEFAULT_CONFIG: dict[str, Any] = {
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review",
    "prompt_path": "agent/prompt.md",
    "gemini_bin": "gemini",
    "model": "gemini-3.1-pro-preview",
    "output_format": "stream-json",
    "timeout_seconds": 900,
    "idle_timeout_seconds": 0,
    "request_delay": 10,
    "batch_size": DEFAULT_BATCH_SIZE,
    "save_raw_cli_io": True,
    "raw_log_dir": "",
    "fallback_to_single_on_batch_error": True,
    "retry_backoff_seconds": [30, 90, 180, 480, 900],
    "retry_jitter_ratio": 0.2,
    "max_requests_per_run": 50,
    "daily_request_budget": 2000,
    "usage_file": "data/review/.gemini_cli_usage.json",
    "stop_on_rate_limit": False,
    "rate_limit_backoff_seconds": 300,
    "skip_existing": True,
    "suggest_min_score": 4.0,
    "classic_pattern_enabled": True,
}

RATE_LIMIT_MARKERS = (
    "rate limit",
    "ratelimit",
    "quota",
    "too many requests",
    "resource exhausted",
    "exceeded",
    "per minute",
    "daily limit",
    "no capacity available",
    "capacity available",
    "resource_exhausted",
)

RATE_LIMIT_PATTERNS = (
    re.compile(r"\b(?:http\s*)?429\b"),
    re.compile(r"\bstatus(?:\s+code)?\s*[:=]?\s*429\b"),
)

TRANSIENT_ERROR_MARKERS = (
    "premature close",
    "err_stream_premature_close",
    "fetchadmincontrols",
    "cloudcode-pa.googleapis.com",
    "gaxioserror",
    "econnreset",
    "socket hang up",
    "etimedout",
    "socket disconnected",
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
    return any(marker in lower for marker in RATE_LIMIT_MARKERS) or any(
        pattern.search(lower) for pattern in RATE_LIMIT_PATTERNS
    )


def _is_credential_error_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in CREDENTIAL_ERROR_MARKERS)


def _is_transient_error_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in TRANSIENT_ERROR_MARKERS) or _is_rate_limit_text(text)


def _transient_error_label(text: str) -> str:
    lower = text.lower()
    if "fetchadmincontrols" in lower or "cloudcode-pa.googleapis.com" in lower:
        return "Gemini CLI 账号策略接口网络异常"
    if "socket hang up" in lower:
        return "网络连接被中断"
    if "premature close" in lower or "err_stream_premature_close" in lower:
        return "流式连接提前关闭"
    if "timeout" in lower or "timed out" in lower:
        return "请求超时"
    return "瞬时网络错误"


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


def _collect_stream_text_payloads(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        payloads: list[str] = []
        for item in value:
            payloads.extend(_collect_stream_text_payloads(item))
        return payloads
    if isinstance(value, dict):
        payloads: list[str] = []
        text_keys = ("response", "text", "content", "delta", "value", "output", "result", "message")
        for key in text_keys:
            if key in value:
                payloads.extend(_collect_stream_text_payloads(value[key]))
        for key, item in value.items():
            if key in text_keys or key in {"type", "role", "index", "id"}:
                continue
            if isinstance(item, (dict, list)):
                payloads.extend(_collect_stream_text_payloads(item))
        return payloads
    return []


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _unwrap_stream_json_output(stdout: str) -> str:
    text = stdout.strip()
    payloads: list[str] = []
    for line in text.splitlines():
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
                payloads.extend(_collect_stream_text_payloads(event.get("content")))
                continue
            if event.get("type") in {"init", "result"}:
                continue
        payloads.extend(_collect_stream_text_payloads(event))

    if not payloads:
        return _unwrap_cli_output(stdout, "json")

    joined = "".join(payloads).strip()
    if joined:
        return joined

    for payload in reversed(payloads):
        if payload.strip():
            return payload.strip()
    raise GeminiCliError(f"无法从 Gemini CLI stream-json 输出中找到模型正文：{text[:500]}")


def _unwrap_cli_output(stdout: str, output_format: str = "json") -> str:
    text = stdout.strip()
    if not text:
        raise GeminiCliError("Gemini CLI 返回空 stdout")

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
    cfg["idle_timeout_seconds"] = _optional_int(cfg.get("idle_timeout_seconds"))
    cfg["rate_limit_backoff_seconds"] = int(cfg.get("rate_limit_backoff_seconds", 300))
    cfg["retry_backoff_seconds"] = _optional_float_list(cfg.get("retry_backoff_seconds", [30, 90, 180, 480, 900]))
    cfg["retry_jitter_ratio"] = float(cfg.get("retry_jitter_ratio", 0.2))
    cfg["request_delay"] = float(cfg.get("request_delay", 10))
    cfg["batch_size"] = int(cfg.get("batch_size", DEFAULT_BATCH_SIZE))
    if cfg["batch_size"] < 1 or cfg["batch_size"] > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size 必须在 1 到 {MAX_BATCH_SIZE} 之间")

    output_format = str(cfg.get("output_format", "json")).strip().lower()
    if output_format not in {"text", "json", "stream-json"}:
        raise ValueError("output_format 只能是 text、json 或 stream-json")
    cfg["output_format"] = output_format
    cfg["save_raw_cli_io"] = bool(cfg.get("save_raw_cli_io", True))
    raw_log_dir = str(cfg.get("raw_log_dir", "") or "").strip()
    cfg["raw_log_dir"] = _resolve_cfg_path(raw_log_dir) if raw_log_dir else None

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
        self.raw_log_root: Path | None = None
        self.cli_call_index = 0
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

    def _iter_pending_codes(self, candidates: Iterable[dict]) -> list[str]:
        return [self.candidate_review_key(candidate) for candidate in candidates if candidate.get("code")]

    def _validate_chart_for_cli(self, source: Path, code: str) -> None:
        image_size = source.stat().st_size
        if image_size > MAX_IMAGE_BYTES:
            raise GeminiCliError(
                f"{code} 图片超过 Gemini CLI 单图大小限制 7MB：{image_size / 1024 / 1024:.2f}MB"
            )

    @staticmethod
    def _chart_ref_for_cli(source: Path, cwd: Path) -> str:
        try:
            rel = source.resolve().relative_to(cwd.resolve())
            return f"@{rel.as_posix()}"
        except ValueError:
            return f"@{source.resolve().as_posix()}"

    @staticmethod
    def _safe_log_name(codes: list[str]) -> str:
        if not codes:
            return "unknown"
        if len(codes) == 1:
            raw = codes[0]
        else:
            raw = f"{codes[0]}-{codes[-1]}_{len(codes)}"
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)[:80]

    def _next_raw_call_dir(self, codes: list[str]) -> Path | None:
        if not self.config.get("save_raw_cli_io", True) or self.raw_log_root is None:
            return None
        self.cli_call_index += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        call_dir = self.raw_log_root / f"{timestamp}_{self.cli_call_index:04d}_{self._safe_log_name(codes)}"
        call_dir.mkdir(parents=True, exist_ok=True)
        return call_dir

    @staticmethod
    def _write_raw_meta(raw_dir: Path | None, payload: dict[str, Any]) -> None:
        if raw_dir is None:
            return
        with open(raw_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _build_prompt(self, *, code: str, chart_ref: str, prompt: str, strategy: str = "") -> str:
        strategy_line = f"来源策略：{strategy}\n" if strategy else ""
        return (
            f"{prompt}\n\n"
            "---\n\n"
            f"股票代码：{code}\n"
            f"{strategy_line}"
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
            if item.get("strategy"):
                lines.append(f"   来源策略：{item['strategy']}")
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

    def _run_cli_command(
        self,
        cmd: list[str],
        env: dict[str, str],
        prompt_text: str,
        *,
        cwd: Path,
        codes: list[str],
        image_paths: list[Path],
    ) -> subprocess.CompletedProcess[str]:
        model = str(self.config.get("model", "") or "").strip() or "(Gemini CLI default)"
        raw_dir = self._next_raw_call_dir(codes)
        print(f"[Command] Gemini CLI 实际命令: {shlex.join(cmd)}")
        print(f"[INFO] Gemini CLI model: {model}")
        print(f"[INFO] Gemini CLI cwd: {cwd}")
        if raw_dir is not None:
            print(f"[INFO] Gemini CLI raw log: {raw_dir}")

        started_at = datetime.now()
        started_monotonic = time.monotonic()
        first_output_after: float | None = None
        last_output_at = started_monotonic
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        timeout_seconds = int(self.config.get("timeout_seconds", 900))
        idle_timeout_seconds = self.config.get("idle_timeout_seconds")
        stderr_preview_count = 0
        output_notice_printed = False

        if raw_dir is not None:
            with open(raw_dir / "prompt.txt", "w", encoding="utf-8") as f:
                f.write(prompt_text)
            self._write_raw_meta(
                raw_dir,
                {
                    "status": "running",
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "command": cmd,
                    "model": model,
                    "cwd": str(cwd),
                    "codes": codes,
                    "image_paths": [str(path) for path in image_paths],
                    "output_format": self.config.get("output_format", "json"),
                    "timeout_seconds": timeout_seconds,
                    "idle_timeout_seconds": idle_timeout_seconds,
                },
            )

        popen_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "env": env,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)
        event_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

        def reader(name: str, pipe: Any) -> None:
            try:
                for line in iter(pipe.readline, ""):
                    event_queue.put((name, line))
            finally:
                try:
                    pipe.close()
                except OSError:
                    pass
                event_queue.put((name, None))

        stdout_thread = threading.Thread(target=reader, args=("stdout", proc.stdout), daemon=True)
        stderr_thread = threading.Thread(target=reader, args=("stderr", proc.stderr), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        assert proc.stdin is not None
        try:
            proc.stdin.write(prompt_text)
            proc.stdin.close()
        except BrokenPipeError:
            pass

        stdout_file = open(raw_dir / "stdout.jsonl", "w", encoding="utf-8") if raw_dir is not None else None
        stderr_file = open(raw_dir / "stderr.log", "w", encoding="utf-8") if raw_dir is not None else None
        timed_out_reason = ""
        ended_streams = 0
        try:
            while ended_streams < 2:
                now = time.monotonic()
                if now - started_monotonic > timeout_seconds:
                    timed_out_reason = f"Gemini CLI 超时（{timeout_seconds} 秒）"
                    break
                if idle_timeout_seconds and now - last_output_at > float(idle_timeout_seconds):
                    timed_out_reason = f"Gemini CLI 空闲超时（{idle_timeout_seconds} 秒无输出）"
                    break

                try:
                    stream_name, line = event_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if line is None:
                    ended_streams += 1
                    continue

                now = time.monotonic()
                last_output_at = now
                if first_output_after is None:
                    first_output_after = round(now - started_monotonic, 3)
                if not output_notice_printed:
                    target = f"，原始输出见 {raw_dir}" if raw_dir is not None else ""
                    print(f"[INFO] Gemini CLI 已收到 {stream_name} 输出{target}")
                    output_notice_printed = True

                if stream_name == "stdout":
                    stdout_parts.append(line)
                    if stdout_file is not None:
                        stdout_file.write(line)
                        stdout_file.flush()
                else:
                    stderr_parts.append(line)
                    if stderr_file is not None:
                        stderr_file.write(line)
                        stderr_file.flush()
                    if stderr_preview_count < 8:
                        print(f"[Gemini STDERR] {line.rstrip()[:500]}")
                        stderr_preview_count += 1
                    elif stderr_preview_count == 8:
                        print("[Gemini STDERR] 后续 stderr 已写入 raw log，主日志不再展开。")
                        stderr_preview_count += 1
        finally:
            if stdout_file is not None:
                stdout_file.close()
            if stderr_file is not None:
                stderr_file.close()

        if timed_out_reason:
            self._send_process_signal(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._send_process_signal(proc, signal.SIGKILL)
                proc.wait(timeout=5)
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)
            detail = f"{stdout}\n{stderr}".strip()
            self._write_raw_meta(
                raw_dir,
                {
                    "status": "timeout",
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "ended_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
                    "command": cmd,
                    "model": model,
                    "cwd": str(cwd),
                    "codes": codes,
                    "image_paths": [str(path) for path in image_paths],
                    "exit_code": proc.returncode,
                    "first_output_after_seconds": first_output_after,
                    "last_output_after_seconds": round(last_output_at - started_monotonic, 3),
                    "timeout_reason": timed_out_reason,
                },
            )
            if _is_credential_error_text(detail):
                raise GeminiCliCredentialError(
                    "Gemini CLI 凭证不可访问：无法打开 ~/.gemini/oauth_creds.json。"
                    "请从普通终端启动 workbench，或让当前运行环境具备 ~/.gemini 读写权限。"
                )
            suffix = f": {detail[:500]}" if detail else ""
            raise GeminiCliError(f"{timed_out_reason}{suffix}")

        proc.wait()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)
        self._write_raw_meta(
            raw_dir,
            {
                "status": "finished",
                "started_at": started_at.isoformat(timespec="seconds"),
                "ended_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
                "command": cmd,
                "model": model,
                "cwd": str(cwd),
                "codes": codes,
                "image_paths": [str(path) for path in image_paths],
                "output_format": self.config.get("output_format", "json"),
                "exit_code": proc.returncode,
                "first_output_after_seconds": first_output_after,
                "last_output_after_seconds": round(last_output_at - started_monotonic, 3),
            },
        )

        return subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
        )

    def _consume_cli_request(
        self,
        cmd: list[str],
        env: dict[str, str],
        prompt_text: str,
        *,
        cwd: Path,
        codes: list[str],
        image_paths: list[Path],
    ) -> subprocess.CompletedProcess[str]:
        ok, reason = self._can_start_request()
        if not ok:
            raise GeminiCliError(reason)
        self.requests_this_run += 1
        self.usage.consume()
        return self._run_cli_command(cmd, env, prompt_text, cwd=cwd, codes=codes, image_paths=image_paths)

    def review_stock(self, code: str, day_chart: Path, prompt: str, strategy: str = "") -> dict:
        self._validate_chart_for_cli(day_chart, code)
        cwd = day_chart.parent
        chart_ref = self._chart_ref_for_cli(day_chart, cwd)
        prompt_text = self._build_prompt(code=code, chart_ref=chart_ref, prompt=prompt, strategy=strategy)
        cmd = self._build_command()
        env = {**os.environ, "NO_COLOR": "1"}

        result = self._consume_cli_request(
            cmd,
            env,
            prompt_text,
            cwd=cwd,
            codes=[code],
            image_paths=[day_chart],
        )

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

        response_text = _unwrap_cli_output(result.stdout, str(self.config.get("output_format", "json")))
        if _is_rate_limit_text(response_text):
            raise GeminiCliRateLimitError(response_text)

        parsed = self.extract_json(response_text)
        parsed["code"] = code
        parsed["reviewer"] = "gemini-cli"
        return parsed

    def review_batch(self, items: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
        prompt_items: list[dict[str, Any]] = []
        image_paths: list[Path] = []
        cwd = Path(items[0]["day_chart"]).parent
        for item in items:
            day_chart = Path(item["day_chart"])
            self._validate_chart_for_cli(day_chart, item["code"])
            chart_ref = self._chart_ref_for_cli(day_chart, cwd)
            image_paths.append(day_chart)
            prompt_items.append({"code": item["code"], "strategy": item.get("strategy") or "", "chart_ref": chart_ref})

        prompt_text = self._build_batch_prompt(items=prompt_items, prompt=prompt)
        cmd = self._build_command()
        env = {**os.environ, "NO_COLOR": "1"}

        result = self._consume_cli_request(
            cmd,
            env,
            prompt_text,
            cwd=cwd,
            codes=[str(item["code"]) for item in items],
            image_paths=image_paths,
        )

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

        response_text = _unwrap_cli_output(result.stdout, str(self.config.get("output_format", "json")))
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
        return [str(item.get("review_key") or item["code"]) for item in items]

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
            review_key = str(item.get("review_key") or code)
            attempt = 0

            while True:
                ok, reason = self._can_start_request()
                if not ok:
                    stop_reason = reason
                    print(f"[STOP] {reason}")
                    break

                print(f"[{item['index']}/{total_candidates}] {review_key} — Gemini CLI 正在分析 ...", end=" ", flush=True)
                try:
                    result = self.review_stock(
                        code=code,
                        day_chart=item["day_chart"],
                        prompt=self.prompt,
                        strategy=str(item.get("strategy") or ""),
                    )
                    result["strategy"] = item.get("strategy") or result.get("strategy", "")
                    result["review_key"] = review_key
                    result = self.normalize_scores(result, self.config)
                    self._write_stock_result(item, result)
                    all_results.append(result)
                    print(f"完成 — {self._format_result_status(result)}")
                    self._write_checkpoint(status="stock_done", codes=[review_key], message=self._format_result_status(result))
                    break
                except GeminiCliRateLimitError as exc:
                    print(f"限流/容量错误 — {str(exc)[:500]}")
                    if attempt < len(retry_delays):
                        delay = self._jittered_delay(retry_delays[attempt])
                        attempt += 1
                        self._sleep_before_retry(
                            delay,
                            f"[INFO] {review_key} 命中限流/容量不足，重试 {attempt}/{len(retry_delays)}",
                            codes=[review_key],
                            attempt=attempt,
                        )
                        continue
                    failed_codes.append(review_key)
                    if self.config.get("stop_on_rate_limit", True):
                        stop_reason = "Gemini CLI 命中限流或额度限制"
                    else:
                        print(f"[WARN] {review_key} 达到限流重试上限，跳过该候选并继续。")
                    break
                except GeminiCliCredentialError as exc:
                    failed_codes.append(review_key)
                    stop_reason = str(exc)
                    print(f"凭证错误 — {stop_reason}")
                    break
                except Exception as exc:
                    message = str(exc)
                    if _is_transient_error_text(message) and attempt < len(retry_delays):
                        delay = self._jittered_delay(retry_delays[attempt])
                        attempt += 1
                        print(f"失败 — {_transient_error_label(message)}: {message[:500]}")
                        self._sleep_before_retry(
                            delay,
                            f"[INFO] {review_key} {_transient_error_label(message)}，重试 {attempt}/{len(retry_delays)}",
                            codes=[review_key],
                            attempt=attempt,
                        )
                        continue
                    print(f"失败 — {exc}")
                    failed_codes.append(review_key)
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
                    result["strategy"] = item.get("strategy") or result.get("strategy", "")
                    result["review_key"] = item.get("review_key") or self.review_key(
                        str(result.get("code") or item["code"]),
                        str(result.get("strategy") or ""),
                    )
                    result = self.normalize_scores(result, self.config)
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
                message = str(exc)
                if _is_transient_error_text(message) and attempt < len(retry_delays):
                    print(f"批量失败 — {_transient_error_label(message)}: {message[:500]}")
                    delay = self._jittered_delay(retry_delays[attempt])
                    self._sleep_before_retry(
                        delay,
                        f"[INFO] 本批{_transient_error_label(message)}，重试 {attempt + 1}/{len(retry_delays)}",
                        codes=codes,
                        attempt=attempt + 1,
                    )
                    continue
                print(f"批量失败 — {exc}")
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
        candidates: list[dict] = self.order_candidates_for_review(candidates_data)
        batch_size = int(self.config.get("batch_size", 1))
        print(f"[INFO] pick_date={pick_date}，候选股票数={len(candidates)}")
        priority = self.review_priority_strategies(candidates_data)
        if priority:
            print(f"[INFO] 复评优先策略: {', '.join(priority)}")
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
        raw_log_dir = self.config.get("raw_log_dir")
        self.raw_log_root = Path(raw_log_dir) if raw_log_dir else out_dir / "gemini_cli_runs"
        if self.config.get("save_raw_cli_io", True):
            self.raw_log_root.mkdir(parents=True, exist_ok=True)
            print(f"[INFO] Gemini CLI raw logs: {self.raw_log_root}")
        else:
            self.raw_log_root = None
        idle_timeout_seconds = self.config.get("idle_timeout_seconds")
        if idle_timeout_seconds:
            print(f"[INFO] Gemini CLI idle timeout: {idle_timeout_seconds} 秒无 stdout/stderr 输出即中止")
        self._write_checkpoint(status="started", message=f"pick_date={pick_date}, candidates={len(candidates)}")

        all_results: list[dict] = []
        failed_codes: list[str] = []
        stop_reason = ""
        review_batch: list[dict[str, Any]] = []
        review_batch_estimated_tokens = ESTIMATED_PROMPT_TOKEN_RESERVE

        for i, candidate in enumerate(candidates, 1):
            code: str = candidate["code"]
            strategy = str(candidate.get("strategy") or "")
            review_key = self.review_key(code, strategy)
            out_file = self.review_file(out_dir, code, strategy)

            if self.config.get("skip_existing", False) and out_file.exists():
                with open(out_file, encoding="utf-8") as f:
                    result = json.load(f)
                if result.get("reviewer") == "gemini-cli":
                    print(f"[{i}/{len(candidates)}] {review_key} — 已存在，跳过。")
                    result = self.normalize_scores(result, self.config)
                    all_results.append(result)
                    self._write_checkpoint(status="skip_existing", codes=[review_key], message="已存在 gemini-cli 结果")
                    continue
                print(f"[{i}/{len(candidates)}] {review_key} — 已有非 gemini-cli 结果，重新复评。")

            day_chart = self.find_chart_images(pick_date, code, strategy)
            if day_chart is None:
                print(f"[{i}/{len(candidates)}] {review_key} — 缺少日线图，跳过。")
                failed_codes.append(review_key)
                continue
            try:
                item_estimated_tokens = estimate_image_tokens(day_chart) + ESTIMATED_OUTPUT_TOKENS_PER_STOCK
            except Exception as exc:
                print(f"[{i}/{len(candidates)}] {review_key} — 图片 token 估算失败，跳过：{exc}")
                failed_codes.append(review_key)
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
                    "strategy": strategy,
                    "review_key": review_key,
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
            candidates=candidates,
        )
        reviewed_codes = {self.result_review_key(item) for item in all_results}
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
        for strategy, counts in sorted(suggestion.get("strategy_counts", {}).items()):
            print(f"       {strategy}: 推荐 {counts.get('recommended', 0)} / 候选 {counts.get('total', 0)}")

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
