from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPECTED_STRATEGIES = ("b1", "b2", "brick")


def review_key(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes()) if path.exists() else ""


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def resolve_path(value: str | Path, *, base: Path = ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def strategy_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        strategy = str(candidate.get("strategy") or "")
        if not strategy:
            continue
        counts[strategy] = counts.get(strategy, 0) + 1
    return counts


def configured_strategy_counts(candidates_data: dict[str, Any]) -> dict[str, int]:
    raw = (candidates_data.get("meta") or {}).get("strategy_candidate_counts") or {}
    return {str(strategy): int(total or 0) for strategy, total in raw.items()}


def executed_strategies(candidates_data: dict[str, Any]) -> list[str]:
    meta = candidates_data.get("meta") or {}
    raw = meta.get("executed_strategies") or meta.get("merged_strategies") or meta.get("replaced_strategies") or []
    return [str(strategy) for strategy in raw if str(strategy)]


def missing_chart_keys(candidates: list[dict[str, Any]], *, pick_date: str, kline_dir: Path) -> list[str]:
    missing: list[str] = []
    date_dir = kline_dir / pick_date
    for candidate in candidates:
        code = str(candidate.get("code") or "")
        strategy = str(candidate.get("strategy") or "")
        if not code:
            continue
        if not (date_dir / f"{code}_day.jpg").exists() and not (date_dir / f"{code}_day.png").exists():
            missing.append(review_key(code, strategy))
    return missing


def freeze_review_batch(
    *,
    candidates_path: Path,
    batch_root: Path | None = None,
    kline_dir: Path | None = None,
    prompt_path: Path | None = None,
    expected_strategies: list[str] | None = None,
    batch_id: str = "",
    strict: bool = True,
) -> dict[str, Any]:
    candidates_path = candidates_path.resolve()
    batch_root = batch_root or ROOT / "data" / "review_batches"
    kline_dir = kline_dir or ROOT / "data" / "kline"
    prompt_path = prompt_path or ROOT / "agent" / "prompt.md"
    expected = expected_strategies or list(DEFAULT_EXPECTED_STRATEGIES)

    candidates_data = json.loads(candidates_path.read_text(encoding="utf-8"))
    pick_date = str(candidates_data.get("pick_date") or "")
    if not pick_date:
        raise ValueError(f"候选文件缺少 pick_date: {candidates_path}")

    candidates = list(candidates_data.get("candidates") or [])
    candidate_hash = sha256_bytes(canonical_json_bytes(candidates_data))
    batch_id = batch_id or f"{pick_date}_{candidate_hash[:8]}"
    out_dir = batch_root / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)

    actual_counts = strategy_counts(candidates)
    configured_counts = configured_strategy_counts(candidates_data)
    executed = executed_strategies(candidates_data)
    known_strategies = set(actual_counts) | set(configured_counts) | set(executed)
    missing_expected = [strategy for strategy in expected if strategy not in known_strategies]
    charts_missing = missing_chart_keys(candidates, pick_date=pick_date, kline_dir=kline_dir)
    is_complete = not missing_expected and not charts_missing

    batch_payload = {
        **candidates_data,
        "meta": {
            **(candidates_data.get("meta") or {}),
            "review_batch_id": batch_id,
            "review_batch_frozen_at": datetime.now().isoformat(timespec="seconds"),
            "review_batch_source": str(candidates_path),
        },
    }
    (out_dir / "candidates.json").write_text(
        json.dumps(batch_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest = {
        "batch_id": batch_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pick_date": pick_date,
        "candidate_count": len(candidates),
        "candidate_sha256": candidate_hash,
        "candidates_source": str(candidates_path),
        "candidates_file": str(out_dir / "candidates.json"),
        "kline_dir": str(kline_dir),
        "prompt_path": str(prompt_path),
        "prompt_sha256": sha256_file(prompt_path),
        "expected_strategies": expected,
        "executed_strategies": executed,
        "strategy_candidate_counts": configured_counts or actual_counts,
        "actual_strategy_counts": actual_counts,
        "missing_expected_strategies": missing_expected,
        "missing_charts": charts_missing,
        "complete": is_complete,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (batch_root / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if strict and not is_complete:
        problems: list[str] = []
        if missing_expected:
            problems.append(f"缺少策略执行记录: {', '.join(missing_expected)}")
        if charts_missing:
            problems.append(f"缺少 K 线图: {len(charts_missing)} 个")
        raise RuntimeError(f"review batch 校验失败（{batch_id}）: {'; '.join(problems)}")

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="冻结多模型复评候选批次")
    parser.add_argument("--candidates", default="data/candidates/candidates_latest.json")
    parser.add_argument("--batch-root", default="data/review_batches")
    parser.add_argument("--kline-dir", default="data/kline")
    parser.add_argument("--prompt-path", default="agent/prompt.md")
    parser.add_argument("--expected-strategies", default="b1,b2,brick")
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    expected = [item.strip() for item in str(args.expected_strategies).split(",") if item.strip()]
    manifest = freeze_review_batch(
        candidates_path=resolve_path(args.candidates),
        batch_root=resolve_path(args.batch_root),
        kline_dir=resolve_path(args.kline_dir),
        prompt_path=resolve_path(args.prompt_path),
        expected_strategies=expected,
        batch_id=args.batch_id,
        strict=not args.allow_incomplete,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
