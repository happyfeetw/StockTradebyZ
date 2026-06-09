from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: Any, digits: int = 3) -> float | None:
    try:
        if pd.isna(value):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def raw_kline_path(raw_dir: str | Path, code: str) -> Path:
    return Path(raw_dir) / f"{str(code).zfill(6)}.csv"


def load_raw_kline(raw_dir: str | Path, code: str, *, pick_date: str = "") -> pd.DataFrame:
    path = raw_kline_path(raw_dir, code)
    if not path.exists():
        raise FileNotFoundError(f"找不到原始K线：{path}")
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"原始K线为空：{path}")

    rename_map = {
        "trade_date": "date",
        "vol": "volume",
    }
    df = df.rename(columns={key: value for key, value in rename_map.items() if key in df.columns})
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"原始K线缺少字段 {sorted(missing)}：{path}")

    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close", "volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    df = df.sort_values("date").reset_index(drop=True)
    if pick_date:
        df = df[df["date"] <= pick_date].reset_index(drop=True)
    if df.empty:
        raise ValueError(f"{code} 在 {pick_date or '当前'} 前没有可用K线")
    return df


def compute_z_features(raw_dir: str | Path, code: str, *, pick_date: str = "", lookback: int = 60) -> dict[str, Any]:
    """从本项目 data/raw K线计算 Z 质量层所需的客观特征。

    只计算本地 OHLCV 能确定的事实；成交额、换手、分时量比等缺失项会显式标记。
    """
    code = str(code).zfill(6)
    try:
        df = load_raw_kline(raw_dir, code, pick_date=pick_date)
    except Exception as exc:  # noqa: BLE001 - callers need a structured limitation.
        return {
            "code": code,
            "data_available": False,
            "data_error": str(exc),
            "data_limitations": ["raw_kline_missing_or_invalid"],
        }

    df = df.copy()
    df["prev_close"] = df["close"].shift(1)
    df["pct_chg"] = (df["close"] / df["prev_close"] - 1.0) * 100.0
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["upper_shadow_pct"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"] * 100.0
    df["lower_shadow_pct"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"] * 100.0
    df["body_pct"] = (df["close"] - df["open"]).abs() / df["close"] * 100.0
    df["is_bullish"] = df["close"] >= df["open"]
    df["is_bearish"] = df["close"] < df["open"]

    today = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    recent = df.tail(max(1, int(lookback)))
    recent20 = df.tail(min(len(df), 20))
    recent60 = df.tail(min(len(df), 60))
    prior = df.iloc[:-1]
    prior20 = prior.tail(min(len(prior), 20))
    prior60 = prior.tail(min(len(prior), 60))

    close = _safe_float(today["close"])
    high_20 = _safe_float(recent20["high"].max())
    low_20 = _safe_float(recent20["low"].min())
    high_60 = _safe_float(recent60["high"].max())
    low_60 = _safe_float(recent60["low"].min())
    prior_high_20 = _safe_float(prior20["high"].max()) if not prior20.empty else 0.0
    prior_high_60 = _safe_float(prior60["high"].max()) if not prior60.empty else 0.0
    vol_ma5 = _safe_float(today.get("vol_ma5"))
    vol_ma20 = _safe_float(today.get("vol_ma20"))
    volume = _safe_float(today["volume"])
    prev_volume = _safe_float(prev["volume"]) if prev is not None else 0.0
    vol_ratio_5 = volume / vol_ma5 if vol_ma5 > 0 else None
    vol_ratio_20 = volume / vol_ma20 if vol_ma20 > 0 else None

    largest_volume_row = recent20.loc[recent20["volume"].idxmax()] if not recent20.empty else today
    largest_volume_direction = "up" if bool(largest_volume_row["is_bullish"]) else "down"
    largest_volume_ratio_to_ma20 = (
        _safe_float(largest_volume_row["volume"]) / _safe_float(largest_volume_row.get("vol_ma20"))
        if _safe_float(largest_volume_row.get("vol_ma20")) > 0
        else None
    )

    supports = [
        _safe_float(today.get("ma20")),
        _safe_float(today.get("ma60")),
        low_20,
        low_60,
    ]
    supports_below = [value for value in supports if value > 0 and value <= close]
    nearest_support = max(supports_below) if supports_below else None
    support_distance_pct = ((close / nearest_support - 1.0) * 100.0) if nearest_support else None
    overhead_room_20_pct = ((high_20 / close - 1.0) * 100.0) if close > 0 and high_20 >= close else 0.0
    overhead_room_60_pct = ((high_60 / close - 1.0) * 100.0) if close > 0 and high_60 >= close else 0.0
    prior_overhead_room_20_pct = ((prior_high_20 / close - 1.0) * 100.0) if close > 0 and prior_high_20 >= close else 0.0
    prior_overhead_room_60_pct = ((prior_high_60 / close - 1.0) * 100.0) if close > 0 and prior_high_60 >= close else 0.0

    pct_chg = _safe_float(today.get("pct_chg"))
    is_volume_expansion = vol_ratio_20 is not None and vol_ratio_20 >= 1.5
    is_volume_shrink = bool(prev_volume > 0 and volume <= prev_volume * 0.8)
    is_pullback_day = bool(pct_chg <= 0.5)
    pullback_shrink = bool(is_pullback_day and (is_volume_shrink or (vol_ratio_20 is not None and vol_ratio_20 <= 0.9)))
    long_upper_shadow = bool(_safe_float(today.get("upper_shadow_pct")) >= 3.0 and _safe_float(today.get("upper_shadow_pct")) >= _safe_float(today.get("body_pct")) * 1.2)
    fangliang_yinxian = bool(today["is_bearish"] and is_volume_expansion)
    high_volume_upper_shadow = bool(long_upper_shadow and is_volume_expansion)
    gap_up = bool(prev is not None and _safe_float(today["low"]) > _safe_float(prev["high"]) * 1.005)
    gap_down = bool(prev is not None and _safe_float(today["high"]) < _safe_float(prev["low"]) * 0.995)
    near_support = bool(support_distance_pct is not None and support_distance_pct <= 5.0)
    overhead_pressure_close = bool(0 < prior_overhead_room_20_pct <= 5.0 and prior_high_20 > close)
    largest_volume_down = bool(largest_volume_direction == "down")
    drawdown_from_20_high_pct = ((close / high_20 - 1.0) * 100.0) if high_20 > 0 else None

    shadow_days = recent20[
        (recent20["upper_shadow_pct"] >= 2.5) | (recent20["lower_shadow_pct"] >= 2.5)
    ]
    high_volume_days = recent20[
        recent20["volume"] >= recent20["vol_ma20"].fillna(recent20["volume"].mean()) * 1.3
    ]
    centipede_like = bool(len(shadow_days) >= 6 and len(high_volume_days) >= 5)

    bullish_volume_days = recent20[(recent20["is_bullish"]) & (recent20["volume"] >= recent20["vol_ma20"].fillna(0) * 1.2)]
    bearish_volume_days = recent20[(recent20["is_bearish"]) & (recent20["volume"] >= recent20["vol_ma20"].fillna(0) * 1.2)]
    if centipede_like:
        breathing_structure = "messy"
    elif len(bullish_volume_days) >= 2 and len(bearish_volume_days) <= 2:
        breathing_structure = "healthy"
    elif len(bearish_volume_days) >= 3:
        breathing_structure = "distribution_risk"
    else:
        breathing_structure = "neutral"

    limitations = []
    if "amount" not in df.columns:
        limitations.append("amount_missing")
    limitations.extend(["turnover_missing", "intraday_volume_ratio_missing"])

    return {
        "code": code,
        "data_available": True,
        "effective_date": str(today["date"]),
        "requested_pick_date": pick_date,
        "rows": int(len(df)),
        "lookback_rows": int(len(recent)),
        "data_limitations": limitations,
        "ohlcv": {
            "open": _round(today["open"]),
            "high": _round(today["high"]),
            "low": _round(today["low"]),
            "close": _round(today["close"]),
            "volume": _round(today["volume"]),
            "prev_close": _round(today.get("prev_close")),
            "pct_chg": _round(today.get("pct_chg")),
            "amount": _round(today.get("amount")) if "amount" in df.columns else None,
        },
        "moving_average": {
            "ma5": _round(today.get("ma5")),
            "ma10": _round(today.get("ma10")),
            "ma20": _round(today.get("ma20")),
            "ma60": _round(today.get("ma60")),
            "close_above_ma20": bool(close >= _safe_float(today.get("ma20"))) if _safe_float(today.get("ma20")) > 0 else None,
            "close_above_ma60": bool(close >= _safe_float(today.get("ma60"))) if _safe_float(today.get("ma60")) > 0 else None,
        },
        "volume": {
            "vol_ratio_5": _round(vol_ratio_5),
            "vol_ratio_20": _round(vol_ratio_20),
            "volume_expansion": bool(is_volume_expansion),
            "volume_shrink_vs_prev": bool(is_volume_shrink),
            "pullback_shrink": bool(pullback_shrink),
            "largest_volume_date_20": str(largest_volume_row["date"]),
            "largest_volume_direction_20": largest_volume_direction,
            "largest_volume_ratio_to_ma20": _round(largest_volume_ratio_to_ma20),
            "largest_volume_down_20": largest_volume_down,
        },
        "price_position": {
            "high_20": _round(high_20),
            "low_20": _round(low_20),
            "high_60": _round(high_60),
            "low_60": _round(low_60),
            "prior_high_20": _round(prior_high_20),
            "prior_high_60": _round(prior_high_60),
            "drawdown_from_20_high_pct": _round(drawdown_from_20_high_pct),
            "overhead_room_20_pct": _round(overhead_room_20_pct),
            "overhead_room_60_pct": _round(overhead_room_60_pct),
            "prior_overhead_room_20_pct": _round(prior_overhead_room_20_pct),
            "prior_overhead_room_60_pct": _round(prior_overhead_room_60_pct),
            "nearest_support": _round(nearest_support),
            "support_distance_pct": _round(support_distance_pct),
            "near_support": near_support,
            "overhead_pressure_close": overhead_pressure_close,
        },
        "candle": {
            "is_bullish": bool(today["is_bullish"]),
            "is_bearish": bool(today["is_bearish"]),
            "body_pct": _round(today.get("body_pct")),
            "upper_shadow_pct": _round(today.get("upper_shadow_pct")),
            "lower_shadow_pct": _round(today.get("lower_shadow_pct")),
            "long_upper_shadow": long_upper_shadow,
            "fangliang_yinxian": fangliang_yinxian,
            "high_volume_upper_shadow": high_volume_upper_shadow,
            "gap_up": gap_up,
            "gap_down": gap_down,
        },
        "structure": {
            "breathing_structure": breathing_structure,
            "centipede_like": centipede_like,
            "bullish_volume_days_20": int(len(bullish_volume_days)),
            "bearish_volume_days_20": int(len(bearish_volume_days)),
            "shadow_days_20": int(len(shadow_days)),
        },
    }
