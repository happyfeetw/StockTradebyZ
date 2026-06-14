"""
multi_model_review.py
~~~~~~~~~~~~~~~~~~~~~
冻结同一候选批次，并行执行多个 reviewer，最后生成横向共识汇总。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.review_batch import freeze_review_batch
from pipeline.review_consensus import build_consensus

DEFAULT_CONFIG_PATH = ROOT / "config" / "multi_model_review.yaml"
PROGRESS_LINE_RE = re.compile(r"^\[(\d+)(?:-(\d+))?/(\d+)\]\s*(.+)$")
EVENT_LINE_RE = re.compile(r"(\[ERROR\]|\[WARN\]|\[STOP\]|\[INFO\]|失败|错误|限流|重试|完成|已存在|缺少)")
SUMMARY_LINE_RE = re.compile(r"(?:评分完成|复评完成).*?成功\s*(\d+)\s*支[，,]\s*失败/跳过\s*(\d+)\s*支")


@dataclass
class ReviewerRuntime:
    spec: dict[str, Any]
    proc: subprocess.Popen[bytes]
    log_file: Any
    log_path: Path
    started_at: float


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_event(level: str, message: str) -> None:
    print(f"[{timestamp()}] [{level}] {message}", flush=True)


def resolve_path(value: str | Path, *, base: Path = ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(path: Path) -> dict[str, Any]:
    config = load_yaml(path)
    if not config:
        raise FileNotFoundError(f"找不到多模型复评配置：{path}")
    return config


def refresh_latest_json(path: Path, payload: Any, *, default_root: Path) -> None:
    write_json(path, payload)
    try:
        path.resolve().parent.relative_to(default_root.resolve())
    except ValueError:
        return
    write_json(default_root / "latest.json", payload)


def run_z_quality_postprocess(multi_cfg: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any] | None:
    z_cfg = multi_cfg.get("z_quality") or {}
    if not isinstance(z_cfg, dict) or not _truthy(z_cfg.get("enabled"), default=False):
        return None

    import z_quality_review

    config_path = resolve_path(z_cfg.get("config", "config/z_quality_rules.yaml"))
    config = z_quality_review.load_yaml(config_path)
    if not config:
        raise FileNotFoundError(f"找不到 Z 质量规则配置：{config_path}")

    for key in ("raw_dir", "kline_dir", "include_incomplete", "local_rules_only"):
        if key in z_cfg:
            config[key] = z_cfg[key]

    summary_file = str((summary.get("files") or {}).get("summary") or "")
    if not summary_file:
        raise ValueError("共识 summary 缺少 files.summary，无法运行 Z 质量层")
    summary_path = resolve_path(summary_file)
    output_root = resolve_path(z_cfg.get("output_root") or config.get("output_root", "data/z_quality"))
    max_items = z_cfg.get("max_items", config.get("max_items"))
    max_items_int = int(max_items) if max_items not in {"", None, 0, "0"} else None

    log_event("INFO", f"开始 Z 质量裁决: consensus={summary_path}")
    z_summary = z_quality_review.run_z_quality_review(
        config,
        summary_path=summary_path,
        output_root=output_root,
        max_items=max_items_int,
    )
    log_event("INFO", f"Z 质量裁决已写入: {z_summary['files']['summary']}")
    log_event(
        "INFO",
        "Z 裁决统计: "
        + ", ".join(f"{key}={value}" for key, value in sorted(z_summary.get("verdict_counts", {}).items())),
    )
    return z_summary


def config_path_for_spec(spec: dict[str, Any], run_dir: Path | None) -> Path:
    configured = Path(str(spec.get("config") or ""))
    if run_dir is not None:
        candidate = run_dir / configured.name
        if candidate.exists():
            return candidate
    return resolve_path(configured)


def model_profile(spec: dict[str, Any]) -> str:
    return str(spec.get("model_profile") or spec.get("model") or spec.get("reviewer_key") or "model").strip()


def model_key(spec: dict[str, Any]) -> str:
    configured = str(spec.get("model_key") or spec.get("model_id") or "").strip()
    if configured:
        return configured
    return f"{spec.get('reviewer_key')}/{model_profile(spec)}"


def validate_unique_model_keys(specs: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    for spec in specs:
        key = str(spec.get("model_key") or "").strip()
        if not key:
            continue
        label = str(spec.get("label") or spec.get("reviewer_key") or key)
        if key in seen:
            raise RuntimeError(f"多模型复评模型 ID 重复：{key}（{seen[key]} / {label}）")
        seen[key] = label


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def enforce_no_model_substitution(spec: dict[str, Any], *, profile: str, multi_cfg: dict[str, Any]) -> None:
    if not _truthy(multi_cfg.get("no_model_substitution"), default=True):
        return
    forbidden = sorted(key for key in spec if key.startswith("fallback_model") or key in {"fallback_reviewer", "substitute_model"})
    if forbidden:
        raise RuntimeError(f"{model_key(spec)} 禁止配置模型降级/替换字段：{forbidden}")
    if not str(spec.get("model") or "").strip():
        raise RuntimeError(f"{model_key(spec)} 启用 no_model_substitution 时必须显式声明 model")
    if not profile:
        raise RuntimeError(f"{model_key(spec)} 启用 no_model_substitution 时必须显式声明 model_profile")
    if str(spec.get("reviewer_key") or "") == "codex-cli":
        model = str(spec.get("model") or "").strip()
        if model != "gpt-5.5" and spec.get("force_fixed_model") is not False:
            raise RuntimeError(
                f"{model_key(spec)} 声明了 {model}，但 codex_cli_review 默认锁定 gpt-5.5；"
                "如需非 5.5 测试，必须显式 force_fixed_model: false，且不能作为正式降级替换。"
            )


def safe_log_name(key: str, *, attempt: int) -> str:
    name = key.replace("/", "__")
    if attempt > 1:
        name = f"{name}__attempt{attempt}"
    return name


def reviewer_concurrency_group(spec: dict[str, Any]) -> str:
    return str(
        spec.get("concurrency_group")
        or spec.get("execution_backend")
        or spec.get("reviewer_key")
        or spec.get("reviewer")
        or model_key(spec)
        or "reviewer"
    )


def reviewer_execution_waves(run_specs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    pending = list(run_specs)
    waves: list[list[dict[str, Any]]] = []
    while pending:
        used_groups: set[str] = set()
        wave: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for spec in pending:
            group = reviewer_concurrency_group(spec)
            if group in used_groups:
                remaining.append(spec)
                continue
            used_groups.add(group)
            wave.append(spec)
        waves.append(wave)
        pending = remaining
    return waves


def reviewer_group(model_key_value: str) -> str:
    return str(model_key_value or "reviewer").split("/", 1)[0] or "reviewer"


def format_duration(seconds: float) -> str:
    seconds_i = max(0, int(seconds))
    minutes, sec = divmod(seconds_i, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def tail_text(path: Path, *, max_bytes: int = 16000) -> str:
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes), os.SEEK_SET)
        return f.read().decode("utf-8", errors="replace")


def _shorten_line(line: str, *, limit: int = 220) -> str:
    line = re.sub(r"\s+", " ", line.strip())
    if len(line) <= limit:
        return line
    return line[: limit - 1] + "…"


def progress_snapshot(log_path: Path) -> dict[str, Any]:
    text = tail_text(log_path)
    if not text:
        return {
            "completed": None,
            "total": None,
            "progress_text": "等待输出",
            "latest": "暂无日志输出",
        }

    latest_progress = ""
    latest_event = ""
    latest_summary = ""
    completed: int | None = None
    total: int | None = None
    success_count: int | None = None
    failed_count: int | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        summary_match = SUMMARY_LINE_RE.search(line)
        if summary_match:
            success_count = int(summary_match.group(1))
            failed_count = int(summary_match.group(2))
            latest_summary = line
            latest_event = line
            continue
        match = PROGRESS_LINE_RE.match(line)
        if match:
            start = int(match.group(1))
            end = int(match.group(2) or start)
            completed = end
            total = int(match.group(3))
            latest_progress = line
            latest_event = line
            continue
        if EVENT_LINE_RE.search(line):
            latest_event = line

    latest = latest_summary or latest_progress or latest_event
    if success_count is not None and failed_count is not None:
        attempted = success_count + failed_count
        total = total or attempted
        completed = attempted
        pct = min(100.0, max(0.0, attempted / total * 100)) if total else 0.0
        progress_text = f"成功 {success_count}/{total}，失败/跳过 {failed_count} ({pct:.0f}%)"
    elif completed is not None and total:
        pct = min(100.0, max(0.0, completed / total * 100))
        progress_text = f"处理到 {completed}/{total} ({pct:.0f}%)"
    else:
        progress_text = "等待首条进度"
        latest = latest or text.splitlines()[-1].strip()

    return {
        "completed": completed,
        "total": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "progress_text": progress_text,
        "latest": _shorten_line(latest or "暂无可读进度"),
    }


def format_reviewer_progress(
    *,
    key: str,
    status: str,
    log_path: Path,
    started_at: float,
    exit_code: int | None = None,
) -> str:
    snapshot = progress_snapshot(log_path)
    elapsed = format_duration(time.monotonic() - started_at)
    exit_text = "" if exit_code is None else f", exit={exit_code}"
    return (
        f"    - {key}: {status}{exit_text}, elapsed={elapsed}, "
        f"progress={snapshot['progress_text']}, latest={snapshot['latest']}"
    )


def log_grouped_progress(runtimes: dict[str, ReviewerRuntime], *, attempt: int) -> None:
    if not runtimes:
        return
    log_event("PROGRESS", f"多模型复评进度 attempt={attempt}")
    groups: dict[str, list[tuple[str, ReviewerRuntime]]] = {}
    for key, runtime in sorted(runtimes.items()):
        groups.setdefault(reviewer_group(key), []).append((key, runtime))
    for group, items in sorted(groups.items()):
        print(f"  [{group}]", flush=True)
        for key, runtime in items:
            print(
                format_reviewer_progress(
                    key=key,
                    status="running",
                    log_path=runtime.log_path,
                    started_at=runtime.started_at,
                ),
                flush=True,
            )


def read_failure_info(log_path: Path, *, max_lines: int = 160, max_chars: int = 6000) -> dict[str, str]:
    if not log_path.exists():
        return {"summary": f"日志文件不存在：{log_path}", "log_tail": ""}
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()[-max_lines:]
    tail = "\n".join(lines)[-max_chars:]
    patterns = re.compile(
        r"(ERROR|WARN|Traceback|Exception|失败|错误|timeout|timed out|FAILED_PRECONDITION|"
        r"ModelNotFound|OAuth|unauthori[sz]ed|permission|not supported|rate limit|quota)",
        re.IGNORECASE,
    )
    matched = [line.strip() for line in lines if patterns.search(line)]
    nonempty = [line.strip() for line in lines if line.strip()]
    summary = (matched[-1] if matched else (nonempty[-1] if nonempty else "")).strip()
    return {"summary": summary[:1000] or f"模型进程失败，详见日志：{log_path}", "log_tail": tail}


def prepare_reviewer_config(
    *,
    spec: dict[str, Any],
    multi_cfg: dict[str, Any],
    manifest: dict[str, Any],
    run_dir: Path,
    review_runs_dir: Path,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    reviewer_key = str(spec["reviewer_key"])
    profile = model_profile(spec)
    enforce_no_model_substitution(spec, profile=profile, multi_cfg=multi_cfg)
    base_cfg = load_yaml(config_path_for_spec(spec, run_dir))
    output_dir = review_runs_dir / str(manifest["batch_id"]) / reviewer_key / profile
    runtime_cfg = {
        **base_cfg,
        "candidates": manifest["candidates_file"],
        "kline_dir": manifest["kline_dir"],
        "output_dir": str(output_dir),
        "prompt_path": manifest["prompt_path"],
        "suggest_min_score": float(multi_cfg.get("suggest_min_score", 4.0)),
        "batch_size": int(spec.get("batch_size") or multi_cfg.get("batch_size") or base_cfg.get("batch_size") or 5),
        "skip_existing": bool(multi_cfg.get("skip_existing", base_cfg.get("skip_existing", True))),
        "classic_pattern_enabled": bool(multi_cfg.get("classic_pattern_enabled", base_cfg.get("classic_pattern_enabled", True))),
        "group_review_by_strategy": bool(multi_cfg.get("group_review_by_strategy", base_cfg.get("group_review_by_strategy", True))),
        "max_items": multi_cfg.get("max_items", None),
    }
    if "review_scoring" in multi_cfg:
        runtime_cfg["review_scoring"] = multi_cfg["review_scoring"]
    if spec.get("model"):
        runtime_cfg["model"] = spec["model"]
    runtime_cfg["model_key"] = model_key(spec)
    runtime_cfg["model_profile"] = profile
    if spec.get("output_format"):
        runtime_cfg["output_format"] = spec["output_format"]
    reviewer_runtime_overrides = (
        "print_timeout",
        "timeout_seconds",
        "request_delay",
        "stdin_mode",
        "dangerously_skip_permissions",
        "stop_on_cli_timeout",
        "fallback_to_single_on_batch_error",
        "split_batch_on_cli_timeout",
        "json_repair_enabled",
        "auth_recovery_enabled",
        "auth_recovery_wait_seconds",
        "auth_recovery_check_interval",
        "auth_recovery_probe_timeout_seconds",
        "max_requests_per_run",
        "reasoning_effort",
        "speed_tier",
        "force_fixed_model",
    )
    for key in reviewer_runtime_overrides:
        if key in spec:
            runtime_cfg[key] = spec[key]
    if "max_requests_per_run" in multi_cfg:
        runtime_cfg["max_requests_per_run"] = multi_cfg.get("max_requests_per_run")

    configs_dir = review_runs_dir / str(manifest["batch_id"]) / "configs"
    runtime_config_path = configs_dir / f"{reviewer_key}_{profile}.yaml"
    write_yaml(runtime_config_path, runtime_cfg)

    run_spec = {
        "enabled": True,
        "label": spec.get("label") or model_key(spec),
        "model_key": model_key(spec),
        "model_id": model_key(spec),
        "execution_backend": reviewer_key,
        "reviewer": reviewer_key,
        "reviewer_key": reviewer_key,
        "model": str(spec.get("model") or runtime_cfg.get("model") or ""),
        "model_profile": profile,
        "declared_model": str(spec.get("model") or ""),
        "declared_model_profile": profile,
        "model_substitution_allowed": not _truthy(multi_cfg.get("no_model_substitution"), default=True),
        "output_dir": str(output_dir),
        "config": str(runtime_config_path),
        "script": str(resolve_path(spec["script"])),
    }
    return runtime_cfg, runtime_config_path, run_spec


def _run_reviewer_wave(run_specs: list[dict[str, Any]], *, run_dir: Path, env: dict[str, str], attempt: int) -> dict[str, dict[str, Any]]:
    logs_dir = run_dir / "multi_model_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    processes: dict[str, ReviewerRuntime] = {}
    for spec in run_specs:
        key = str(spec["model_key"])
        log_path = logs_dir / f"{safe_log_name(key, attempt=attempt)}.log"
        log_file = open(log_path, "wb")
        cmd = [sys.executable, str(spec["script"]), "--config", str(spec["config"])]
        attempt_label = f" attempt={attempt}" if attempt > 1 else ""
        log_event("START", f"[{reviewer_group(key)}] 启动 {key}{attempt_label}")
        log_event("COMMAND", " ".join(cmd))
        log_event("LOG", f"{key} -> {log_path}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        processes[key] = ReviewerRuntime(
            spec=spec,
            proc=proc,
            log_file=log_file,
            log_path=log_path,
            started_at=time.monotonic(),
        )

    results: dict[str, dict[str, Any]] = {}
    last_notice = 0.0
    while processes:
        for key, runtime in list(processes.items()):
            code = runtime.proc.poll()
            if code is None:
                continue
            runtime.log_file.close()
            duration_seconds = max(0.0, time.monotonic() - runtime.started_at)
            results[key] = {
                "attempt": attempt,
                "exit_code": int(code),
                "log_path": str(runtime.log_path),
                "duration_seconds": round(duration_seconds, 3),
            }
            log_event("DONE", f"[{reviewer_group(key)}] {key} 结束")
            print(
                format_reviewer_progress(
                    key=key,
                    status="finished",
                    log_path=runtime.log_path,
                    started_at=runtime.started_at,
                    exit_code=int(code),
                ),
                flush=True,
            )
            print(f"      log={runtime.log_path}", flush=True)
            del processes[key]
        now = time.monotonic()
        if processes and now - last_notice >= 30:
            log_grouped_progress(processes, attempt=attempt)
            last_notice = now
        if processes:
            time.sleep(2)
    return results


def run_reviewers_parallel(run_specs: list[dict[str, Any]], *, run_dir: Path, env: dict[str, str], attempt: int = 1) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    waves = reviewer_execution_waves(run_specs)
    for index, wave in enumerate(waves, start=1):
        if len(waves) > 1:
            wave_keys = ", ".join(str(spec["model_key"]) for spec in wave)
            log_event("INFO", f"调度批次 {index}/{len(waves)} attempt={attempt}: {wave_keys}")
        results.update(_run_reviewer_wave(wave, run_dir=run_dir, env=env, attempt=attempt))
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多模型复评与共识汇总")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="多模型配置 YAML")
    parser.add_argument("--run-dir", default="", help="Workbench 本次运行快照目录")
    parser.add_argument("--batch-id", default="", help="手动指定 review batch id")
    parser.add_argument("--allow-incomplete-batch", action="store_true", help="允许候选批次缺策略或缺图")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = resolve_path(args.run_dir) if args.run_dir else ROOT / "data" / "runs" / f"multi_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = resolve_path(args.config)
    if args.run_dir:
        run_config_path = run_dir / "multi_model_review.yaml"
        if run_config_path.exists():
            config_path = run_config_path
    multi_cfg = load_config(config_path)

    expected = [str(item) for item in (multi_cfg.get("expected_strategies") or []) if str(item)]
    manifest = freeze_review_batch(
        candidates_path=resolve_path(multi_cfg.get("candidates", "data/candidates/candidates_latest.json")),
        batch_root=resolve_path(multi_cfg.get("batch_root", "data/review_batches")),
        kline_dir=resolve_path(multi_cfg.get("kline_dir", "data/kline")),
        prompt_path=resolve_path(multi_cfg.get("prompt_path", "agent/prompt.md")),
        expected_strategies=expected,
        batch_id=str(args.batch_id or multi_cfg.get("batch_id") or ""),
        strict=bool(multi_cfg.get("strict_batch", True)) and not args.allow_incomplete_batch,
    )
    log_event(
        "INFO",
        f"review_batch={manifest['batch_id']} pick_date={manifest['pick_date']} candidates={manifest['candidate_count']}",
    )

    review_runs_dir = resolve_path(multi_cfg.get("review_runs_dir", "data/review_runs"))
    consensus_root = resolve_path(multi_cfg.get("consensus_dir", "data/review_consensus"))
    enabled_specs = [spec for spec in (multi_cfg.get("reviewers") or []) if spec.get("enabled", True)]
    if not enabled_specs:
        raise RuntimeError("multi_model_review.yaml 没有启用任何 reviewer")

    run_specs: list[dict[str, Any]] = []
    for spec in enabled_specs:
        _, runtime_path, run_spec = prepare_reviewer_config(
            spec=spec,
            multi_cfg=multi_cfg,
            manifest=manifest,
            run_dir=run_dir,
            review_runs_dir=review_runs_dir,
        )
        log_event("CONFIG", f"{run_spec['model_key']} -> {runtime_path}")
        run_specs.append(run_spec)
    validate_unique_model_keys(run_specs)

    run_specs_path = review_runs_dir / str(manifest["batch_id"]) / "review_runs.json"
    write_json(
        run_specs_path,
        {
            "batch_id": manifest["batch_id"],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "reviewers": run_specs,
        },
    )

    env = {**os.environ, "NO_COLOR": "1"}
    first_results = run_reviewers_parallel(run_specs, run_dir=run_dir, env=env, attempt=1)
    for spec in run_specs:
        key = str(spec["model_key"])
        attempt_info = first_results.get(key)
        spec["attempts"] = [attempt_info] if attempt_info else []

    failed_specs = [
        spec
        for spec in run_specs
        if (spec.get("attempts") or [{}])[-1].get("exit_code") not in (0, None)
    ]
    if failed_specs and _truthy(multi_cfg.get("rerun_failed_models_once"), default=True):
        failed_keys = ", ".join(str(spec["model_key"]) for spec in failed_specs)
        log_event("WARN", f"以下模型首轮失败，将按原模型重跑一次（skip_existing 会跳过已完成结果）：{failed_keys}")
        second_results = run_reviewers_parallel(failed_specs, run_dir=run_dir, env=env, attempt=2)
        for spec in failed_specs:
            key = str(spec["model_key"])
            attempt_info = second_results.get(key)
            if attempt_info:
                spec.setdefault("attempts", []).append(attempt_info)

    exit_codes: dict[str, int | None] = {}
    for spec in run_specs:
        attempts = [attempt for attempt in (spec.get("attempts") or []) if isinstance(attempt, dict)]
        last_attempt = attempts[-1] if attempts else {}
        exit_code = last_attempt.get("exit_code")
        spec["exit_code"] = exit_code
        exit_codes[str(spec["model_key"])] = exit_code
        if last_attempt.get("log_path"):
            spec["log_path"] = last_attempt["log_path"]
        failed_attempts = [attempt for attempt in attempts if attempt.get("exit_code") not in (0, None)]
        if failed_attempts and exit_code == 0:
            spec["recovered_after_rerun"] = True
        if exit_code not in (0, None):
            log_path = Path(str(last_attempt.get("log_path") or ""))
            failure = read_failure_info(log_path)
            spec["failure_reason"] = failure["summary"]
            spec["failure_log_tail"] = failure["log_tail"]

    summary = build_consensus(
        batch_manifest=manifest,
        run_specs=run_specs,
        output_dir=consensus_root / str(manifest["batch_id"]),
        threshold=float(multi_cfg.get("suggest_min_score", 4.0)),
        review_scoring=multi_cfg.get("review_scoring"),
    )
    z_summary = run_z_quality_postprocess(multi_cfg, summary)
    if z_summary:
        summary["z_quality"] = {
            "summary": z_summary["files"]["summary"],
            "decisions": z_summary["files"]["decisions"],
            "processed_count": z_summary.get("processed_count"),
            "verdict_counts": z_summary.get("verdict_counts", {}),
            "result_mode": z_summary.get("result_mode"),
        }
        refresh_latest_json(Path(summary["files"]["summary"]), summary, default_root=consensus_root)
    write_json(run_specs_path, {"batch_id": manifest["batch_id"], "reviewers": run_specs, "summary": summary})

    log_event("INFO", f"共识汇总已写入: {summary['files']['summary']}")
    log_event(
        "INFO",
        "决策统计: "
        + ", ".join(f"{key}={value}" for key, value in sorted(summary.get("decision_bucket_counts", {}).items())),
    )
    failed_models = [key for key, code in exit_codes.items() if code not in (0, None)]
    if failed_models:
        log_event("ERROR", f"以下模型进程失败: {failed_models}")
    if not summary.get("complete"):
        log_event("ERROR", "共识汇总不完整：存在模型缺失评分，可断点重跑多模型复评。")
    return 1 if failed_models or not summary.get("complete") else 0


if __name__ == "__main__":
    raise SystemExit(main())
