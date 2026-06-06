from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THRESHOLD = 4.0


def review_key(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


def resolve_path(value: str | Path, *, base: Path = ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def model_key(spec: dict[str, Any]) -> str:
    key = str(spec.get("model_key") or "").strip()
    if key:
        return key
    reviewer = str(spec.get("reviewer") or spec.get("reviewer_key") or "reviewer").strip()
    model = str(spec.get("model_profile") or spec.get("model") or "model").strip()
    return f"{reviewer}/{model}"


def review_output_file(spec: dict[str, Any], *, pick_date: str, item_key: str) -> Path:
    output_dir = resolve_path(spec["output_dir"])
    dated = output_dir / pick_date / f"{item_key}.json"
    if dated.exists():
        return dated
    return output_dir / f"{item_key}.json"


def numeric_score(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_recommended(review: dict[str, Any], threshold: float) -> bool:
    score = numeric_score(review.get("total_score"))
    return score is not None and score >= threshold


def decision_bucket(completed: int, recommended: int, total_models: int) -> str:
    if completed < total_models:
        return "incomplete"
    if recommended == total_models:
        return "all_models_recommended"
    if recommended == 0:
        return "none_recommended"
    if recommended > total_models / 2:
        return "majority_recommended"
    if recommended == 1:
        return "single_model_recommended"
    return "partial_recommended"


def row_comment(review: dict[str, Any]) -> str:
    return str(review.get("comment") or review.get("signal_reasoning") or "")[:1000]


def build_consensus(
    *,
    batch_manifest: dict[str, Any],
    run_specs: list[dict[str, Any]],
    output_dir: Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    candidates_file = resolve_path(batch_manifest["candidates_file"])
    batch_payload = load_json(candidates_file)
    pick_date = str(batch_manifest.get("pick_date") or batch_payload.get("pick_date") or "")
    batch_id = str(batch_manifest.get("batch_id") or "")
    if not pick_date:
        raise ValueError("batch manifest 缺少 pick_date")
    if not batch_id:
        raise ValueError("batch manifest 缺少 batch_id")

    output_dir = output_dir or ROOT / "data" / "review_consensus" / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)
    model_specs = [spec for spec in run_specs if spec.get("enabled", True)]
    if not model_specs:
        raise ValueError("没有启用的 reviewer 规格")

    details: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    invariant_violations: list[dict[str, Any]] = []
    total_models = len(model_specs)

    for rank, candidate in enumerate(batch_payload.get("candidates") or [], 1):
        code = str(candidate.get("code") or "")
        if not code:
            continue
        strategy = str(candidate.get("strategy") or "")
        item_key = str(candidate.get("review_key") or review_key(code, strategy))
        scores_by_model: dict[str, float | None] = {}
        verdicts_by_model: dict[str, str] = {}
        recommended_by_model: dict[str, bool] = {}
        comments_by_model: dict[str, str] = {}
        completed_models: list[str] = []
        recommended_models: list[str] = []
        missing_models: list[str] = []

        for spec in model_specs:
            key = model_key(spec)
            path = review_output_file(spec, pick_date=pick_date, item_key=item_key)
            review = load_json(path)
            if not review:
                missing_models.append(key)
                details.append(
                    {
                        "batch_id": batch_id,
                        "pick_date": pick_date,
                        "rank": rank,
                        "review_key": item_key,
                        "code": code,
                        "strategy": strategy,
                        "model_key": key,
                        "reviewer": str(spec.get("reviewer") or spec.get("reviewer_key") or ""),
                        "model": str(spec.get("model") or ""),
                        "total_score": "",
                        "verdict": "",
                        "recommended": False,
                        "status": "missing",
                        "comment": "",
                        "file": str(path),
                    }
                )
                continue

            score = numeric_score(review.get("total_score"))
            verdict = str(review.get("verdict") or "")
            recommended = is_recommended(review, threshold)
            scores_by_model[key] = score
            verdicts_by_model[key] = verdict
            recommended_by_model[key] = recommended
            comments_by_model[key] = row_comment(review)
            completed_models.append(key)
            if recommended:
                recommended_models.append(key)
            if recommended and verdict.upper() != "PASS":
                invariant_violations.append(
                    {
                        "review_key": item_key,
                        "model_key": key,
                        "total_score": score,
                        "verdict": verdict,
                        "file": str(path),
                    }
                )

            details.append(
                {
                    "batch_id": batch_id,
                    "pick_date": pick_date,
                    "rank": rank,
                    "review_key": item_key,
                    "code": code,
                    "strategy": strategy,
                    "model_key": key,
                    "reviewer": str(review.get("reviewer") or spec.get("reviewer") or spec.get("reviewer_key") or ""),
                    "model": str(review.get("model") or spec.get("model") or ""),
                    "total_score": score if score is not None else "",
                    "verdict": verdict,
                    "recommended": recommended,
                    "status": "reviewed",
                    "comment": row_comment(review),
                    "file": str(path),
                }
            )

        bucket = decision_bucket(len(completed_models), len(recommended_models), total_models)
        decisions.append(
            {
                "batch_id": batch_id,
                "pick_date": pick_date,
                "rank": rank,
                "review_key": item_key,
                "code": code,
                "strategy": strategy,
                "close": candidate.get("close"),
                "decision_bucket": bucket,
                "all_models_recommended": bucket == "all_models_recommended",
                "recommended_count": len(recommended_models),
                "completed_count": len(completed_models),
                "total_models": total_models,
                "completed_models": completed_models,
                "recommended_models": recommended_models,
                "missing_models": missing_models,
                "scores_by_model": scores_by_model,
                "verdicts_by_model": verdicts_by_model,
                "recommended_by_model": recommended_by_model,
                "comments_by_model": comments_by_model,
            }
        )

    strategy_counts: dict[str, dict[str, int]] = {}
    bucket_counts: dict[str, int] = {}
    for decision in decisions:
        strategy = str(decision.get("strategy") or "unknown")
        bucket = str(decision.get("decision_bucket") or "unknown")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        counts = strategy_counts.setdefault(
            strategy,
            {
                "total": 0,
                "all_models_recommended": 0,
                "majority_recommended": 0,
                "single_model_recommended": 0,
                "partial_recommended": 0,
                "none_recommended": 0,
                "incomplete": 0,
            },
        )
        counts["total"] += 1
        counts[bucket] = counts.get(bucket, 0) + 1

    summary = {
        "batch_id": batch_id,
        "pick_date": pick_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "threshold": threshold,
        "candidate_count": len(decisions),
        "model_count": total_models,
        "models": [model_key(spec) for spec in model_specs],
        "complete": not any(decision.get("missing_models") for decision in decisions),
        "decision_bucket_counts": bucket_counts,
        "strategy_counts": strategy_counts,
        "all_models_recommended_count": bucket_counts.get("all_models_recommended", 0),
        "invariant_violations": invariant_violations,
        "files": {
            "summary": str(output_dir / "summary.json"),
            "decisions": str(output_dir / "decisions.json"),
            "details": str(output_dir / "details.json"),
            "decisions_csv": str(output_dir / "decisions.csv"),
            "details_csv": str(output_dir / "details.csv"),
        },
        "review_runs": model_specs,
        "batch_manifest": batch_manifest,
    }

    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "decisions.json", decisions)
    write_json(output_dir / "details.json", details)
    write_csv(output_dir / "details.csv", details)
    write_csv(output_dir / "decisions.csv", flatten_decision_rows(decisions))
    default_consensus_root = (ROOT / "data" / "review_consensus").resolve()
    try:
        output_dir.resolve().relative_to(default_consensus_root)
    except ValueError:
        pass
    else:
        write_json(default_consensus_root / "latest.json", summary)
    return summary


def flatten_decision_rows(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        row = {
            "batch_id": decision.get("batch_id"),
            "pick_date": decision.get("pick_date"),
            "rank": decision.get("rank"),
            "review_key": decision.get("review_key"),
            "code": decision.get("code"),
            "strategy": decision.get("strategy"),
            "close": decision.get("close"),
            "decision_bucket": decision.get("decision_bucket"),
            "all_models_recommended": decision.get("all_models_recommended"),
            "recommended_count": decision.get("recommended_count"),
            "completed_count": decision.get("completed_count"),
            "total_models": decision.get("total_models"),
            "completed_models": ",".join(decision.get("completed_models") or []),
            "recommended_models": ",".join(decision.get("recommended_models") or []),
            "missing_models": ",".join(decision.get("missing_models") or []),
        }
        for key, score in sorted((decision.get("scores_by_model") or {}).items()):
            safe_key = key.replace("/", "__")
            row[f"score__{safe_key}"] = score
            row[f"verdict__{safe_key}"] = (decision.get("verdicts_by_model") or {}).get(key, "")
            row[f"recommended__{safe_key}"] = (decision.get("recommended_by_model") or {}).get(key, False)
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总多模型复评共识")
    parser.add_argument("--manifest", required=True, help="review batch manifest.json")
    parser.add_argument("--runs", required=True, help="包含 reviewers 列表的 JSON 文件")
    parser.add_argument("--output-dir", default="", help="共识输出目录")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = load_json(resolve_path(args.manifest))
    runs_payload = load_json(resolve_path(args.runs))
    run_specs = runs_payload.get("reviewers") if isinstance(runs_payload, dict) else runs_payload
    if not isinstance(run_specs, list):
        raise ValueError("--runs 必须是列表，或包含 reviewers 列表的对象")
    output_dir = resolve_path(args.output_dir) if args.output_dir else None
    summary = build_consensus(
        batch_manifest=manifest,
        run_specs=run_specs,
        output_dir=output_dir,
        threshold=float(args.threshold),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
