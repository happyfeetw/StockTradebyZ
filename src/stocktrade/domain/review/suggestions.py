from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

DEFAULT_CLASSIC_PATTERN_STRATEGIES: tuple[str, ...] = ("b1", "b2", "brick")
BASE_SCORE_WEIGHTS: dict[str, float] = {
    "trend_structure": 0.20,
    "price_position": 0.20,
    "volume_behavior": 0.30,
    "previous_abnormal_move": 0.30,
}
CLASSIC_PATTERN_BONUS_WEIGHT: float = 0.10


def review_key(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


def candidate_review_key(candidate: Mapping[str, Any]) -> str:
    return review_key(str(candidate.get("code") or ""), str(candidate.get("strategy") or ""))


def result_review_key(result: Mapping[str, Any]) -> str:
    if result.get("review_key"):
        return str(result["review_key"])
    return review_key(str(result.get("code") or ""), str(result.get("strategy") or ""))


def _strategy_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _classic_pattern_enabled_strategies(value: Any) -> set[str]:
    if isinstance(value, dict):
        if "classic_pattern_enabled" in value:
            if not bool(value.get("classic_pattern_enabled")):
                return set()
            strategies = list(DEFAULT_CLASSIC_PATTERN_STRATEGIES)
        elif "classic_pattern_strategies" in value:
            strategies = _strategy_list(value.get("classic_pattern_strategies"))
        else:
            strategies = list(DEFAULT_CLASSIC_PATTERN_STRATEGIES)
    elif isinstance(value, bool):
        strategies = list(DEFAULT_CLASSIC_PATTERN_STRATEGIES) if value else []
    elif value is None:
        strategies = list(DEFAULT_CLASSIC_PATTERN_STRATEGIES)
    else:
        strategies = _strategy_list(value)
    return {strategy.lower() for strategy in strategies}


def is_composite_strategy(strategy: str) -> bool:
    return any(separator in strategy for separator in ("+", "&", ",", "|"))


def has_classic_pattern_review(strategy: str, classic_pattern_config: Any = None) -> bool:
    normalized = str(strategy or "").strip().lower()
    if not normalized or is_composite_strategy(normalized):
        return False
    enabled = _classic_pattern_enabled_strategies(classic_pattern_config)
    return "*" in enabled or normalized in enabled


def _numeric_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 5:
        return None
    return score


def classic_pattern_score(result: Mapping[str, Any]) -> float | None:
    scores = result.get("scores") or {}
    if not isinstance(scores, dict):
        return None
    return _numeric_score(scores.get("classic_pattern_match"))


def normalize_scores(result: Mapping[str, Any], classic_pattern_config: Any = None) -> dict[str, Any]:
    normalized_result = deepcopy(dict(result))
    strategy = str(normalized_result.get("strategy") or "").strip().lower()
    scores = normalized_result.get("scores") or {}
    if not isinstance(scores, dict):
        return normalized_result

    normalized_scores: dict[str, float] = {}
    for key in BASE_SCORE_WEIGHTS:
        score = _numeric_score(scores.get(key))
        if score is None:
            return normalized_result
        normalized_scores[key] = score

    merged_scores = {**scores, **normalized_scores}
    has_classic_pattern = has_classic_pattern_review(strategy, classic_pattern_config)
    base_score = sum(normalized_scores[key] * weight for key, weight in BASE_SCORE_WEIGHTS.items())
    classic_bonus = 0.0

    if not has_classic_pattern:
        merged_scores["classic_pattern_match"] = 0.0
        normalized_result["classic_pattern_type"] = "none"
        normalized_result["classic_pattern_reasoning"] = ""
    else:
        classic_score = _numeric_score(scores.get("classic_pattern_match"))
        if classic_score is None:
            return normalized_result
        classic_score = max(1.0, classic_score)
        normalized_scores["classic_pattern_match"] = classic_score
        merged_scores["classic_pattern_match"] = classic_score
        classic_bonus = max(0.0, classic_score - 1.0) * CLASSIC_PATTERN_BONUS_WEIGHT

    normalized_result["scores"] = merged_scores
    normalized_result["total_score"] = round(min(5.0, base_score + classic_bonus), 2)

    if normalized_scores["volume_behavior"] <= 1:
        normalized_result["verdict"] = "FAIL"
    elif normalized_result["total_score"] >= 4.0:
        normalized_result["verdict"] = "PASS"
    elif normalized_result["total_score"] >= 3.2:
        normalized_result["verdict"] = "WATCH"
    else:
        normalized_result["verdict"] = "FAIL"

    return normalized_result


def generate_suggestion(
    *,
    pick_date: str,
    all_results: Sequence[Mapping[str, Any]],
    min_score: float,
    candidates: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    candidates = candidates or []
    key_to_strategy = {
        candidate_review_key(candidate): str(candidate.get("strategy") or "")
        for candidate in candidates
        if candidate.get("code")
    }
    result_by_key = {result_review_key(result): result for result in all_results if result.get("code")}
    passed = [result for result in all_results if result.get("total_score", 0) >= min_score]
    excluded = [result_review_key(result) for result in all_results if result.get("total_score", 0) < min_score]

    strategy_counts: dict[str, dict[str, int]] = {}
    for candidate in candidates:
        code = str(candidate.get("code") or "")
        if not code:
            continue
        current_review_key = candidate_review_key(candidate)
        strategy = str(candidate.get("strategy") or "unknown")
        counts = strategy_counts.setdefault(
            strategy,
            {"total": 0, "reviewed": 0, "recommended": 0, "excluded": 0, "pending": 0},
        )
        counts["total"] += 1
        result = result_by_key.get(current_review_key)
        if not result:
            counts["pending"] += 1
            continue
        counts["reviewed"] += 1
        if result.get("total_score", 0) >= min_score:
            counts["recommended"] += 1
        else:
            counts["excluded"] += 1

    passed.sort(key=lambda result: result.get("total_score", 0), reverse=True)

    recommendations = [
        {
            "rank": index + 1,
            "code": result["code"],
            "strategy": result.get("strategy") or key_to_strategy.get(result_review_key(result), ""),
            "review_key": result_review_key(result),
            "verdict": result.get("verdict", ""),
            "total_score": result.get("total_score", 0),
            "signal_type": result.get("signal_type", ""),
            "classic_pattern_type": result.get("classic_pattern_type", ""),
            "classic_pattern_match": classic_pattern_score(result),
            "classic_pattern_reasoning": result.get("classic_pattern_reasoning", ""),
            "comment": result.get("comment", ""),
        }
        for index, result in enumerate(passed)
    ]

    return {
        "date": pick_date,
        "min_score_threshold": min_score,
        "total_reviewed": len(all_results),
        "recommendations": recommendations,
        "excluded": excluded,
        "strategy_counts": strategy_counts,
    }
