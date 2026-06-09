"""
multi_model_review.py
~~~~~~~~~~~~~~~~~~~~~
冻结同一候选批次，并行执行多个 reviewer，最后生成横向共识汇总。
"""

from __future__ import annotations

import argparse
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
    return f"{spec.get('reviewer_key')}/{model_profile(spec)}"


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
    runtime_cfg["model_profile"] = profile
    if spec.get("output_format"):
        runtime_cfg["output_format"] = spec["output_format"]
    for key in ("reasoning_effort", "speed_tier", "force_fixed_model"):
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


def run_reviewers_parallel(run_specs: list[dict[str, Any]], *, run_dir: Path, env: dict[str, str], attempt: int = 1) -> dict[str, dict[str, Any]]:
    logs_dir = run_dir / "multi_model_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    processes: dict[str, tuple[subprocess.Popen[bytes], Any, Path]] = {}
    for spec in run_specs:
        key = str(spec["model_key"])
        log_path = logs_dir / f"{safe_log_name(key, attempt=attempt)}.log"
        log_file = open(log_path, "wb")
        cmd = [sys.executable, str(spec["script"]), "--config", str(spec["config"])]
        attempt_label = f" attempt={attempt}" if attempt > 1 else ""
        print(f"[INFO] 启动 {key}{attempt_label}: {' '.join(cmd)}")
        print(f"[INFO] {key} log: {log_path}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        processes[key] = (proc, log_file, log_path)

    results: dict[str, dict[str, Any]] = {}
    last_notice = 0.0
    while processes:
        for key, (proc, log_file, log_path) in list(processes.items()):
            code = proc.poll()
            if code is None:
                continue
            log_file.close()
            results[key] = {
                "attempt": attempt,
                "exit_code": int(code),
                "log_path": str(log_path),
            }
            print(f"[INFO] {key} 结束，exit={code}，log={log_path}")
            del processes[key]
        now = time.monotonic()
        if processes and now - last_notice >= 30:
            running = ", ".join(sorted(processes))
            print(f"[INFO] 多模型复评仍在运行：{running}")
            last_notice = now
        if processes:
            time.sleep(2)
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
    print(f"[INFO] review_batch={manifest['batch_id']} pick_date={manifest['pick_date']} candidates={manifest['candidate_count']}")

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
        print(f"[INFO] reviewer config: {run_spec['model_key']} -> {runtime_path}")
        run_specs.append(run_spec)

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
        print(f"[WARN] 以下模型首轮失败，将按原模型重跑一次（skip_existing 会跳过已完成结果）：{failed_keys}")
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
    write_json(run_specs_path, {"batch_id": manifest["batch_id"], "reviewers": run_specs, "summary": summary})

    print(f"[INFO] 共识汇总已写入: {summary['files']['summary']}")
    print(
        "[INFO] 决策统计: "
        + ", ".join(f"{key}={value}" for key, value in sorted(summary.get("decision_bucket_counts", {}).items()))
    )
    failed_models = [key for key, code in exit_codes.items() if code not in (0, None)]
    if failed_models:
        print(f"[ERROR] 以下模型进程失败: {failed_models}")
    if not summary.get("complete"):
        print("[ERROR] 共识汇总不完整：存在模型缺失评分，可断点重跑多模型复评。")
    return 1 if failed_models or not summary.get("complete") else 0


if __name__ == "__main__":
    raise SystemExit(main())
