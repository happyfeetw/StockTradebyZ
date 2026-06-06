from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


BASE_SCORE_WEIGHTS: dict[str, float] = {
    "trend_structure": 0.20,
    "price_position": 0.20,
    "volume_behavior": 0.30,
    "previous_abnormal_move": 0.30,
}

CLASSIC_PATTERN_BONUS_WEIGHT = 0.10

COMMON_GATE_SCORE_FIELDS: tuple[str, ...] = (
    "trend_qualification",
    "support_stop_loss_control",
    "overhead_room",
    "volume_health",
    "post_entry_discipline",
)

DEFAULT_REVIEW_SCORING: dict[str, Any] = {
    "enabled": True,
    "common_gate": {
        "enabled": True,
        "pass_min": 3.0,
        "watch_min": 2.4,
        "hard_fail_below": 2.0,
        "watch_caps_strategy_pass": True,
        "weights": {
            "trend_qualification": 0.20,
            "support_stop_loss_control": 0.25,
            "overhead_room": 0.20,
            "volume_health": 0.25,
            "post_entry_discipline": 0.10,
        },
        "hard_veto_score_max": {
            "support_stop_loss_control": 1.0,
            "volume_health": 1.0,
        },
    },
    "strategy_profiles": {
        "b1": {
            "label": "B1 回调建仓",
            "pass_min": 4.0,
            "watch_min": 3.3,
            "weights": {
                "trend_structure": 0.20,
                "price_position": 0.30,
                "volume_behavior": 0.25,
                "previous_abnormal_move": 0.15,
                "classic_pattern_match": 0.10,
            },
            "hard_veto_score_max": {
                "price_position": 1.0,
                "volume_behavior": 1.0,
            },
        },
        "b2": {
            "label": "B2 突破确认",
            "pass_min": 4.1,
            "watch_min": 3.4,
            "weights": {
                "trend_structure": 0.15,
                "price_position": 0.20,
                "volume_behavior": 0.35,
                "previous_abnormal_move": 0.15,
                "classic_pattern_match": 0.15,
            },
            "hard_veto_score_max": {
                "volume_behavior": 1.0,
            },
        },
        "brick": {
            "label": "砖形图超短绿转红",
            "pass_min": 4.2,
            "watch_min": 3.5,
            "weights": {
                "trend_structure": 0.10,
                "price_position": 0.25,
                "volume_behavior": 0.20,
                "previous_abnormal_move": 0.05,
                "classic_pattern_match": 0.40,
            },
            "hard_veto_score_max": {
                "price_position": 1.0,
                "volume_behavior": 1.0,
                "classic_pattern_match": 1.0,
            },
        },
    },
}


def _deep_merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, Mapping):
        return deepcopy(override)
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def scoring_config(config: Any = None) -> dict[str, Any]:
    if isinstance(config, Mapping):
        if "review_scoring" in config:
            override = config.get("review_scoring") or {}
        elif any(key in config for key in ("common_gate", "strategy_profiles", "enabled")):
            override = config
        else:
            override = {}
    else:
        override = {}
    if not isinstance(override, Mapping):
        override = {}
    return _deep_merge(DEFAULT_REVIEW_SCORING, override)


def is_composite_strategy(strategy: str) -> bool:
    return any(separator in strategy for separator in ("+", "&", ",", "|"))


def normalized_strategy(strategy: str) -> str:
    return str(strategy or "").strip().lower()


def strategy_profile(strategy: str, config: Any = None) -> dict[str, Any] | None:
    cfg = scoring_config(config)
    if not bool(cfg.get("enabled", True)):
        return None
    normalized = normalized_strategy(strategy)
    if not normalized or is_composite_strategy(normalized):
        return None
    profiles = cfg.get("strategy_profiles") or {}
    profile = profiles.get(normalized)
    return deepcopy(profile) if isinstance(profile, dict) else None


def strategy_thresholds(strategy: str, config: Any = None, fallback: float = 4.0) -> tuple[float, float]:
    profile = strategy_profile(strategy, config)
    if not profile:
        return float(fallback), 3.2
    return float(profile.get("pass_min", fallback)), float(profile.get("watch_min", 3.2))


def numeric_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 5:
        return None
    return score


def weighted_score(scores: Mapping[str, Any], weights: Mapping[str, Any], *, disabled_fields: set[str] | None = None) -> float | None:
    disabled_fields = disabled_fields or set()
    total_weight = 0.0
    total = 0.0
    for key, raw_weight in weights.items():
        if key in disabled_fields:
            continue
        score = numeric_score(scores.get(key))
        if score is None:
            return None
        weight = float(raw_weight)
        if weight <= 0:
            continue
        total_weight += weight
        total += score * weight
    if total_weight <= 0:
        return None
    return total / total_weight


