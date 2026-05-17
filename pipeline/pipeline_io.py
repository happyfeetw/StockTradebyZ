"""
pipeline/io.py
统一路径解析 + 原子写入 candidates*.json。

契约规则：
  - 按日期存档：candidates/candidates_YYYY-MM-DD.json
  - 唯一契约文件（下游只读）：candidates/candidates_latest.json
  - 写入采用"先写临时文件 → os.replace 原子替换"，防止下游读到半写文件。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Union

from schemas import Candidate, CandidateRun

logger = logging.getLogger(__name__)

# 默认输出目录（相对于项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CANDIDATES_DIR = _PROJECT_ROOT / "data" / "candidates"


def _resolve_path(path_like: Union[str, Path]) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else (_PROJECT_ROOT / p)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, content: str) -> None:
    """原子写入：先写 .tmp，再 os.replace。"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    logger.debug("写入完成: %s", path)


def _load_run(path: Path) -> CandidateRun | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return CandidateRun.from_dict(data)


def _candidate_key(candidate: Candidate) -> tuple[str, str]:
    return candidate.code, candidate.strategy


def _run_strategies(run: CandidateRun) -> set[str]:
    strategies = run.meta.get("executed_strategies") or run.meta.get("replaced_strategies") or []
    if strategies:
        return {str(strategy) for strategy in strategies}
    return {candidate.strategy for candidate in run.candidates}


def _strategy_candidate_counts(run: CandidateRun) -> dict[str, int]:
    raw_counts = run.meta.get("strategy_candidate_counts") or {}
    if raw_counts:
        counts = {str(strategy): int(total or 0) for strategy, total in raw_counts.items()}
        for candidate in run.candidates:
            counts.setdefault(candidate.strategy, 0)
        return counts

    counts: dict[str, int] = {}
    for candidate in run.candidates:
        counts[candidate.strategy] = counts.get(candidate.strategy, 0) + 1
    return counts


def merge_same_date_by_strategy(existing: CandidateRun | None, incoming: CandidateRun) -> CandidateRun:
    """
    Merge same-date candidate snapshots by strategy.

    Re-running a strategy should replace that strategy's stale rows, while
    preserving rows from other strategies selected earlier on the same pick date.
    """
    if existing is None or existing.pick_date != incoming.pick_date:
        return incoming

    incoming_strategies = _run_strategies(incoming)
    kept = [
        candidate
        for candidate in existing.candidates
        if candidate.strategy not in incoming_strategies
    ]
    merged: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in [*kept, *incoming.candidates]:
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        merged.append(candidate)

    existing_counts = _strategy_candidate_counts(existing)
    incoming_counts = _strategy_candidate_counts(incoming)
    merged_counts = {
        strategy: total
        for strategy, total in existing_counts.items()
        if strategy not in incoming_strategies
    }
    merged_counts.update(incoming_counts)
    executed_strategies = sorted(_run_strategies(existing) | incoming_strategies)

    meta = {
        **existing.meta,
        **incoming.meta,
        "merge_same_date": True,
        "merged_strategies": sorted({candidate.strategy for candidate in merged}),
        "replaced_strategies": sorted(incoming_strategies),
        "executed_strategies": executed_strategies,
        "strategy_candidate_counts": merged_counts,
        "previous_total": len(existing.candidates),
        "incoming_total": len(incoming.candidates),
        "merged_total": len(merged),
    }
    return CandidateRun(
        run_date=incoming.run_date,
        pick_date=incoming.pick_date,
        candidates=merged,
        meta=meta,
    )


def save_candidates(
    run: CandidateRun,
    *,
    candidates_dir: Union[str, Path, None] = None,
    write_dated: bool = True,
    write_latest: bool = True,
    merge_same_date: bool = False,
) -> dict[str, Path]:
    """
    将 CandidateRun 序列化为 JSON，写入磁盘。

    参数
    ----
    run             : CandidateRun 对象
    candidates_dir  : 输出目录，默认 data/candidates/
    write_dated     : 是否写 candidates_YYYY-MM-DD.json
    write_latest    : 是否覆盖 candidates_latest.json
    merge_same_date : 是否按同一 pick_date 的策略维度合并旧结果

    返回
    ----
    写入成功的路径字典，key 为 "dated" / "latest"。
    """
    out_dir = _resolve_path(candidates_dir) if candidates_dir else _DEFAULT_CANDIDATES_DIR
    _ensure_dir(out_dir)

    if merge_same_date:
        dated_path = out_dir / f"candidates_{run.pick_date}.json"
        existing = _load_run(dated_path)
        merged_run = merge_same_date_by_strategy(existing, run)
        if merged_run is not run:
            logger.info(
                "同日期候选按策略合并: 原 %d，只替换策略 %s，新合计 %d",
                len(existing.candidates) if existing else 0,
                ",".join(merged_run.meta.get("replaced_strategies", [])),
                len(merged_run.candidates),
            )
        run = merged_run

    payload = json.dumps(run.to_dict(), ensure_ascii=False, indent=2)
    written: dict[str, Path] = {}

    if write_dated:
        dated_path = out_dir / f"candidates_{run.pick_date}.json"
        _atomic_write(dated_path, payload)
        written["dated"] = dated_path
        logger.info("存档文件: %s", dated_path)

    if write_latest:
        latest_path = out_dir / "candidates_latest.json"
        _atomic_write(latest_path, payload)
        written["latest"] = latest_path
        logger.info("契约文件: %s", latest_path)

    return written


def load_latest(
    candidates_dir: Union[str, Path, None] = None,
) -> CandidateRun:
    """
    读取 candidates_latest.json，返回 CandidateRun。
    供 dashboard 或外部脚本调用。
    """
    out_dir = _resolve_path(candidates_dir) if candidates_dir else _DEFAULT_CANDIDATES_DIR
    latest_path = out_dir / "candidates_latest.json"

    if not latest_path.exists():
        raise FileNotFoundError(f"契约文件不存在: {latest_path}")

    data = json.loads(latest_path.read_text(encoding="utf-8"))
    return CandidateRun.from_dict(data)


def load_by_date(
    pick_date: str,
    candidates_dir: Union[str, Path, None] = None,
) -> CandidateRun:
    """读取指定日期的存档文件。"""
    out_dir = _resolve_path(candidates_dir) if candidates_dir else _DEFAULT_CANDIDATES_DIR
    path = out_dir / f"candidates_{pick_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"存档文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return CandidateRun.from_dict(data)
