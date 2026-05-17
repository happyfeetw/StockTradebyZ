"""
Archive daily final stock-picking results for local workbench history.

The script reads existing candidates, review outputs, and chart paths, then
writes a stable per-date history snapshot under data/history/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def atomic_write_json(path: Path, data: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def resolve(path_text: str | None, default: Path) -> Path:
    if not path_text:
        return default
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def find_chart(kline_dir: Path, pick_date: str, code: str) -> str:
    date_dir = kline_dir / pick_date
    for suffix in ("jpg", "png"):
        path = date_dir / f"{code}_day.{suffix}"
        if path.exists():
            return str(path)
    return ""


def result_status(code: str, review: dict[str, Any], recommendation_ranks: dict[str, int]) -> str:
    if code in recommendation_ranks:
        return "recommended"
    if review:
        return "reviewed"
    return "unreviewed"


def build_rows(
    *,
    candidates_data: dict[str, Any],
    suggestion: dict[str, Any],
    review_dir: Path,
    kline_dir: Path,
    pick_date: str,
    run_id: str,
) -> list[dict[str, Any]]:
    recommendations = suggestion.get("recommendations", []) if suggestion else []
    recommendation_ranks = {
        str(item.get("code")): int(item.get("rank") or index + 1)
        for index, item in enumerate(recommendations)
        if item.get("code")
    }

    rows: list[dict[str, Any]] = []
    for candidate in candidates_data.get("candidates", []):
        code = str(candidate.get("code") or "")
        if not code:
            continue
        review = load_json(review_dir / f"{code}.json")
        rows.append(
            {
                "date": pick_date,
                "run_id": run_id,
                "code": code,
                "strategy": candidate.get("strategy") or "",
                "close": candidate.get("close"),
                "turnover_n": candidate.get("turnover_n"),
                "brick_growth": candidate.get("brick_growth"),
                "review": review,
                "rank": recommendation_ranks.get(code),
                "chart": find_chart(kline_dir, pick_date, code),
                "status": result_status(code, review, recommendation_ranks),
            }
        )

    rows.sort(
        key=lambda item: (
            item["rank"] is None,
            item["rank"] if item["rank"] is not None else 999999,
            str(item.get("strategy") or ""),
            str(item.get("code") or ""),
        )
    )
    return rows


def candidate_strategy_totals(candidates_data: dict[str, Any]) -> dict[str, int]:
    meta = candidates_data.get("meta") or {}
    raw_counts = meta.get("strategy_candidate_counts") or {}
    counts = {str(strategy): int(total or 0) for strategy, total in raw_counts.items()}
    for strategy in meta.get("executed_strategies") or []:
        counts.setdefault(str(strategy), 0)
    return counts


def strategy_counts(rows: list[dict[str, Any]], strategy_totals: dict[str, int] | None = None) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    strategy_totals = strategy_totals or {}
    for strategy, total in strategy_totals.items():
        counts[strategy] = {"total": int(total), "recommended": 0, "reviewed": 0, "unreviewed": 0}
    for row in rows:
        strategy = str(row.get("strategy") or "unknown")
        status = str(row.get("status") or "unknown")
        if strategy not in counts:
            counts[strategy] = {"total": 0, "recommended": 0, "reviewed": 0, "unreviewed": 0}
            counts[strategy]["total"] += 1
        if status in counts[strategy]:
            counts[strategy][status] += 1
    return counts


def build_summary(
    *,
    pick_date: str,
    run_id: str,
    candidates_data: dict[str, Any],
    suggestion: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = strategy_counts(rows, candidate_strategy_totals(candidates_data))
    recommended = [row for row in rows if row.get("status") == "recommended"]
    reviewed = [row for row in rows if row.get("review")]
    return {
        "date": pick_date,
        "run_id": run_id,
        "archived_at": dt.datetime.now().isoformat(timespec="seconds"),
        "candidate_run_date": candidates_data.get("run_date"),
        "candidate_count": len(rows),
        "reviewed_count": len(reviewed),
        "recommended_count": len(recommended),
        "strategy_counts": counts,
        "executed_strategies": sorted(counts),
        "min_score_threshold": suggestion.get("min_score_threshold"),
        "source": {
            "candidates": "data/candidates/candidates_latest.json",
            "review": f"data/review/{pick_date}",
            "kline": f"data/kline/{pick_date}",
        },
    }


def update_index(history_dir: Path, summary: dict[str, Any]) -> None:
    index_path = history_dir / "index.json"
    existing = load_json(index_path)
    dates = existing.get("dates", []) if existing else []
    by_date = {str(item.get("date")): item for item in dates if item.get("date")}
    by_date[str(summary["date"])] = {
        "date": summary["date"],
        "run_id": summary.get("run_id", ""),
        "archived_at": summary.get("archived_at", ""),
        "candidate_count": summary.get("candidate_count", 0),
        "reviewed_count": summary.get("reviewed_count", 0),
        "recommended_count": summary.get("recommended_count", 0),
        "strategy_counts": summary.get("strategy_counts", {}),
    }
    ordered = sorted(by_date.values(), key=lambda item: str(item.get("date") or ""), reverse=True)
    atomic_write_json(
        index_path,
        {
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "dates": ordered,
        },
    )


def archive(args: argparse.Namespace) -> Path:
    candidates_path = resolve(args.candidates, ROOT / "data" / "candidates" / "candidates_latest.json")
    candidates_data = load_json(candidates_path)
    if not candidates_data:
        raise FileNotFoundError(f"候选文件不存在或为空: {candidates_path}")

    pick_date = args.date or str(candidates_data.get("pick_date") or "")
    if not pick_date:
        raise ValueError("无法确定归档日期，请指定 --date 或确保 candidates JSON 包含 pick_date。")

    review_root = resolve(args.review_dir, ROOT / "data" / "review")
    review_dir = review_root / pick_date
    suggestion = load_json(review_dir / "suggestion.json")
    kline_dir = resolve(args.kline_dir, ROOT / "data" / "kline")
    history_dir = resolve(args.history_dir, ROOT / "data" / "history")
    run_id = args.run_id or ""

    rows = build_rows(
        candidates_data=candidates_data,
        suggestion=suggestion,
        review_dir=review_dir,
        kline_dir=kline_dir,
        pick_date=pick_date,
        run_id=run_id,
    )
    strategy_totals = candidate_strategy_totals(candidates_data)
    if not rows and not strategy_totals:
        raise ValueError(f"没有可归档候选: {candidates_path}")

    date_dir = history_dir / pick_date
    all_rows = {"date": pick_date, "run_id": run_id, "results": rows}
    atomic_write_json(date_dir / "all.json", all_rows)

    strategies = sorted(set(strategy_totals) | {str(row.get("strategy") or "unknown") for row in rows})
    for strategy in strategies:
        strategy_rows = [row for row in rows if str(row.get("strategy") or "unknown") == strategy]
        atomic_write_json(date_dir / f"{strategy}.json", {"date": pick_date, "run_id": run_id, "results": strategy_rows})

    summary = build_summary(
        pick_date=pick_date,
        run_id=run_id,
        candidates_data=candidates_data,
        suggestion=suggestion,
        rows=rows,
    )
    atomic_write_json(date_dir / "summary.json", summary)
    update_index(history_dir, summary)
    print(f"[INFO] 已归档 {pick_date}: 候选 {len(rows)}，推荐 {summary['recommended_count']}，输出 {date_dir}")
    return date_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="归档每日最终选股结果")
    parser.add_argument("--date", default="", help="归档日期，默认读取 candidates pick_date")
    parser.add_argument("--run-id", default="", help="关联的 workbench run_id")
    parser.add_argument("--candidates", default="", help="候选 JSON，默认 data/candidates/candidates_latest.json")
    parser.add_argument("--review-dir", default="", help="复评根目录，默认 data/review")
    parser.add_argument("--kline-dir", default="", help="图表根目录，默认 data/kline")
    parser.add_argument("--history-dir", default="", help="历史归档根目录，默认 data/history")
    return parser.parse_args()


def main() -> None:
    archive(parse_args())


if __name__ == "__main__":
    main()
