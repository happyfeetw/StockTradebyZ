from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.z_features import compute_z_features  # noqa: E402

DEFAULT_CONFIG_PATH = ROOT / "config" / "z_quality_rules.yaml"
DEFAULT_PROMPT_PATH = ROOT / "agent" / "z_quality_prompt.md"


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


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def review_key(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


def numeric(value: Any) -> float | None:
    try:
        if value in {"", None}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_score(value: float) -> float:
    return round(max(0.0, min(5.0, value)), 2)


def z_threshold(thresholds: dict[str, Any], key: str, *, legacy_key: str, default: float) -> float:
    """Read Z-layer thresholds while accepting the first-version config keys."""
    if key in thresholds:
        return float(thresholds[key])
    if legacy_key in thresholds:
        return float(thresholds[legacy_key])
    return default


def z_quality_thresholds(rules: dict[str, Any]) -> dict[str, float]:
    thresholds = rules.get("thresholds") or {}
    return {
        "z_select_min_quality_score": z_threshold(
            thresholds,
            "z_select_min_quality_score",
            legacy_key="a_select_min_quality_score",
            default=4.2,
        ),
        "z_watch_min_quality_score": z_threshold(
            thresholds,
            "z_watch_min_quality_score",
            legacy_key="b_watch_min_quality_score",
            default=3.4,
        ),
        "max_reject_score": float(thresholds.get("max_reject_score", 2.6)),
    }


def consensus_base_dir(summary: dict[str, Any], summary_path: Path) -> Path:
    files = summary.get("files") or {}
    summary_file = str(files.get("summary") or "")
    return resolve_path(summary_file).parent if summary_file else summary_path.parent


def load_consensus_payload(summary_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    summary = load_json(summary_path)
    if not isinstance(summary, dict):
        raise ValueError(f"共识 summary 非法：{summary_path}")
    base = consensus_base_dir(summary, summary_path)
    files = summary.get("files") or {}
    decisions_path = resolve_path(files.get("decisions") or base / "decisions.json")
    details_path = resolve_path(files.get("details") or base / "details.json")
    decisions = load_json(decisions_path)
    details = load_json(details_path)
    if not isinstance(decisions, list):
        raise ValueError(f"共识 decisions 非法：{decisions_path}")
    if not isinstance(details, list):
        details = []
    return summary, decisions, details


def group_details(details: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in details:
        key = str(item.get("review_key") or review_key(str(item.get("code") or ""), str(item.get("strategy") or "")))
        grouped.setdefault(key, []).append(item)
    return grouped


def compact_review(full_review: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_key": detail.get("model_key"),
        "reviewer": detail.get("reviewer"),
        "model": detail.get("model") or full_review.get("model"),
        "status": detail.get("status"),
        "recommended": bool(detail.get("recommended")),
        "total_score": full_review.get("total_score", detail.get("total_score")),
        "verdict": full_review.get("verdict", detail.get("verdict")),
        "comment": full_review.get("comment") or full_review.get("signal_reasoning") or detail.get("comment"),
        "common_gate": full_review.get("common_gate") or {},
        "common_gate_status": full_review.get("common_gate_status") or (full_review.get("common_gate") or {}).get("status"),
        "strategy_score": full_review.get("strategy_score"),
        "scores": full_review.get("scores") or {},
        "classic_pattern_type": full_review.get("classic_pattern_type"),
        "classic_pattern_match": (full_review.get("scores") or {}).get("classic_pattern_match"),
        "classic_pattern_reasoning": full_review.get("classic_pattern_reasoning"),
        "trend_reasoning": full_review.get("trend_reasoning"),
        "position_reasoning": full_review.get("position_reasoning"),
        "volume_reasoning": full_review.get("volume_reasoning"),
        "abnormal_move_reasoning": full_review.get("abnormal_move_reasoning"),
        "hard_veto_reasons": full_review.get("hard_veto_reasons") or (full_review.get("common_gate") or {}).get("hard_veto_reasons") or [],
        "watch_cap_reasons": full_review.get("watch_cap_reasons") or (full_review.get("common_gate") or {}).get("watch_cap_reasons") or [],
        "file": detail.get("file"),
    }


def load_model_reviews(details_for_item: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for detail in details_for_item:
        path_text = str(detail.get("file") or "")
        full = load_json(resolve_path(path_text)) if path_text else {}
        if not isinstance(full, dict):
            full = {}
        reviews.append(compact_review(full, detail))
    return reviews


def model_evidence_stats(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [review for review in reviews if str(review.get("status") or "") == "reviewed"]
    common_statuses = [str(review.get("common_gate_status") or "").upper() for review in completed]
    verdicts = [str(review.get("verdict") or "").upper() for review in completed]
    hard_veto_count = sum(1 for review in completed if review.get("hard_veto_reasons") or (review.get("common_gate") or {}).get("hard_veto"))
    scores = [numeric(review.get("total_score")) for review in completed]
    scores = [score for score in scores if score is not None]
    classic_scores = [numeric(review.get("classic_pattern_match")) for review in completed]
    classic_scores = [score for score in classic_scores if score is not None]
    previous_abnormal_scores = [numeric((review.get("scores") or {}).get("previous_abnormal_move")) for review in completed]
    previous_abnormal_scores = [score for score in previous_abnormal_scores if score is not None]
    return {
        "completed": len(completed),
        "common_gate_fail_count": sum(1 for status in common_statuses if status == "FAIL"),
        "common_gate_pass_count": sum(1 for status in common_statuses if status == "PASS"),
        "pass_count": sum(1 for verdict in verdicts if verdict == "PASS"),
        "watch_count": sum(1 for verdict in verdicts if verdict == "WATCH"),
        "fail_count": sum(1 for verdict in verdicts if verdict == "FAIL"),
        "hard_veto_count": hard_veto_count,
        "max_score": max(scores) if scores else None,
        "min_score": min(scores) if scores else None,
        "score_spread": (max(scores) - min(scores)) if len(scores) >= 2 else 0.0,
        "avg_classic_pattern_match": round(sum(classic_scores) / len(classic_scores), 2) if classic_scores else None,
        "max_classic_pattern_match": max(classic_scores) if classic_scores else None,
        "avg_previous_abnormal_move": round(sum(previous_abnormal_scores) / len(previous_abnormal_scores), 2) if previous_abnormal_scores else None,
        "max_previous_abnormal_move": max(previous_abnormal_scores) if previous_abnormal_scores else None,
    }


def feature_value(features: dict[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = features
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    return default if node is None else node


def local_quality_judge(
    *,
    decision: dict[str, Any],
    reviews: list[dict[str, Any]],
    features: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any]:
    total_models = int(decision.get("total_models") or len(reviews) or 1)
    stats = model_evidence_stats(reviews)
    hard_vetoes: list[str] = []
    watch_caps: list[str] = []
    reasons: list[str] = []
    risks: list[str] = []

    if decision.get("missing_models") and not bool(rules.get("include_incomplete", False)):
        hard_vetoes.append("consensus_incomplete")
        risks.append("模型复评不完整，暂不进入精选池。")

    if stats["common_gate_fail_count"] > total_models / 2:
        hard_vetoes.append("majority_common_gate_fail")
        risks.append("多数模型公共交易资格失败。")
    if stats["hard_veto_count"] > total_models / 2:
        hard_vetoes.append("model_hard_veto_majority")
        risks.append("多数模型触发硬否决。")

    if not features.get("data_available"):
        watch_caps.append("data_limited")
        risks.append(f"本地K线特征不可用：{features.get('data_error') or 'unknown'}")
    else:
        if feature_value(features, "candle", "fangliang_yinxian"):
            hard_vetoes.append("fangliang_yinxian")
            risks.append("信号日是放量阴线，按 Z 质量层直接排除。")
        if feature_value(features, "candle", "high_volume_upper_shadow"):
            hard_vetoes.append("high_volume_upper_shadow")
            risks.append("信号日放量长上影，存在冲高回落/派发嫌疑。")
        if feature_value(features, "structure", "centipede_like"):
            hard_vetoes.append("centipede_like")
            risks.append("近 20 日上下影和乱量偏多，接近蜈蚣图。")
        if (
            feature_value(features, "volume", "largest_volume_down_20")
            and (feature_value(features, "volume", "largest_volume_ratio_to_ma20", default=0) or 0) >= 1.5
        ):
            hard_vetoes.append("largest_volume_down_distribution")
            risks.append("近 20 日最大量出现在下跌 K 线，筹码发散风险高。")

        if feature_value(features, "price_position", "overhead_pressure_close"):
            watch_caps.append("overhead_pressure_close")
            risks.append("上方 20 日压力较近，不能轻易给高质量精选。")
        support_distance = feature_value(features, "price_position", "support_distance_pct")
        if support_distance is not None and float(support_distance) > 10:
            watch_caps.append("support_too_far")
            risks.append("距离最近可见支撑偏远，天然止损空间不够友好。")

    score_spread = stats.get("score_spread") or 0.0
    if score_spread >= 1.8:
        watch_caps.append("model_disagreement")
        risks.append("模型分歧较大，需要人工重点复核。")

    quality_score = 2.4
    bucket = str(decision.get("decision_bucket") or "")
    consensus_verdict = str(decision.get("consensus_verdict") or "").upper()
    if bucket == "all_models_recommended":
        quality_score += 1.4
        reasons.append("多模型全票推荐，基础共识质量较高。")
    elif bucket == "majority_recommended":
        quality_score += 1.0
        reasons.append("多数模型推荐，具备基础共识优势。")
    elif bucket == "single_model_recommended":
        quality_score += 0.45
        reasons.append("有单模型推荐，存在可复核的结构亮点。")
    elif consensus_verdict == "WATCH":
        quality_score += 0.25
        reasons.append("共识层给到 WATCH，说明并非完全无交易审美。")

    if stats["common_gate_pass_count"] >= max(1, total_models - 1):
        quality_score += 0.45
        reasons.append("多数模型公共交易资格通过。")
    if (stats.get("max_classic_pattern_match") or 0) >= 4:
        quality_score += 0.35
        reasons.append("至少一个模型认为经典图形完成度较高。")
    if (stats.get("max_previous_abnormal_move") or 0) >= 4:
        quality_score += 0.25
        reasons.append("模型证据中存在有效资金异动痕迹。")

    if features.get("data_available"):
        if feature_value(features, "price_position", "near_support"):
            quality_score += 0.35
            reasons.append("本地K线显示价格仍贴近可见支撑，试错质量更好。")
        if feature_value(features, "volume", "pullback_shrink"):
            quality_score += 0.35
            reasons.append("本地K线显示回调缩量，量价呼吸较健康。")
        if feature_value(features, "structure", "breathing_structure") == "healthy":
            quality_score += 0.25
            reasons.append("近 20 日放量/缩量节奏较健康。")
        if feature_value(features, "volume", "largest_volume_direction_20") == "up":
            quality_score += 0.2
            reasons.append("近 20 日最大量出现在上涨 K 线，资金痕迹相对正面。")
        if feature_value(features, "candle", "gap_up"):
            watch_caps.append("gap_up_risk")
            risks.append("信号日存在跳空，次日不能追高验证。")

    thresholds = z_quality_thresholds(rules)
    if hard_vetoes:
        verdict = "REJECT"
        quality_score = min(quality_score, thresholds["max_reject_score"])
    else:
        quality_score = clamp_score(quality_score)
        if quality_score >= thresholds["z_select_min_quality_score"] and not watch_caps:
            verdict = "A_SELECT"
        elif quality_score >= thresholds["z_watch_min_quality_score"]:
            verdict = "B_WATCH"
        elif reasons:
            verdict = "C_REVIEW_ONLY"
        else:
            verdict = "REJECT"

    strategy = str(decision.get("strategy") or "").lower()
    return {
        "code": str(decision.get("code") or ""),
        "strategy": strategy,
        "review_key": str(decision.get("review_key") or review_key(str(decision.get("code") or ""), strategy)),
        "pick_date": str(decision.get("pick_date") or ""),
        "rank": decision.get("rank"),
        "z_quality_verdict": verdict,
        "z_quality_score": clamp_score(float(quality_score)),
        "quality_reasons": reasons[:8],
        "quality_risks": risks[:8],
        "model_disagreement_notes": model_disagreement_notes(decision, stats),
        "next_day_plan": next_day_plan(strategy=strategy, features=features),
        "hold_plan": hold_plan(strategy=strategy),
        "hard_vetoes": sorted(set(hard_vetoes)),
        "watch_caps": sorted(set(watch_caps)),
        "data_limitations": features.get("data_limitations") or [],
        "local_stats": stats,
        "z_quality_thresholds": thresholds,
    }


def model_disagreement_notes(decision: dict[str, Any], stats: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if (stats.get("score_spread") or 0) >= 1.8:
        notes.append(f"模型最高分与最低分差 {stats['score_spread']:.2f}，说明图形解释存在明显分歧。")
    rec_count = int(decision.get("recommended_count") or 0)
    total = int(decision.get("total_models") or 0)
    if rec_count and total and rec_count < total:
        notes.append(f"{rec_count}/{total} 个模型推荐，适合人工复核分歧来源。")
    if rec_count == 0:
        notes.append("没有模型达到推荐门槛；若入选，只能基于 Z 质量层的低风险观察逻辑。")
    return notes


def next_day_plan(*, strategy: str, features: dict[str, Any]) -> dict[str, Any]:
    base_confirm = [
        "大环境不能处于明显空头；本层不自动判断大盘，由人工确认。",
        "次日不能高开远离支撑后追涨。",
        "不能出现放量冲高回落、长上影失败或放量阴线。",
    ]
    strategy_confirm = {
        "b1": [
            "回踩区域不破可见支撑，最好继续缩量或温和放量向上。",
            "如果低开后快速收回且不放量杀跌，可作为观察点。",
        ],
        "b2": [
            "B2 强阳后的承接不能明显走弱，不能跌回关键阳线内部太深。",
            "量能要支持多头接管，不能变成放量分歧失败。",
        ],
        "brick": [
            "绿转红后 1-4 日节奏仍要延续，不能跳空高开后冲高回落。",
            "转折点仍应贴近支撑或平台，不做远离支撑的追红砖。",
        ],
    }.get(strategy, [])
    skip_conditions = [
        "高开过多导致远离支撑。",
        "开盘或盘中出现放量长上影。",
        "跌破关键支撑后无法收回。",
        "量价表现从健康呼吸变成乱量。",
    ]
    if feature_value(features, "candle", "gap_up"):
        skip_conditions.insert(0, "已有跳空风险，次日继续高开时优先跳过。")
    return {
        "entry_bias": "条件满足才观察，不是自动买入指令。",
        "confirm_conditions": base_confirm + strategy_confirm,
        "skip_conditions": skip_conditions,
    }


def hold_plan(*, strategy: str) -> dict[str, Any]:
    valid = [
        "缩量调整且支撑不破，可以继续观察。",
        "上涨有量、回踩缩量，说明量价呼吸仍在。",
    ]
    warnings = [
        "买后不涨或盈转亏，要降低预期。",
        "放量大阴、冲高回落、跌破关键支撑属于警戒。",
        "走势开始让人持续操心，说明质量可能不够硬。",
    ]
    if strategy == "brick":
        valid.append("砖型图 1-4 日内延续红砖节奏，才符合超短观察逻辑。")
        warnings.append("红砖后快速翻绿或第四块红砖走完，应降低持有预期。")
    return {
        "valid_hold_conditions": valid,
        "warning_signals": warnings,
    }


def build_llm_input(
    *,
    decision: dict[str, Any],
    reviews: list[dict[str, Any]],
    features: dict[str, Any],
    local_judgement: dict[str, Any],
    chart_path: Path,
    rules: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ruleset_version": str(rules.get("ruleset_version") or "z_quality_v1"),
        "candidate": {
            "code": decision.get("code"),
            "strategy": decision.get("strategy"),
            "pick_date": decision.get("pick_date"),
            "rank": decision.get("rank"),
            "close": decision.get("close"),
        },
        "consensus": {
            key: decision.get(key)
            for key in (
                "decision_bucket",
                "consensus_score",
                "consensus_verdict",
                "average_score",
                "min_score",
                "max_score",
                "agreement_score",
                "recommended_count",
                "completed_count",
                "total_models",
                "scores_by_model",
                "verdicts_by_model",
                "recommended_by_model",
                "comments_by_model",
            )
        },
        "model_reviews": reviews,
        "computed_features": features,
        "local_prejudge": local_judgement,
        "charts": {
            "day": str(chart_path) if chart_path.exists() else "",
        },
    }


def flatten_decision(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pick_date": row.get("pick_date"),
        "rank": row.get("rank"),
        "review_key": row.get("review_key"),
        "code": row.get("code"),
        "strategy": row.get("strategy"),
        "z_quality_verdict": row.get("z_quality_verdict"),
        "z_quality_score": row.get("z_quality_score"),
        "hard_vetoes": ",".join(row.get("hard_vetoes") or []),
        "watch_caps": ",".join(row.get("watch_caps") or []),
        "quality_reasons": " | ".join(row.get("quality_reasons") or []),
        "quality_risks": " | ".join(row.get("quality_risks") or []),
        "data_limitations": ",".join(row.get("data_limitations") or []),
    }


def run_z_quality_review(config: dict[str, Any], *, summary_path: Path, output_root: Path, max_items: int | None = None) -> dict[str, Any]:
    summary, decisions, details = load_consensus_payload(summary_path)
    batch_id = str(summary.get("batch_id") or "")
    pick_date = str(summary.get("pick_date") or "")
    if not batch_id or not pick_date:
        raise ValueError("共识 summary 缺少 batch_id 或 pick_date")

    details_by_key = group_details(details)
    raw_dir = resolve_path(config.get("raw_dir", "data/raw"))
    kline_dir = resolve_path(config.get("kline_dir", "data/kline"))
    out_dir = output_root / batch_id
    per_stock_dir = out_dir / "items"
    out_dir.mkdir(parents=True, exist_ok=True)
    per_stock_dir.mkdir(parents=True, exist_ok=True)

    include_incomplete = bool(config.get("include_incomplete", False))
    if max_items is None and config.get("max_items") not in {"", None}:
        max_items = int(config["max_items"])

    selected_decisions = []
    for decision in decisions:
        if not include_incomplete and decision.get("missing_models"):
            continue
        selected_decisions.append(decision)
        if max_items and len(selected_decisions) >= max_items:
            break

    z_decisions: list[dict[str, Any]] = []
    llm_inputs: list[dict[str, Any]] = []
    for decision in selected_decisions:
        code = str(decision.get("code") or "")
        strategy = str(decision.get("strategy") or "")
        item_key = str(decision.get("review_key") or review_key(code, strategy))
        reviews = load_model_reviews(details_by_key.get(item_key, []))
        features = compute_z_features(raw_dir, code, pick_date=pick_date)
        judgement = local_quality_judge(decision=decision, reviews=reviews, features=features, rules=config)
        chart_path = kline_dir / pick_date / f"{code}_day.jpg"
        llm_input = build_llm_input(
            decision=decision,
            reviews=reviews,
            features=features,
            local_judgement=judgement,
            chart_path=chart_path,
            rules=config,
        )
        z_decision = {
            **judgement,
            "batch_id": batch_id,
            "decision_bucket": decision.get("decision_bucket"),
            "consensus_verdict": decision.get("consensus_verdict"),
            "consensus_score": decision.get("consensus_score"),
            "recommended_count": decision.get("recommended_count"),
            "total_models": decision.get("total_models"),
            "chart_path": str(chart_path) if chart_path.exists() else "",
            "llm_input_file": str(per_stock_dir / f"{item_key}.input.json"),
            "result_mode": "local_rules_dry_run",
        }
        write_json(per_stock_dir / f"{item_key}.json", z_decision)
        write_json(per_stock_dir / f"{item_key}.input.json", llm_input)
        z_decisions.append(z_decision)
        llm_inputs.append({"review_key": item_key, "input_file": z_decision["llm_input_file"]})

    counts = Counter(str(item.get("z_quality_verdict") or "UNKNOWN") for item in z_decisions)
    output_files = {
        "summary": str(out_dir / "summary.json"),
        "decisions": str(out_dir / "decisions.json"),
        "decisions_csv": str(out_dir / "decisions.csv"),
        "llm_inputs": str(out_dir / "llm_inputs.json"),
        "items_dir": str(per_stock_dir),
    }
    z_summary = {
        "batch_id": batch_id,
        "pick_date": pick_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ruleset_version": str(config.get("ruleset_version") or "z_quality_v1"),
        "source_consensus_summary": str(summary_path),
        "source_candidate_count": len(decisions),
        "processed_count": len(z_decisions),
        "include_incomplete": include_incomplete,
        "result_mode": "local_rules_dry_run",
        "z_quality_thresholds": z_quality_thresholds(config),
        "verdict_counts": dict(sorted(counts.items())),
        "files": output_files,
    }
    write_json(out_dir / "summary.json", z_summary)
    write_json(out_dir / "decisions.json", z_decisions)
    write_json(out_dir / "llm_inputs.json", llm_inputs)
    write_csv(out_dir / "decisions.csv", [flatten_decision(row) for row in z_decisions])

    default_root = (ROOT / "data" / "z_quality").resolve()
    try:
        out_dir.resolve().relative_to(default_root)
    except ValueError:
        pass
    else:
        write_json(default_root / "latest.json", z_summary)

    return z_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Z 视角精选池质量裁决")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Z 质量规则 YAML")
    parser.add_argument("--consensus", default="", help="共识 summary.json；默认读取配置 consensus_summary")
    parser.add_argument("--output-root", default="", help="输出根目录；默认读取配置 output_root")
    parser.add_argument("--max-items", type=int, default=0, help="最多处理多少只；0 表示不限制")
    parser.add_argument("--include-incomplete", action="store_true", help="包含模型缺失的共识项")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    if not config:
        raise FileNotFoundError(f"找不到 Z 质量规则配置：{config_path}")
    if args.include_incomplete:
        config["include_incomplete"] = True
    summary_path = resolve_path(args.consensus or config.get("consensus_summary", "data/review_consensus/latest.json"))
    output_root = resolve_path(args.output_root or config.get("output_root", "data/z_quality"))
    summary = run_z_quality_review(
        config,
        summary_path=summary_path,
        output_root=output_root,
        max_items=args.max_items or None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