def _score_map(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    raw_scores = value.get("scores") if isinstance(value.get("scores"), Mapping) else value
    if not isinstance(raw_scores, Mapping):
        return {}
    return dict(raw_scores)


def fallback_common_gate_scores(strategy_scores: Mapping[str, Any]) -> dict[str, float]:
    trend = numeric_score(strategy_scores.get("trend_structure")) or 0.0
    position = numeric_score(strategy_scores.get("price_position")) or 0.0
    volume = numeric_score(strategy_scores.get("volume_behavior")) or 0.0
    abnormal = numeric_score(strategy_scores.get("previous_abnormal_move")) or 0.0
    return {
        "trend_qualification": trend,
        "support_stop_loss_control": position,
        "overhead_room": position,
        "volume_health": volume,
        "post_entry_discipline": min(position, volume, max(abnormal, 1.0)),
    }


def normalize_common_gate(result: dict[str, Any], config: Any = None) -> dict[str, Any]:
    cfg = scoring_config(config)
    gate_cfg = cfg.get("common_gate") or {}
    raw_gate = result.get("common_gate")
    raw_scores = _score_map(raw_gate)
    strategy_scores = result.get("scores") if isinstance(result.get("scores"), Mapping) else {}
    if not raw_scores:
        raw_scores = fallback_common_gate_scores(strategy_scores)

    normalized_scores: dict[str, float] = {}
    for key in COMMON_GATE_SCORE_FIELDS:
        score = numeric_score(raw_scores.get(key))
        if score is None:
            score = fallback_common_gate_scores(strategy_scores).get(key, 0.0)
        normalized_scores[key] = float(score)

    gate_score = weighted_score(normalized_scores, gate_cfg.get("weights") or {})
    if gate_score is None:
        gate_score = min(normalized_scores.values()) if normalized_scores else 0.0
    gate_score = round(float(gate_score), 2)

    explicit_veto = False
    explicit_reasons: list[str] = []
    if isinstance(raw_gate, Mapping):
        explicit_veto = bool(raw_gate.get("hard_veto", False))
        reasons = raw_gate.get("hard_veto_reasons") or raw_gate.get("hard_veto_reason") or []
        if isinstance(reasons, str):
            explicit_reasons = [reasons]
        elif isinstance(reasons, list):
            explicit_reasons = [str(item) for item in reasons if str(item)]

    veto_reasons = list(explicit_reasons)
    if explicit_veto and not veto_reasons:
        veto_reasons.append("common_gate.hard_veto")

    hard_fail_below = float(gate_cfg.get("hard_fail_below", 2.0))
    if gate_score < hard_fail_below:
        veto_reasons.append(f"common_gate_score < {hard_fail_below}")

    for field, max_score in (gate_cfg.get("hard_veto_score_max") or {}).items():
        score = normalized_scores.get(str(field))
        if score is not None and score <= float(max_score):
            veto_reasons.append(f"common_gate.{field} <= {float(max_score):g}")

    pass_min = float(gate_cfg.get("pass_min", 3.0))
    watch_min = float(gate_cfg.get("watch_min", 2.4))
    if veto_reasons:
        status = "FAIL"
    elif gate_score >= pass_min:
        status = "PASS"
    elif gate_score >= watch_min:
        status = "WATCH"
    else:
        status = "FAIL"

    comment = ""
    if isinstance(raw_gate, Mapping):
        comment = str(raw_gate.get("comment") or raw_gate.get("reasoning") or "")

    return {
        "enabled": bool(gate_cfg.get("enabled", True)),
        "scores": normalized_scores,
        "score": gate_score,
        "status": status,
        "hard_veto": bool(veto_reasons),
        "hard_veto_reasons": veto_reasons,
        "comment": comment,
        "market_timing": "manual",
    }


def build_prompt_context(strategy: str = "", config: Any = None) -> str:
    profile = strategy_profile(strategy, config)
    pass_min, watch_min = strategy_thresholds(strategy, config)
    cfg = scoring_config(config)
    gate = cfg.get("common_gate") or {}
    strategy_label = profile.get("label") if profile else "通用复评"
    weights = profile.get("weights") if profile else BASE_SCORE_WEIGHTS
    weights_text = "\n".join(f"- {key}: {float(value):.2f}" for key, value in weights.items())

    return (
        "\n\n---\n\n"
        "# 评分体系 V2（本节优先于旧通用权重）\n\n"
        f"本批来源策略：{strategy or '未指定'}；策略评分档案：{strategy_label}。\n\n"
        "## 公共条件 gate\n\n"
        "先判断所有战法共用的交易前提，活跃市值/大盘择时由用户人工确认，本轮不要臆测。\n"
        "公共条件分数越高越好，必须输出 common_gate。明显出货、止损不可控、上方空间不足、白黄线/支撑结构失效时，必须给出硬否决或显著降分。\n\n"
        "common_gate.scores 必须包含：trend_qualification、support_stop_loss_control、overhead_room、volume_health、post_entry_discipline。\n"
        f"公共 gate PASS 门槛 {float(gate.get('pass_min', 3.0)):.2f}，WATCH 门槛 {float(gate.get('watch_min', 2.4)):.2f}；公共 gate 不通过时不能给策略 PASS。\n\n"
        "## 策略专项权重\n\n"
        f"{weights_text}\n\n"
        f"策略 PASS 门槛 {pass_min:.2f}，WATCH 门槛 {watch_min:.2f}。"
        "请按本策略目标解释同名字段，而不是把所有策略都当成波段爆发评分。\n\n"
        "输出中的 total_score/verdict 仍需填写；本地程序会按同一套 profile 重新归一化，模型不得用旧的全局 4.0/3.2 门槛覆盖本节。"
    )
