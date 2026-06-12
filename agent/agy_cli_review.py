"""
agy_cli_review.py
~~~~~~~~~~~~~~~~~
使用本机 Antigravity CLI（agy）对候选股票进行图表复评。

该路径用于 Google 订阅登录模型复评：
- 支持 per-call --model，模型名称直接使用 agy models 中的名称。
- 默认不走 Gemini CLI。
- 默认输出到 data/review/agy_cli/{pick_date}；多模型流程会按模型 profile 写入隔离目录。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from base_reviewer import BaseReviewer

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "agy_cli_review.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review/agy_cli",
    "prompt_path": "agent/prompt.md",
    "agy_bin": "agy",
    "model": "Gemini 3.5 Flash (High)",
    "print_timeout": "6m",
    "timeout_seconds": 360,
    "stdin_mode": "devnull",
    "dangerously_skip_permissions": False,
    "request_delay": 3,
    "batch_size": 4,
    "max_items": None,
    "skip_existing": True,
    "suggest_min_score": 4.0,
    "save_raw_cli_io": True,
    "raw_log_dir": "",
    "json_repair_enabled": True,
    "json_repair_prompt_max_chars": 12000,
    "fallback_to_single_on_batch_error": True,
    "split_batch_on_cli_timeout": True,
    "stop_on_cli_timeout": True,
    "settings_path": "~/.gemini/antigravity-cli/settings.json",
    "expected_model_label": "",
    "fail_on_model_mismatch": False,
    "classic_pattern_enabled": True,
    "group_review_by_strategy": True,
    "auth_recovery_enabled": True,
    "auth_recovery_wait_seconds": 900,
    "auth_recovery_check_interval": 15,
    "auth_recovery_probe_timeout_seconds": 90,
    "auth_recovery_probe_prompt": "只输出 OK 两个字母，不要解释。",
    "auth_code_file": "",
    "auth_code_wait_seconds": 25,
    "auth_code_poll_interval": 1,
}

AUTH_URL_RE = re.compile(r"https://accounts\.google\.com/\S+")

REQUIRED_TEXT_FIELDS: tuple[str, ...] = (
    "trend_reasoning",
    "position_reasoning",
    "volume_reasoning",
    "abnormal_move_reasoning",
    "signal_reasoning",
    "classic_pattern_type",
    "classic_pattern_reasoning",
    "signal_type",
    "verdict",
    "comment",
)
REQUIRED_SCORE_FIELDS: tuple[str, ...] = (
    "trend_structure",
    "price_position",
    "volume_behavior",
    "previous_abnormal_move",
    "classic_pattern_match",
)
VALID_VERDICTS = {"PASS", "WATCH", "FAIL"}

JSON_OUTPUT_CONTRACT = """
输出契约：
1. 只能输出一个 JSON 对象，必须能被 Python json.loads 直接解析。
2. 不要输出 Markdown 代码块、解释文字、前后缀、注释或多余字段说明。
3. 必须包含 trend_reasoning、position_reasoning、volume_reasoning、abnormal_move_reasoning、signal_reasoning、classic_pattern_type、classic_pattern_reasoning、common_gate、scores、total_score、signal_type、verdict、comment。
4. common_gate.scores 必须包含 trend_qualification、support_stop_loss_control、overhead_room、volume_health、post_entry_discipline，且分数必须是 0 到 5 的数字。
5. scores 必须包含 trend_structure、price_position、volume_behavior、previous_abnormal_move、classic_pattern_match，且分数必须是 0 到 5 的数字。
6. verdict 只能是 PASS、WATCH 或 FAIL。
""".strip()

JSON_SCHEMA_EXAMPLE = """
{
  "trend_reasoning": "string",
  "position_reasoning": "string",
  "volume_reasoning": "string",
  "abnormal_move_reasoning": "string",
  "signal_reasoning": "string",
  "classic_pattern_type": "string",
  "classic_pattern_reasoning": "string",
  "common_gate": {
    "scores": {
      "trend_qualification": 1,
      "support_stop_loss_control": 1,
      "overhead_room": 1,
      "volume_health": 1,
      "post_entry_discipline": 1
    },
    "hard_veto": false,
    "hard_veto_reasons": [],
    "comment": "string"
  },
  "scores": {
    "trend_structure": 1,
    "price_position": 1,
    "volume_behavior": 1,
    "previous_abnormal_move": 1,
    "classic_pattern_match": 1
  },
  "total_score": 1.0,
  "signal_type": "string",
  "verdict": "WATCH",
  "comment": "一句中文交易员点评"
}
""".strip()


class AgyCliError(RuntimeError):
    pass


class AgyCliAuthError(AgyCliError):
    pass


class AgyCliTimeoutError(AgyCliError):
    pass


class AgyCliJsonContractError(AgyCliError):
    pass


def _is_auth_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "authentication required",
            "waiting for authentication",
            "authorization code",
            "authentication timed out",
            "keyringauth: timed out",
            "silent auth failed",
            "auth timed out",
            "print mode: not authenticated",
        )
    )


def _raise_if_auth_error(output: str, *, prefix: str = "AGY CLI 认证失败") -> None:
    if _is_auth_error(output):
        raise AgyCliAuthError(f"{prefix}: {output[:1200]}")


def _is_cli_timeout(output: str) -> bool:
    return "agy cli timed out after" in output.lower()


def _raise_if_cli_timeout(output: str, *, prefix: str = "AGY CLI 超时") -> None:
    if _is_cli_timeout(output):
        raise AgyCliTimeoutError(f"{prefix}: {output[:1200]}")


def _normalize_stdin_mode(value: Any) -> str:
    mode = str(value or "devnull").strip().lower()
    if mode not in {"devnull", "pipe"}:
        raise ValueError("AGY stdin_mode 只能是 devnull 或 pipe")
    return mode


def _resolve_cfg_path(path_like: str | Path, base_dir: Path = _ROOT) -> Path:
    p = Path(path_like).expanduser()
    return p if p.is_absolute() else (base_dir / p)


def _load_yaml_config(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    config = {**DEFAULT_CONFIG, **loaded}
    for key in ("candidates", "kline_dir", "output_dir", "prompt_path"):
        config[key] = _resolve_cfg_path(config[key])
    config["settings_path"] = _resolve_cfg_path(config["settings_path"], base_dir=Path.home())
    config["json_repair_enabled"] = bool(config.get("json_repair_enabled", True))
    config["json_repair_prompt_max_chars"] = int(config.get("json_repair_prompt_max_chars", 12000))
    config["fallback_to_single_on_batch_error"] = bool(config.get("fallback_to_single_on_batch_error", True))
    config["split_batch_on_cli_timeout"] = bool(config.get("split_batch_on_cli_timeout", True))
    config["stop_on_cli_timeout"] = bool(config.get("stop_on_cli_timeout", True))
    config["stdin_mode"] = _normalize_stdin_mode(config.get("stdin_mode", "devnull"))
    config["dangerously_skip_permissions"] = bool(config.get("dangerously_skip_permissions", False))
    config["batch_size"] = max(1, int(config.get("batch_size", 5)))
    config["request_delay"] = float(config.get("request_delay", 10))
    config["auth_recovery_enabled"] = bool(config.get("auth_recovery_enabled", True))
    config["auth_recovery_wait_seconds"] = max(0, int(config.get("auth_recovery_wait_seconds", 900)))
    config["auth_recovery_check_interval"] = max(1.0, float(config.get("auth_recovery_check_interval", 15)))
    config["auth_recovery_probe_timeout_seconds"] = max(1, int(config.get("auth_recovery_probe_timeout_seconds", 90)))
    config["auth_recovery_probe_prompt"] = str(
        config.get("auth_recovery_probe_prompt") or "只输出 OK 两个字母，不要解释。"
    )
    config["auth_code_file"] = str(config.get("auth_code_file") or "")
    config["auth_code_wait_seconds"] = max(0, int(config.get("auth_code_wait_seconds", 25)))
    config["auth_code_poll_interval"] = max(0.2, float(config.get("auth_code_poll_interval", 1)))
    return config


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    payload = _strip_json_fence(text)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("[")
        end = payload.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError(f"未能在 AGY 输出中找到 JSON 数组:\n{payload[:1200]}")
        parsed = json.loads(payload[start:end])

    if isinstance(parsed, dict):
        for key in ("reviews", "results", "items", "stocks"):
            value = parsed.get(key)
            if isinstance(value, list):
                parsed = value
                break

    if not isinstance(parsed, list):
        raise ValueError(f"AGY 输出不是 JSON 数组:\n{payload[:1200]}")
    if not all(isinstance(item, dict) for item in parsed):
        raise ValueError(f"AGY 输出数组中存在非对象元素:\n{payload[:1200]}")
    return parsed


def _read_model_evidence(settings_path: Path, configured_model: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "source": str(settings_path),
        "model": configured_model,
        "available": bool(configured_model),
        "control": "per-call --model",
        "note": "AGY 1.0.5 supports per-call --model; settings model is recorded only as secondary evidence.",
    }
    if not settings_path.exists():
        return evidence
    try:
        parsed = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - model evidence should not block unless configured
        evidence["error"] = str(exc)
        return evidence
    settings_model = parsed.get("model")
    if isinstance(settings_model, str) and settings_model.strip():
        evidence["settings_model"] = settings_model.strip()
    return evidence


class AgyCliReviewer(BaseReviewer):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.agy_bin = str(config.get("agy_bin", "agy"))
        self.model = str(config.get("model") or config.get("expected_model_label") or "").strip()
        self.print_timeout = str(config.get("print_timeout", "10m"))
        self.timeout_seconds = int(config.get("timeout_seconds", 900))
        self.stdin_mode = _normalize_stdin_mode(config.get("stdin_mode", "devnull"))
        self.dangerously_skip_permissions = bool(config.get("dangerously_skip_permissions", False))
        self.cli_call_index = 0
        self.raw_log_root: Path | None = None
        self.model_evidence = _read_model_evidence(Path(config["settings_path"]), self.model)
        self._validate_agy_bin()
        self._validate_model_expectation()

    def _validate_agy_bin(self) -> None:
        if os.sep in self.agy_bin:
            if not Path(self.agy_bin).exists():
                raise FileNotFoundError(f"找不到 agy CLI：{self.agy_bin}")
            return
        if shutil.which(self.agy_bin) is None:
            raise FileNotFoundError(f"找不到 agy CLI：{self.agy_bin}")

    def _validate_model_expectation(self) -> None:
        expected = str(self.config.get("expected_model_label") or "").strip()
        actual = self.model
        if expected and expected != actual:
            message = f"AGY 本次调用模型为 {actual or '默认模型'}，期望为 {expected}"
            if self.config.get("fail_on_model_mismatch", False):
                raise AgyCliError(message)
            print(f"[WARN] {message}；本次继续执行。")

    @staticmethod
    def _safe_log_name(code: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in code)[:80]

    def _next_raw_call_dir(self, code: str) -> Path | None:
        if not self.config.get("save_raw_cli_io", True) or self.raw_log_root is None:
            return None
        self.cli_call_index += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        call_dir = self.raw_log_root / f"{timestamp}_{self.cli_call_index:04d}_{self._safe_log_name(code)}"
        call_dir.mkdir(parents=True, exist_ok=True)
        return call_dir

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        half = max(1, max_chars // 2)
        return (
            text[:half]
            + "\n\n...[TRUNCATED_FOR_JSON_REPAIR]...\n\n"
            + text[-half:]
        )

    @staticmethod
    def _chart_ref_for_agy(source: Path) -> str:
        return f"@{source.resolve().as_posix()}"

    def _build_prompt(self, *, code: str, day_chart: Path, prompt: str, strategy: str = "") -> str:
        prompt = self.prompt_for_strategy(prompt, strategy)
        chart_ref = self._chart_ref_for_agy(day_chart)
        strategy_line = f"候选策略：{strategy}\n" if strategy else ""
        return (
            f"{prompt}\n\n"
            "---\n\n"
            f"股票代码：{code}\n"
            f"{strategy_line}"
            f"日线图：{chart_ref}\n\n"
            "请读取上面的日线图，严格按照评分规则完成复评。"
            f"\n\n{JSON_OUTPUT_CONTRACT}"
        )

    def _build_batch_prompt(self, *, items: list[dict[str, Any]], prompt: str) -> str:
        prompt = self.prompt_for_strategy(prompt, self.batch_strategy(items))
        lines = [
            prompt,
            "",
            "---",
            "",
            f"本批需要复评 {len(items)} 支股票。每张日线图与股票代码严格一一对应：",
        ]
        for index, item in enumerate(items, 1):
            lines.append(f"{index}. 股票代码：{item['code']}")
            if item.get("strategy"):
                lines.append(f"   候选策略：{item['strategy']}")
            lines.append(f"   日线图：{self._chart_ref_for_agy(Path(item['day_chart']))}")
        lines.extend(
            [
                "",
                "请分别读取每张日线图，严格按照评分规则逐只完成复评。",
                "本批输出格式覆盖上方单股输出格式：只能输出一个 JSON 数组，不要输出 Markdown、解释文字或额外字段。",
                "数组长度必须等于本批股票数量，顺序必须与上方股票列表一致。",
                "数组中每个对象必须包含 code 字段，并保留单股输出格式里的所有字段。",
                "",
                JSON_OUTPUT_CONTRACT.replace("只能输出一个 JSON 对象", "数组中每个元素都是一个 JSON 对象"),
            ]
        )
        return "\n".join(lines)

    def _build_command(self, day_chart: Path, prompt_text: str) -> list[str]:
        cmd = [self.agy_bin]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(
            [
                "--add-dir",
                str(day_chart.parent.resolve()),
                "--print-timeout",
                self.print_timeout,
                "--print",
                prompt_text,
            ]
        )
        return cmd

    def _build_auth_probe_command(self) -> list[str]:
        cmd = [self.agy_bin]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(
            [
                "--print-timeout",
                "1m",
                "--print",
                str(self.config.get("auth_recovery_probe_prompt") or "只输出 OK 两个字母，不要解释。"),
            ]
        )
        return cmd

    @staticmethod
    def _extract_auth_url(text: str) -> str:
        match = AUTH_URL_RE.search(text or "")
        return match.group(0) if match else ""

    def _auth_recovery_status_path(self) -> Path | None:
        if self.raw_log_root is None:
            return None
        return self.raw_log_root / "auth_recovery_status.json"

    def _write_auth_recovery_status(self, payload: dict[str, Any]) -> None:
        status_path = self._auth_recovery_status_path()
        if status_path is None:
            return
        try:
            status_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json(status_path, payload)
        except Exception as exc:  # noqa: BLE001 - status file is best-effort observability.
            print(f"[WARN] 写入 AGY 认证恢复状态失败：{exc}")

    def _auth_code_path(self, raw_dir: Path | None = None) -> Path | None:
        configured = str(self.config.get("auth_code_file") or "").strip()
        if configured:
            return _resolve_cfg_path(configured)
        if self.raw_log_root is not None:
            return self.raw_log_root / "auth_code.txt"
        if raw_dir is not None:
            return raw_dir / "auth_code.txt"
        return None

    @staticmethod
    def _read_and_remove_auth_code(code_path: Path) -> str:
        code = code_path.read_text(encoding="utf-8").strip()
        try:
            code_path.unlink()
        except FileNotFoundError:
            pass
        return code

    def _wait_for_live_auth_code(
        self,
        proc: subprocess.Popen[str],
        *,
        context: str,
        auth_url: str,
        raw_dir: Path | None,
    ) -> bool:
        code_path = self._auth_code_path(raw_dir)
        wait_seconds = int(self.config.get("auth_code_wait_seconds", 25))
        if code_path is None or wait_seconds <= 0:
            return False

        code_path.parent.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now()
        deadline_monotonic = time.monotonic() + wait_seconds
        status_payload = {
            "status": "needs_code",
            "context": context,
            "started_at": started_at.isoformat(timespec="seconds"),
            "deadline_at": datetime.fromtimestamp(time.time() + wait_seconds).isoformat(timespec="seconds"),
            "wait_seconds": wait_seconds,
            "model": self.model,
            "auth_url": auth_url,
            "auth_code_file": str(code_path),
        }
        self._write_auth_recovery_status(status_payload)
        print(f"[WARN] AGY 正在等待授权码；如浏览器返回 code，请写入: {code_path}")

        interval = float(self.config.get("auth_code_poll_interval", 1))
        while proc.poll() is None and time.monotonic() < deadline_monotonic:
            if code_path.exists():
                code = self._read_and_remove_auth_code(code_path)
                if code and proc.stdin is not None:
                    proc.stdin.write(code + "\n")
                    proc.stdin.flush()
                    sent_at = datetime.now().isoformat(timespec="seconds")
                    self._write_auth_recovery_status(
                        {
                            **status_payload,
                            "status": "code_sent",
                            "code_sent_at": sent_at,
                        }
                    )
                    print("[INFO] 已将授权码写入 AGY 子进程 stdin，等待认证结果。")
                    return True
            time.sleep(interval)

        self._write_auth_recovery_status(
            {
                **status_payload,
                "status": "code_wait_timeout",
                "ended_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        return False

    def _probe_auth_recovered(self) -> tuple[bool, str]:
        cmd = self._build_auth_probe_command()
        timeout = int(self.config.get("auth_recovery_probe_timeout_seconds", 90))
        try:
            result = subprocess.run(
                cmd,
                cwd=str(_ROOT),
                text=True,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            output = f"{exc.stdout or ''}\n{exc.stderr or ''}".strip()
            detail = f"AGY 认证探测超时（{timeout}s）"
            return False, f"{detail}: {output[:800]}" if output else detail
        except Exception as exc:  # noqa: BLE001 - caller decides whether to keep waiting.
            return False, f"AGY 认证探测失败：{exc}"

        combined_output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode == 0 and not _is_auth_error(combined_output):
            return True, (combined_output or "probe ok")[:800]
        detail = combined_output or f"AGY 认证探测退出码 {result.returncode}"
        return False, detail[:1200]

    def _wait_for_auth_recovery(self, exc: AgyCliAuthError, *, context: str) -> bool:
        if not self.config.get("auth_recovery_enabled", True):
            return False
        wait_seconds = int(self.config.get("auth_recovery_wait_seconds", 900))
        if wait_seconds <= 0:
            return False

        interval = float(self.config.get("auth_recovery_check_interval", 15))
        started_at = datetime.now()
        deadline_monotonic = time.monotonic() + wait_seconds
        deadline_at = datetime.fromtimestamp(time.time() + wait_seconds)
        auth_url = self._extract_auth_url(str(exc))
        base_status = {
            "status": "waiting",
            "context": context,
            "started_at": started_at.isoformat(timespec="seconds"),
            "deadline_at": deadline_at.isoformat(timespec="seconds"),
            "wait_seconds": wait_seconds,
            "model": self.model,
            "auth_url": auth_url,
            "reason": str(exc)[:1200],
        }
        self._write_auth_recovery_status(base_status)

        print(f"[WARN] AGY 需要重新认证，暂停当前 {context}，最多等待 {wait_seconds}s 后自动探测恢复。")
        if auth_url:
            print(f"[WARN] 认证链接: {auth_url}")
        print("[WARN] 请在浏览器完成 AGY/Google 登录；完成后本进程会自动继续同一模型重试。")

        probe_index = 0
        while True:
            probe_index += 1
            ok, detail = self._probe_auth_recovered()
            now = datetime.now()
            status_payload = {
                **base_status,
                "probe_index": probe_index,
                "last_probe_at": now.isoformat(timespec="seconds"),
                "last_probe_result": "ok" if ok else "waiting",
                "last_probe_detail": detail[:1200],
            }
            if ok:
                status_payload["status"] = "recovered"
                status_payload["recovered_at"] = now.isoformat(timespec="seconds")
                self._write_auth_recovery_status(status_payload)
                print("[INFO] AGY 认证已恢复，继续重试当前批次。")
                return True

            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                status_payload["status"] = "timeout"
                self._write_auth_recovery_status(status_payload)
                print(f"[ERROR] AGY 认证恢复等待超时，最后一次探测：{detail[:500]}")
                return False

            self._write_auth_recovery_status(status_payload)
            print(f"[INFO] AGY 认证尚未恢复，{min(interval, remaining):.0f}s 后再次探测。")
            time.sleep(min(interval, remaining))

    def _run_agy(
        self,
        *,
        code: str,
        day_chart: Path,
        prompt_text: str,
        purpose: str = "review",
    ) -> subprocess.CompletedProcess[str]:
        raw_dir = self._next_raw_call_dir(f"{code}_{purpose}")
        cmd = self._build_command(day_chart, prompt_text)
        started_at = datetime.now()
        started_monotonic = time.monotonic()

        if raw_dir is not None:
            self._write_text(raw_dir / "prompt.txt", prompt_text)
            self._write_json(
                raw_dir / "meta.json",
                {
                    "status": "running",
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "command": cmd,
                    "code": code,
                    "purpose": purpose,
                    "image_path": str(day_chart),
                    "print_timeout": self.print_timeout,
                    "timeout_seconds": self.timeout_seconds,
                    "stdin_mode": self.stdin_mode,
                    "dangerously_skip_permissions": self.dangerously_skip_permissions,
                    "model": self.model,
                    "model_evidence": self.model_evidence,
                },
            )

        model_part = f" --model {self.model!r}" if self.model else ""
        skip_permissions_part = " --dangerously-skip-permissions" if self.dangerously_skip_permissions else ""
        print(
            f"[Command] AGY CLI 实际命令: {self.agy_bin}{model_part}{skip_permissions_part} "
            f"--add-dir {day_chart.parent} --print-timeout {self.print_timeout} --print <prompt>"
        )
        print(f"[INFO] AGY model: {self.model or '(agy default)'}")
        if raw_dir is not None:
            print(f"[INFO] AGY CLI raw log: {raw_dir}")

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        output_lock = threading.Lock()
        auth_lock = threading.Lock()
        auth_code_handled = False

        def maybe_handle_auth_line(line: str, proc: subprocess.Popen[str], raw_call_dir: Path | None) -> None:
            nonlocal auth_code_handled
            if not (AUTH_URL_RE.search(line) or _is_auth_error(line)):
                return
            stripped = line.strip()
            if stripped:
                print(f"[AGY AUTH] {stripped}")

            with output_lock:
                combined_seen = "".join(stdout_chunks) + "\n" + "".join(stderr_chunks)
            auth_url = self._extract_auth_url(combined_seen)
            should_wait_for_code = bool(auth_url) or "authorization code" in line.lower()
            if not should_wait_for_code:
                return

            with auth_lock:
                if auth_code_handled:
                    return
                auth_code_handled = True
            if proc.stdin is None:
                self._write_auth_recovery_status(
                    {
                        "status": "needs_code_no_stdin",
                        "context": purpose,
                        "started_at": datetime.now().isoformat(timespec="seconds"),
                        "model": self.model,
                        "auth_url": auth_url,
                        "stdin_mode": self.stdin_mode,
                    }
                )
                print(
                    "[WARN] AGY 请求授权码，但当前 stdin_mode=devnull；"
                    "请完成登录后等待恢复探测重试，或临时改为 stdin_mode: pipe。"
                )
                return
            self._wait_for_live_auth_code(
                proc,
                context=purpose,
                auth_url=auth_url,
                raw_dir=raw_call_dir,
            )

        def read_stream(
            stream: Any,
            chunks: list[str],
            proc: subprocess.Popen[str],
            raw_call_dir: Path | None,
        ) -> None:
            try:
                for line in stream:
                    with output_lock:
                        chunks.append(line)
                    maybe_handle_auth_line(line, proc, raw_call_dir)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        with tempfile.TemporaryDirectory(prefix="stocktradebyz-agy-review-") as tmp:
            stdin_target = subprocess.PIPE if self.stdin_mode == "pipe" else subprocess.DEVNULL
            proc = subprocess.Popen(
                cmd,
                cwd=tmp,
                text=True,
                stdin=stdin_target,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                env={**os.environ, "NO_COLOR": "1"},
                start_new_session=True,
            )

            def terminate_agy_process(sig: int) -> None:
                if proc.poll() is not None:
                    return
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    return
                except Exception:
                    try:
                        proc.send_signal(sig)
                    except ProcessLookupError:
                        return

            previous_signal_handlers: dict[int, Any] = {}

            def handle_parent_signal(signum: int, _frame: Any) -> None:
                stderr_chunks.append(f"\nAGY CLI interrupted by signal {signum}\n")
                terminate_agy_process(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    terminate_agy_process(signal.SIGKILL)
                    proc.wait(timeout=3)
                raise SystemExit(128 + signum)

            if threading.current_thread() is threading.main_thread():
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous_signal_handlers[sig] = signal.getsignal(sig)
                    signal.signal(sig, handle_parent_signal)

            threads = [
                threading.Thread(target=read_stream, args=(proc.stdout, stdout_chunks, proc, raw_dir), daemon=True),
                threading.Thread(target=read_stream, args=(proc.stderr, stderr_chunks, proc, raw_dir), daemon=True),
            ]
            for thread in threads:
                thread.start()
            try:
                return_code = proc.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                terminate_agy_process(signal.SIGKILL)
                return_code = proc.wait()
                stderr_chunks.append(f"\nAGY CLI timed out after {self.timeout_seconds}s\n")
            finally:
                for sig, previous_handler in previous_signal_handlers.items():
                    signal.signal(sig, previous_handler)
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                for thread in threads:
                    thread.join(timeout=2)
            result = subprocess.CompletedProcess(
                args=cmd,
                returncode=int(return_code),
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
            )

        if raw_dir is not None:
            self._write_text(raw_dir / "stdout.txt", result.stdout or "")
            self._write_text(raw_dir / "stderr.txt", result.stderr or "")
            self._write_json(
                raw_dir / "meta.json",
                {
                    "status": "finished",
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "ended_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
                    "command": cmd,
                    "code": code,
                    "purpose": purpose,
                    "image_path": str(day_chart),
                    "exit_code": result.returncode,
                    "model": self.model,
                    "print_timeout": self.print_timeout,
                    "timeout_seconds": self.timeout_seconds,
                    "stdin_mode": self.stdin_mode,
                    "dangerously_skip_permissions": self.dangerously_skip_permissions,
                    "model_evidence": self.model_evidence,
                },
            )
        return result

    @staticmethod
    def _numeric_score(value: Any) -> float | None:
        return BaseReviewer._numeric_score(value)

    def _validate_review_payload(self, payload: dict[str, Any], *, code: str, strategy: str) -> None:
        errors: list[str] = []
        for field in REQUIRED_TEXT_FIELDS:
            value = payload.get(field)
            if not isinstance(value, str):
                errors.append(f"{field} 缺失或不是字符串")

        scores = payload.get("scores")
        if not isinstance(scores, dict):
            errors.append("scores 缺失或不是对象")
        else:
            for field in REQUIRED_SCORE_FIELDS:
                if field not in scores:
                    errors.append(f"scores.{field} 缺失")
                    continue
                if self._numeric_score(scores.get(field)) is None:
                    errors.append(f"scores.{field} 不是 0 到 5 的数字")

        if self._numeric_score(payload.get("total_score")) is None:
            errors.append("total_score 缺失或不是 0 到 5 的数字")

        verdict = str(payload.get("verdict") or "").strip().upper()
        if verdict not in VALID_VERDICTS:
            errors.append("verdict 必须是 PASS、WATCH 或 FAIL")

        payload_code = str(payload.get("code") or "").strip()
        if payload_code and payload_code != code:
            errors.append(f"code 不匹配：输出 {payload_code}，期望 {code}")

        payload_strategy = str(payload.get("strategy") or "").strip()
        if payload_strategy and strategy and payload_strategy != strategy:
            errors.append(f"strategy 不匹配：输出 {payload_strategy}，期望 {strategy}")

        if errors:
            raise AgyCliJsonContractError("; ".join(errors))

    def _parse_review_payload(self, text: str, *, code: str, strategy: str) -> dict[str, Any]:
        try:
            parsed = self.extract_json(text)
        except Exception as exc:  # noqa: BLE001 - turn parser detail into reviewer contract error
            raise AgyCliJsonContractError(f"无法从 AGY 输出提取合法 JSON：{exc}") from exc
        self._validate_review_payload(parsed, code=code, strategy=strategy)
        return parsed

    def _build_json_repair_prompt(
        self,
        *,
        code: str,
        strategy: str,
        original_output: str,
        error: Exception,
    ) -> str:
        max_chars = int(self.config.get("json_repair_prompt_max_chars", 12000))
        clipped_output = self._truncate_text(original_output, max_chars)
        strategy_line = f"候选策略：{strategy}\n" if strategy else ""
        return (
            "你上一次输出没有通过本地 JSON 契约校验。"
            "不要重新分析图表，不要改变已有交易判断；只把上一段输出修复为一个合法 JSON 对象。\n\n"
            f"股票代码：{code}\n"
            f"{strategy_line}"
            f"校验错误：{error}\n\n"
            f"{JSON_OUTPUT_CONTRACT}\n\n"
            "必须使用以下 JSON 形状，保留原输出中已有的判断和分数；"
            "如果某个必需字段确实缺失，用保守中文说明补齐，不要添加解释文字。\n\n"
            f"{JSON_SCHEMA_EXAMPLE}\n\n"
            "上一段原始输出如下：\n"
            "```text\n"
            f"{clipped_output}\n"
            "```"
        )

    def review_stock(self, code: str, day_chart: Path, prompt: str, strategy: str = "") -> dict:
        prompt_text = self._build_prompt(
            code=code,
            day_chart=day_chart,
            prompt=prompt,
            strategy=strategy,
        )
        result = self._run_agy(code=code, day_chart=day_chart, prompt_text=prompt_text, purpose="review")
        combined_output = f"{result.stdout}\n{result.stderr}".strip()
        _raise_if_auth_error(combined_output)
        _raise_if_cli_timeout(combined_output)
        if result.returncode != 0:
            raise AgyCliError(f"AGY CLI 退出码 {result.returncode}: {combined_output[:1200]}")

        repair_attempted = False
        repair_used = False
        repair_reason = ""
        try:
            parsed = self._parse_review_payload(result.stdout, code=code, strategy=strategy)
        except AgyCliJsonContractError as exc:
            repair_reason = str(exc)
            if not self.config.get("json_repair_enabled", True):
                raise
            repair_attempted = True
            repair_prompt = self._build_json_repair_prompt(
                code=code,
                strategy=strategy,
                original_output=result.stdout,
                error=exc,
            )
            repair_result = self._run_agy(
                code=code,
                day_chart=day_chart,
                prompt_text=repair_prompt,
                purpose="json_repair",
            )
            repair_output = f"{repair_result.stdout}\n{repair_result.stderr}".strip()
            _raise_if_auth_error(repair_output, prefix="AGY JSON 修复调用认证失败")
            _raise_if_cli_timeout(repair_output, prefix="AGY JSON 修复调用超时")
            if repair_result.returncode != 0:
                raise AgyCliError(f"AGY JSON 修复调用退出码 {repair_result.returncode}: {repair_output[:1200]}")
            try:
                parsed = self._parse_review_payload(repair_result.stdout, code=code, strategy=strategy)
            except AgyCliJsonContractError as repair_exc:
                raise AgyCliError(
                    "AGY 输出 JSON 修复失败："
                    f"初次错误：{repair_reason}；修复后错误：{repair_exc}"
                ) from repair_exc
            repair_used = True

        parsed["code"] = code
        parsed["strategy"] = strategy or parsed.get("strategy", "")
        parsed["reviewer"] = "agy-cli"
        parsed["model"] = self.model
        parsed["model_evidence"] = self.model_evidence
        parsed["json_output_mode"] = "prompt-json"
        parsed["json_schema_valid"] = True
        parsed["json_repair_attempted"] = repair_attempted
        parsed["json_repair_used"] = repair_used
        if repair_reason:
            parsed["json_repair_reason"] = repair_reason[:500]
        return parsed

    def review_batch(self, items: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
        if len(items) == 1:
            item = items[0]
            return [
                self.review_stock(
                    code=str(item["code"]),
                    day_chart=Path(item["day_chart"]),
                    prompt=prompt,
                    strategy=str(item.get("strategy") or ""),
                )
            ]

        prompt_text = self._build_batch_prompt(items=items, prompt=prompt)
        result = self._run_agy(
            code="-".join(str(item["code"]) for item in items),
            day_chart=Path(items[0]["day_chart"]),
            prompt_text=prompt_text,
            purpose=f"batch_{len(items)}",
        )
        combined_output = f"{result.stdout}\n{result.stderr}".strip()
        _raise_if_auth_error(combined_output, prefix="AGY CLI 批量调用认证失败")
        _raise_if_cli_timeout(combined_output, prefix="AGY CLI 批量调用超时")
        if result.returncode != 0:
            raise AgyCliError(f"AGY CLI 批量调用退出码 {result.returncode}: {combined_output[:1200]}")

        try:
            parsed_items = _extract_json_array(result.stdout)
        except Exception as exc:  # noqa: BLE001 - caller may split and fallback to single.
            raise AgyCliJsonContractError(f"AGY 批量输出无法解析为 JSON 数组：{exc}") from exc
        if len(parsed_items) != len(items):
            raise AgyCliJsonContractError(f"AGY 批量返回数量不匹配：期望 {len(items)}，实际 {len(parsed_items)}")

        results: list[dict[str, Any]] = []
        for item, parsed in zip(items, parsed_items):
            code = str(item["code"])
            strategy = str(item.get("strategy") or "")
            self._validate_review_payload(parsed, code=code, strategy=strategy)
            parsed["code"] = code
            parsed["strategy"] = strategy or parsed.get("strategy", "")
            parsed["reviewer"] = "agy-cli"
            parsed["model"] = self.model
            parsed["model_evidence"] = self.model_evidence
            parsed["json_output_mode"] = "prompt-json-array"
            parsed["json_schema_valid"] = True
            parsed["json_repair_attempted"] = False
            parsed["json_repair_used"] = False
            results.append(parsed)
        return results

    @staticmethod
    def _codes(items: list[dict[str, Any]]) -> list[str]:
        return [str(item.get("review_key") or item["code"]) for item in items]

    @staticmethod
    def _format_result_status(result: dict[str, Any]) -> str:
        return f"verdict={result.get('verdict', '?')}, score={result.get('total_score', '?')}"

    def _write_stock_result(self, item: dict[str, Any], result: dict[str, Any]) -> None:
        self._write_json(Path(item["out_file"]), result)

    def _split_batch_items(
        self,
        items: list[dict[str, Any]],
        total_candidates: int,
        *,
        reason: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        delay = float(self.config.get("request_delay", 10))
        if len(items) > 2:
            mid = len(items) // 2
            print(f"[INFO] AGY {reason}，拆分为 {mid}+{len(items) - mid} 继续。")
            if delay:
                time.sleep(delay)
            left_results, left_failed = self._review_batch_items(items[:mid], total_candidates)
            if delay:
                time.sleep(delay)
            right_results, right_failed = self._review_batch_items(items[mid:], total_candidates)
            return left_results + right_results, left_failed + right_failed

        print(f"[INFO] AGY {reason}，使用同一模型逐只复评。")
        return self._review_single_items(items, total_candidates)

    def _review_batch_items(
        self,
        items: list[dict[str, Any]],
        total_candidates: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if not items:
            return [], []
        if len(items) == 1 or int(self.config.get("batch_size", 1)) == 1:
            return self._review_single_items(items, total_candidates)

        start_index = items[0]["index"]
        end_index = items[-1]["index"]
        codes = self._codes(items)
        print(
            f"[{start_index}-{end_index}/{total_candidates}] "
            f"{','.join(codes)} — AGY 批量分析 {len(items)} 张图 ...",
            end=" ",
            flush=True,
        )
        auth_recovered = False
        while True:
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
                return results, []
            except AgyCliAuthError as exc:
                print(f"认证失败 — {exc}")
                if auth_recovered or not self._wait_for_auth_recovery(exc, context=f"批次 {start_index}-{end_index}"):
                    raise
                auth_recovered = True
                print(f"[INFO] AGY 认证恢复后重试批次 {start_index}-{end_index}。")
                continue
            except AgyCliTimeoutError as exc:
                print(f"超时 — {exc}")
                if (
                    self.config.get("split_batch_on_cli_timeout", True)
                    and self.config.get("fallback_to_single_on_batch_error", True)
                    and len(items) > 1
                ):
                    return self._split_batch_items(items, total_candidates, reason="批量超时")
                if self.config.get("stop_on_cli_timeout", True):
                    raise
                return [], codes
            except Exception as exc:  # noqa: BLE001 - fallback below decides how to continue.
                print(f"批量失败 — {exc}")
                break

        if not self.config.get("fallback_to_single_on_batch_error", True):
            return [], codes

        return self._split_batch_items(items, total_candidates, reason="批量失败")

    def _review_single_items(
        self,
        items: list[dict[str, Any]],
        total_candidates: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        all_results: list[dict[str, Any]] = []
        failed_codes: list[str] = []
        for offset, item in enumerate(items):
            code = str(item["code"])
            strategy = str(item.get("strategy") or "")
            review_key = str(item.get("review_key") or self.review_key(code, strategy))
            print(f"[{item['index']}/{total_candidates}] {review_key} — AGY 正在分析 ...", end=" ", flush=True)
            auth_recovered = False
            while True:
                try:
                    result = self.review_stock(
                        code=code,
                        day_chart=Path(item["day_chart"]),
                        prompt=self.prompt,
                        strategy=strategy,
                    )
                    result["strategy"] = strategy or result.get("strategy", "")
                    result["review_key"] = review_key
                    result = self.normalize_scores(result, self.config)
                    self._write_stock_result(item, result)
                    all_results.append(result)
                    print(f"完成 — {self._format_result_status(result)}")
                    break
                except AgyCliAuthError as exc:
                    print(f"认证失败 — {exc}")
                    if auth_recovered or not self._wait_for_auth_recovery(exc, context=f"单股 {review_key}"):
                        raise
                    auth_recovered = True
                    print(f"[INFO] AGY 认证恢复后重试 {review_key}。")
                    continue
                except AgyCliTimeoutError as exc:
                    print(f"超时 — {exc}")
                    if self.config.get("stop_on_cli_timeout", True):
                        raise
                    failed_codes.append(review_key)
                    break
                except Exception as exc:  # noqa: BLE001 - collect failed code and continue
                    print(f"失败 — {exc}")
                    failed_codes.append(review_key)
                    break

            if offset < len(items) - 1:
                time.sleep(float(self.config.get("request_delay", 10)))

        return all_results, failed_codes

    def run(self) -> None:
        candidates_data = self.load_candidates(Path(self.config["candidates"]))
        pick_date: str = candidates_data["pick_date"]
        candidates: list[dict[str, Any]] = self.order_candidates_for_review(candidates_data)
        max_items = self.config.get("max_items")
        if max_items is not None:
            candidates = candidates[: int(max_items)]
        batch_size = int(self.config.get("batch_size", 5))
        print(f"[INFO] pick_date={pick_date}，AGY 复评股票数={len(candidates)}，batch_size={batch_size}")

        out_dir = self.output_dir / pick_date
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_log_dir = str(self.config.get("raw_log_dir") or "").strip()
        self.raw_log_root = Path(raw_log_dir) if raw_log_dir else out_dir / "agy_cli_runs"
        if self.config.get("save_raw_cli_io", True):
            self.raw_log_root.mkdir(parents=True, exist_ok=True)
            print(f"[INFO] AGY CLI raw logs: {self.raw_log_root}")

        all_results: list[dict[str, Any]] = []
        failed_codes: list[str] = []
        review_batch: list[dict[str, Any]] = []
        for i, candidate in enumerate(candidates, 1):
            code = str(candidate["code"])
            strategy = str(candidate.get("strategy") or "")
            review_key = self.review_key(code, strategy)
            out_file = self.review_file(out_dir, code, strategy)
            if self.config.get("skip_existing", False) and out_file.exists():
                print(f"[{i}/{len(candidates)}] {review_key} — 已存在，跳过。")
                with open(out_file, encoding="utf-8") as f:
                    result = json.load(f)
                result = self.normalize_scores(result, self.config)
                all_results.append(result)
                continue

            day_chart = self.find_chart_images(pick_date, code)
            if day_chart is None:
                print(f"[{i}/{len(candidates)}] {review_key} — 缺少日线图，跳过。")
                failed_codes.append(review_key)
                continue

            if review_batch and self.batch_strategy(review_batch) != strategy:
                results, failed = self._review_batch_items(review_batch, len(candidates))
                all_results.extend(results)
                failed_codes.extend(failed)
                review_batch = []
                if i < len(candidates):
                    time.sleep(float(self.config.get("request_delay", 10)))

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
            if len(review_batch) < batch_size:
                continue

            results, failed = self._review_batch_items(review_batch, len(candidates))
            all_results.extend(results)
            failed_codes.extend(failed)
            review_batch = []
            if i < len(candidates):
                time.sleep(float(self.config.get("request_delay", 10)))

        if review_batch:
            results, failed = self._review_batch_items(review_batch, len(candidates))
            all_results.extend(results)
            failed_codes.extend(failed)

        print(f"\n[INFO] AGY 复评完成：成功 {len(all_results)} 支，失败/跳过 {len(failed_codes)} 支")
        if failed_codes:
            print(f"[WARN] 未处理股票：{failed_codes}")
        if not all_results:
            print("[ERROR] 没有可用的 AGY 复评结果，跳过汇总。")
            raise SystemExit(1)

        suggestion = self.generate_suggestion(
            pick_date=pick_date,
            all_results=all_results,
            min_score=float(self.config.get("suggest_min_score", 4.0)),
            candidates=candidates,
        )
        suggestion["reviewer"] = "agy-cli"
        suggestion["model"] = self.model
        suggestion["model_evidence"] = self.model_evidence
        suggestion["review_complete"] = not failed_codes
        suggestion["pending"] = failed_codes
        suggestion_file = out_dir / "suggestion.json"
        self._write_json(suggestion_file, suggestion)
        print(f"[INFO] AGY 汇总已写入: {suggestion_file}")
        if not suggestion["review_complete"]:
            raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="AGY CLI 图表复评")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--limit", type=int, default=None, help="覆盖配置中的 max_items")
    parser.add_argument("--candidates", default="", help="覆盖候选列表 JSON")
    parser.add_argument("--kline-dir", default="", help="覆盖 K 线图目录")
    parser.add_argument("--output-dir", default="", help="覆盖输出目录")
    parser.add_argument("--model", default="", help="覆盖配置中的 agy --model")
    args = parser.parse_args()

    config = _load_yaml_config(Path(args.config))
    if args.limit is not None:
        config["max_items"] = args.limit
    if args.candidates:
        config["candidates"] = _resolve_cfg_path(args.candidates)
    if args.kline_dir:
        config["kline_dir"] = _resolve_cfg_path(args.kline_dir)
    if args.output_dir:
        config["output_dir"] = _resolve_cfg_path(args.output_dir)
    if args.model:
        config["model"] = args.model

    reviewer = AgyCliReviewer(config)
    try:
        reviewer.run()
    except AgyCliError as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
