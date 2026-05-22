#!/usr/bin/env python3
"""
Probe Antigravity CLI capabilities for the K-line review migration.

The probe is intentionally read-mostly: it inspects the local `agy` binary and
runs a minimal non-interactive JSON prompt from a temporary directory so AGY does
not leave runtime files in the repository checkout.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_DIR = ROOT / "data" / "review" / "agy_cli_probe"
DEFAULT_SETTINGS_PATH = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
DEFAULT_LOG_DIR = Path.home() / ".gemini" / "antigravity-cli" / "log"
JSON_PROBE_PROMPT = 'Return exactly {"ok":true,"runner":"agy"} and nothing else.'
IMAGE_PROBE_PROMPT = """Read this stock K-line chart image: @{image_path}

Return only JSON with these fields:
{{"ok":true,"code":"{code}","can_read_chart":true,"observations":["one visual fact"]}}
"""
SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|credential|password|oauth|cookie|session|key)",
    re.IGNORECASE,
)
AUTH_MARKER_RE = re.compile(
    r"(authentication required|waiting for authentication|authorization code|authentication timed out|auth timed out)",
    re.IGNORECASE,
)
AUTH_URL_RE = re.compile(r"https://accounts\.google\.com/\S+")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
AUTH_LOG_MARKER_RE = re.compile(
    r"("
    r"keyringAuth: timed out|"
    r"authenticated via keyring|"
    r"failed to persist token|"
    r"Print mode: not authenticated|"
    r"Print mode: silent auth failed|"
    r"Print mode: silent auth succeeded|"
    r"Print mode: auth timed out|"
    r"token exchange failed|"
    r"Invalid code verifier"
    r")",
    re.IGNORECASE,
)


def _utc_timestamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _resolve_binary(binary: str) -> str:
    if os.sep in binary:
        path = Path(binary).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"找不到 agy CLI: {path}")
        return str(path)
    resolved = shutil.which(binary)
    if not resolved:
        raise FileNotFoundError(f"找不到 agy CLI: {binary}")
    return resolved


def _run_command(cmd: list[str], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "cmd": cmd,
            "cwd": str(cwd),
            "returncode": result.returncode,
            "duration_ms": duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "cmd": cmd,
            "cwd": str(cwd),
            "returncode": None,
            "duration_ms": duration_ms,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timed_out": True,
        }


def _sanitize_output(text: str) -> str:
    text = AUTH_URL_RE.sub("<redacted-google-auth-url>", text)
    return EMAIL_RE.sub("<redacted-email>", text)


def _preview(text: str, limit: int = 2000) -> str:
    text = _sanitize_output(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... <truncated {len(text) - limit} chars>"


def _extract_json_object(text: str) -> dict[str, Any]:
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_block:
        text = code_block.group(1)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("未找到 JSON 对象")
    return json.loads(text[start:end])


def _flag_present(help_text: str, flag: str) -> bool:
    return re.search(rf"(^|\s){re.escape(flag)}(\s|,|$)", help_text) is not None


def _collect_cli_capabilities(help_text: str) -> dict[str, bool]:
    return {
        "has_print": _flag_present(help_text, "--print"),
        "has_prompt_alias": _flag_present(help_text, "--prompt"),
        "has_print_timeout": _flag_present(help_text, "--print-timeout"),
        "has_add_dir": _flag_present(help_text, "--add-dir"),
        "has_sandbox": _flag_present(help_text, "--sandbox"),
        "has_dangerously_skip_permissions": _flag_present(help_text, "--dangerously-skip-permissions"),
        "has_model_flag": _flag_present(help_text, "--model"),
        "has_output_format_flag": _flag_present(help_text, "--output-format"),
    }


def _walk_model_keys(value: Any, prefix: str = "") -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if SENSITIVE_KEY_RE.search(key_text):
                continue
            if "model" in key_text.lower() and isinstance(child, (str, int, float, bool)):
                findings.append({"path": path, "value": str(child)})
            findings.extend(_walk_model_keys(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_walk_model_keys(child, f"{prefix}[{index}]"))
    return findings


def _inspect_settings(settings_path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(settings_path),
        "exists": settings_path.exists(),
        "readable": False,
        "valid_json": False,
        "model_findings": [],
        "error": "",
    }
    if not settings_path.exists():
        return info
    try:
        raw = settings_path.read_text(encoding="utf-8")
        info["readable"] = True
        parsed = json.loads(raw)
        info["valid_json"] = True
        info["model_findings"] = _walk_model_keys(parsed)
    except Exception as exc:  # noqa: BLE001 - report probe failure without crashing
        info["error"] = str(exc)
    return info


def _collect_auth_log_diagnostics(
    log_dir: Path,
    max_files: int = 8,
    max_lines: int = 80,
    since_epoch: float | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(log_dir),
        "exists": log_dir.exists(),
        "since_epoch": since_epoch,
        "markers": [],
        "keyring_timeout_seen": False,
        "keyring_success_seen": False,
        "silent_auth_success_seen": False,
        "error": "",
    }
    if not log_dir.exists():
        return info

    try:
        log_files = sorted(
            log_dir.glob("cli-*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:max_files]
        markers: list[dict[str, Any]] = []
        for log_file in log_files:
            if since_epoch is not None and log_file.stat().st_mtime < since_epoch - 2:
                continue
            try:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                if not AUTH_LOG_MARKER_RE.search(line):
                    continue
                sanitized = _sanitize_output(line)
                markers.append(
                    {
                        "file": log_file.name,
                        "line": line_no,
                        "message": sanitized,
                    }
                )
                if "keyringAuth: timed out" in line:
                    info["keyring_timeout_seen"] = True
                if "authenticated via keyring" in line:
                    info["keyring_success_seen"] = True
                if "Print mode: silent auth succeeded" in line:
                    info["silent_auth_success_seen"] = True
                if len(markers) >= max_lines:
                    break
            if len(markers) >= max_lines:
                break
        info["markers"] = markers
    except Exception as exc:  # noqa: BLE001 - diagnostics should not fail the probe
        info["error"] = str(exc)
    return info


def _summarize_probe(raw: dict[str, Any]) -> dict[str, Any]:
    if raw is None:
        return {}
    summary = dict(raw)
    summary["stdout_preview"] = _preview(str(summary.pop("stdout", "")))
    summary["stderr_preview"] = _preview(str(summary.pop("stderr", "")))
    return summary


def _has_auth_marker(raw: dict[str, Any] | None) -> bool:
    if not raw:
        return False
    combined = f"{raw.get('stdout', '')}\n{raw.get('stderr', '')}"
    return AUTH_MARKER_RE.search(combined) is not None


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    agy_bin = _resolve_binary(args.agy_bin)
    generated_at = dt.datetime.now(dt.UTC).isoformat()
    probe_started_epoch = time.time()

    with tempfile.TemporaryDirectory(prefix="stocktradebyz-agy-probe-") as tmp:
        probe_cwd = Path(tmp)
        version = _run_command([agy_bin, "--version"], cwd=probe_cwd, timeout_seconds=args.command_timeout)
        help_result = _run_command([agy_bin, "--help"], cwd=probe_cwd, timeout_seconds=args.command_timeout)
        help_text = str(help_result.get("stdout", "")) + "\n" + str(help_result.get("stderr", ""))
        capabilities = _collect_cli_capabilities(help_text)

        json_probe: dict[str, Any] | None = None
        if not args.skip_json_probe and capabilities["has_print"]:
            json_probe = _run_command(
                [
                    agy_bin,
                    "--print-timeout",
                    args.print_timeout,
                    "--print",
                    JSON_PROBE_PROMPT,
                ],
                cwd=probe_cwd,
                timeout_seconds=args.json_probe_timeout,
            )
            parsed: dict[str, Any] | None = None
            parse_error = ""
            try:
                parsed = _extract_json_object(str(json_probe.get("stdout", "")))
            except Exception as exc:  # noqa: BLE001 - captured in report
                parse_error = str(exc)
            json_probe["json_parse_ok"] = parsed == {"ok": True, "runner": "agy"}
            json_probe["parsed_json"] = parsed
            json_probe["json_parse_error"] = parse_error
            json_probe["authentication_required"] = _has_auth_marker(json_probe)

        image_probe: dict[str, Any] | None = None
        image_path = Path(args.image_path).expanduser() if args.image_path else None
        if image_path:
            if not image_path.exists():
                raise FileNotFoundError(f"图片不存在: {image_path}")
            image_prompt = IMAGE_PROBE_PROMPT.format(
                image_path=str(image_path.resolve()),
                code=args.image_code or image_path.name.split("_")[0],
            )
            image_cmd = [
                agy_bin,
                "--add-dir",
                str(image_path.parent.resolve()),
                "--print-timeout",
                args.print_timeout,
                "--print",
                image_prompt,
            ]
            image_probe = _run_command(
                image_cmd,
                cwd=probe_cwd,
                timeout_seconds=args.image_probe_timeout,
            )
            parsed_image: dict[str, Any] | None = None
            image_parse_error = ""
            try:
                parsed_image = _extract_json_object(str(image_probe.get("stdout", "")))
            except Exception as exc:  # noqa: BLE001 - captured in report
                image_parse_error = str(exc)
            image_probe["json_parse_ok"] = bool(parsed_image)
            image_probe["parsed_json"] = parsed_image
            image_probe["json_parse_error"] = image_parse_error
            image_probe["authentication_required"] = _has_auth_marker(image_probe)

        auth_log_diagnostics = _collect_auth_log_diagnostics(Path(args.log_dir).expanduser())
        current_run_auth_log_diagnostics = _collect_auth_log_diagnostics(
            Path(args.log_dir).expanduser(),
            since_epoch=probe_started_epoch,
        )

        report = {
            "generated_at": generated_at,
            "agy_bin": agy_bin,
            "environment": {
                "term": os.environ.get("TERM", ""),
                "shell": os.environ.get("SHELL", ""),
                "cf_bundle_identifier": os.environ.get("__CFBundleIdentifier", ""),
            },
            "version": _summarize_probe(version),
            "help": _summarize_probe(help_result),
            "capabilities": capabilities,
            "settings": _inspect_settings(Path(args.settings_path).expanduser()),
            "auth_log_diagnostics": auth_log_diagnostics,
            "current_run_auth_log_diagnostics": current_run_auth_log_diagnostics,
            "json_probe": _summarize_probe(json_probe) if json_probe else None,
            "image_probe": _summarize_probe(image_probe) if image_probe else None,
            "blocking": {
                "missing_print": not capabilities["has_print"],
                "missing_model_flag": not capabilities["has_model_flag"],
                "missing_output_format_flag": not capabilities["has_output_format_flag"],
                "json_probe_failed": bool(
                    json_probe
                    and (
                        json_probe.get("returncode") != 0
                        or json_probe.get("timed_out")
                        or json_probe.get("authentication_required")
                        or not json_probe.get("json_parse_ok")
                    )
                ),
                "authentication_required": _has_auth_marker(json_probe),
                "current_run_keyring_auth_timeout_seen": current_run_auth_log_diagnostics[
                    "keyring_timeout_seen"
                ],
                "current_run_keyring_success_seen": current_run_auth_log_diagnostics["keyring_success_seen"],
                "image_probe_failed": bool(
                    image_probe
                    and (
                        image_probe.get("returncode") != 0
                        or image_probe.get("timed_out")
                        or image_probe.get("authentication_required")
                        or not image_probe.get("json_parse_ok")
                    )
                ),
            },
        }
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Antigravity CLI review migration capabilities.")
    parser.add_argument("--agy-bin", default="agy", help="agy executable name or absolute path")
    parser.add_argument("--settings-path", default=str(DEFAULT_SETTINGS_PATH), help="Antigravity settings.json path")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR), help="Antigravity CLI log directory")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Directory for JSON probe reports")
    parser.add_argument("--print-timeout", default="5m", help="Value passed to agy --print-timeout")
    parser.add_argument("--command-timeout", type=int, default=30, help="Timeout for version/help commands in seconds")
    parser.add_argument("--json-probe-timeout", type=int, default=360, help="Timeout for the JSON probe in seconds")
    parser.add_argument("--image-probe-timeout", type=int, default=360, help="Timeout for the optional image probe")
    parser.add_argument("--image-path", default="", help="Optional K-line chart image for image-read probe")
    parser.add_argument("--image-code", default="", help="Optional stock code for --image-path")
    parser.add_argument("--skip-json-probe", action="store_true", help="Only inspect local CLI capabilities")
    args = parser.parse_args()

    report = build_report(args)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"agy_cli_probe_{_utc_timestamp()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"report": str(report_path), **report["blocking"]}, ensure_ascii=False, indent=2))
    return 1 if report["blocking"]["missing_print"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
