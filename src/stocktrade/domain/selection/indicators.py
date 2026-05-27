from __future__ import annotations

import numpy as np
import pandas as pd

try:
    from numba import njit as _njit
except ImportError:

    def _njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda func: func


@_njit(cache=True)
def _kdj_core(rsv: np.ndarray) -> tuple:
    n = len(rsv)
    k = np.empty(n, dtype=np.float64)
    d = np.empty(n, dtype=np.float64)
    k[0] = d[0] = 50.0
    for i in range(1, n):
        k[i] = 2.0 / 3.0 * k[i - 1] + 1.0 / 3.0 * rsv[i]
        d[i] = 2.0 / 3.0 * d[i - 1] + 1.0 / 3.0 * k[i]
    j = 3.0 * k - 2.0 * d
    return k, d, j


@_njit(cache=True)
def _max_volume_not_bearish_core(
    volume: np.ndarray,
    open_: np.ndarray,
    close: np.ndarray,
    lookback: int,
) -> np.ndarray:
    length = len(volume)
    mask = np.zeros(length, dtype=np.bool_)
    for i in range(length):
        start = max(0, i - lookback + 1)
        max_volume = volume[start]
        max_index = start
        for j in range(start + 1, i + 1):
            if volume[j] > max_volume:
                max_volume = volume[j]
                max_index = j
        mask[i] = close[max_index] >= open_[max_index]
    return mask


@_njit(cache=True)
def _compute_brick_core(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    n: int,
    m1: int,
    m2: int,
    m3: int,
    t: float,
    shift1: float,
    shift2: float,
    sma_w1: int,
    sma_w2: int,
    sma_w3: int,
) -> np.ndarray:
    length = len(close)
    hhv = np.empty(length, dtype=np.float64)
    llv = np.empty(length, dtype=np.float64)
    for i in range(length):
        start = max(0, i - n + 1)
        h_max = high[start]
        l_min = low[start]
        for j in range(start + 1, i + 1):
            if high[j] > h_max:
                h_max = high[j]
            if low[j] < l_min:
                l_min = low[j]
        hhv[i] = h_max
        llv[i] = l_min

    a1 = sma_w1 / m1
    b1 = 1.0 - a1
    var2a = np.empty(length, dtype=np.float64)
    for i in range(length):
        rng = hhv[i] - llv[i]
        if rng == 0.0:
            rng = 0.01
        v1 = (hhv[i] - close[i]) / rng * 100.0 - shift1
        if i == 0:
            var2a[i] = v1 + shift2
        else:
            var2a[i] = a1 * v1 + b1 * (var2a[i - 1] - shift2) + shift2

    a2 = sma_w2 / m2
    b2 = 1.0 - a2
    a3 = sma_w3 / m3
    b3 = 1.0 - a3
    var4a = np.empty(length, dtype=np.float64)
    var5a = np.empty(length, dtype=np.float64)
    for i in range(length):
        rng = hhv[i] - llv[i]
        if rng == 0.0:
            rng = 0.01
        v3 = (close[i] - llv[i]) / rng * 100.0
        if i == 0:
            var4a[i] = v3
            var5a[i] = v3 + shift2
        else:
            var4a[i] = a2 * v3 + b2 * var4a[i - 1]
            var5a[i] = a3 * var4a[i] + b3 * (var5a[i - 1] - shift2) + shift2

    raw = np.empty(length, dtype=np.float64)
    for i in range(length):
        diff = var5a[i] - var2a[i]
        raw[i] = diff - t if diff > t else 0.0

    brick = np.empty(length, dtype=np.float64)
    brick[0] = 0.0
    for i in range(1, length):
        brick[i] = raw[i] - raw[i - 1]
    return brick


@_njit(cache=True)
def _brick_green_run_core(brick_values: np.ndarray) -> np.ndarray:
    length = len(brick_values)
    out = np.zeros(length, dtype=np.int32)
    for i in range(1, length):
        if brick_values[i - 1] < 0.0:
            out[i] = out[i - 1] + 1
        else:
            out[i] = 0
    return out


def compute_kdj(frame: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    if frame.empty:
        return frame.assign(K=np.nan, D=np.nan, J=np.nan)

    low_n = frame["low"].rolling(window=n, min_periods=1).min()
    high_n = frame["high"].rolling(window=n, min_periods=1).max()
    rsv = ((frame["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy(dtype=np.float64)

    k, d, j = _kdj_core(rsv)
    return frame.assign(K=k, D=d, J=j)


def compute_kdj_quantile_mask(
    j_values: pd.Series | np.ndarray,
    *,
    j_threshold: float = -5.0,
    j_q_threshold: float = 0.10,
) -> np.ndarray:
    j_series = (
        j_values.astype(float)
        if isinstance(j_values, pd.Series)
        else pd.Series(np.asarray(j_values, dtype=float))
    )
    j_array = j_series.to_numpy(dtype=float)
    quantile = j_series.expanding(min_periods=1).quantile(j_q_threshold).to_numpy(dtype=float)
    return (j_array < j_threshold) | (j_array <= quantile)


def compute_brick_values(
    frame: pd.DataFrame,
    *,
    n: int = 4,
    m1: int = 4,
    m2: int = 6,
    m3: int = 6,
    t: float = 4.0,
    shift1: float = 90.0,
    shift2: float = 100.0,
    sma_w1: int = 1,
    sma_w2: int = 1,
    sma_w3: int = 1,
) -> np.ndarray:
    return _compute_brick_core(
        frame["high"].to_numpy(dtype=np.float64),
        frame["low"].to_numpy(dtype=np.float64),
        frame["close"].to_numpy(dtype=np.float64),
        n,
        m1,
        m2,
        m3,
        float(t),
        float(shift1),
        float(shift2),
        sma_w1,
        sma_w2,
        sma_w3,
    )


def compute_brick_chart(
    frame: pd.DataFrame,
    *,
    n: int = 4,
    m1: int = 4,
    m2: int = 6,
    m3: int = 6,
    t: float = 4.0,
    shift1: float = 90.0,
    shift2: float = 100.0,
    sma_w1: int = 1,
    sma_w2: int = 1,
    sma_w3: int = 1,
) -> pd.Series:
    values = compute_brick_values(
        frame,
        n=n,
        m1=m1,
        m2=m2,
        m3=m3,
        t=t,
        shift1=shift1,
        shift2=shift2,
        sma_w1=sma_w1,
        sma_w2=sma_w2,
        sma_w3=sma_w3,
    )
    return pd.Series(values, index=frame.index, name="brick")


def compute_brick_green_run(brick_values: np.ndarray) -> np.ndarray:
    return _brick_green_run_core(brick_values.astype(np.float64))


def compute_brick_growth(brick_values: np.ndarray) -> np.ndarray:
    previous = np.empty_like(brick_values, dtype=float)
    previous[0] = np.nan
    previous[1:] = brick_values[:-1]
    previous_abs = np.abs(previous)
    safe = np.where(previous_abs > 0, previous_abs, 1.0)
    return np.where(previous_abs > 0, brick_values / safe, brick_values)


def compute_brick_pattern_mask(
    frame: pd.DataFrame,
    brick_values: np.ndarray,
    *,
    daily_return_threshold: float = 0.05,
    brick_growth_ratio: float = 1.0,
    min_prior_green_bars: int = 1,
) -> np.ndarray:
    close = frame["close"].to_numpy(dtype=float)

    previous_brick = np.empty_like(brick_values, dtype=float)
    previous_brick[0] = np.nan
    previous_brick[1:] = brick_values[:-1]

    previous_close = np.empty_like(close)
    previous_close[0] = np.nan
    previous_close[1:] = close[:-1]

    previous_abs = np.abs(previous_brick)
    cond_ret = (close / previous_close - 1.0) < daily_return_threshold
    cond_red = brick_values > 0
    cond_green = previous_brick < 0
    cond_growth = brick_values >= brick_growth_ratio * previous_abs

    if min_prior_green_bars <= 1:
        cond_green_count = cond_green
    else:
        green_run = compute_brick_green_run(brick_values)
        cond_green_count = cond_green & (green_run >= min_prior_green_bars)

    return cond_ret & cond_red & cond_green_count & cond_growth


def compute_daily_return(frame: pd.DataFrame) -> np.ndarray:
    close = frame["close"].to_numpy(dtype=float)
    prev_close = np.empty_like(close)
    prev_close[0] = np.nan
    prev_close[1:] = close[:-1]
    out = np.full(len(close), np.nan, dtype=float)
    np.divide(close, prev_close, out=out, where=prev_close > 0)
    return out - 1.0


def compute_body_pct(frame: pd.DataFrame) -> np.ndarray:
    open_ = frame["open"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    out = np.full(len(frame), np.nan, dtype=float)
    np.divide(close - open_, open_, out=out, where=open_ > 0)
    return out


def compute_max_volume_not_bearish(frame: pd.DataFrame, lookback: int = 20) -> np.ndarray:
    return _max_volume_not_bearish_core(
        frame["volume"].to_numpy(dtype=np.float64),
        frame["open"].to_numpy(dtype=np.float64),
        frame["close"].to_numpy(dtype=np.float64),
        lookback,
    )


def compute_b1_pick_mask(
    frame: pd.DataFrame,
    *,
    j_threshold: float = -5.0,
    j_q_threshold: float = 0.10,
    require_close_gt_long: bool = True,
    require_short_gt_long: bool = True,
    max_vol_lookback: int | None = 20,
) -> np.ndarray:
    mask = (
        compute_kdj_quantile_mask(
            frame["J"],
            j_threshold=j_threshold,
            j_q_threshold=j_q_threshold,
        )
        & compute_zx_condition_mask(
            frame,
            frame["zxdq"],
            frame["zxdkx"],
            require_close_gt_long=require_close_gt_long,
            require_short_gt_long=require_short_gt_long,
        )
        & frame["wma_bull"].to_numpy(dtype=bool)
    )
    if max_vol_lookback is not None:
        mask &= compute_max_volume_not_bearish(frame, lookback=max_vol_lookback)
    return mask


def compute_upper_shadow_ratio(frame: pd.DataFrame) -> np.ndarray:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    span = high - low
    out = np.zeros(len(frame), dtype=float)
    np.divide(high - close, span, out=out, where=span > 0)
    return out


def compute_b2_quality_score(
    frame: pd.DataFrame,
    *,
    upper_shadow_soft_limit: float = 0.15,
) -> np.ndarray:
    score = np.full(len(frame), 100.0, dtype=float)
    j_values = frame["J"].to_numpy(dtype=float)
    prior_j = frame["_b2_prior_b1_j"].to_numpy(dtype=float)
    j_delta = j_values - prior_j
    volume_ratio = frame["_b2_volume_ratio"].to_numpy(dtype=float)
    body_pct = frame["_b2_today_body_pct"].to_numpy(dtype=float)
    upper_shadow = frame["_b2_upper_shadow_ratio"].to_numpy(dtype=float)

    score += np.where(j_values < 25.0, 5.0, 0.0)
    score += np.where((j_values >= 45.0) & (j_values < 55.0), -5.0, 0.0)
    score += np.where(j_delta >= 10.0, 5.0, 0.0)
    score += np.where(volume_ratio > 1.2, 8.0, np.where(volume_ratio > 1.0, 3.0, 0.0))
    score += np.where(body_pct >= 0.03, 5.0, np.where(body_pct < 0.01, -5.0, 0.0))
    score += np.where(upper_shadow <= 0.03, 5.0, 0.0)
    score += np.where(upper_shadow > upper_shadow_soft_limit, -10.0, 0.0)
    return score


def compute_b2_pick_mask(
    frame: pd.DataFrame,
    *,
    require_j_turn_up: bool = True,
    j_ceiling: float = 55.0,
    min_return: float = 0.04,
    return_tolerance: float = 1e-12,
    min_today_body_pct: float = 0.003,
    volume_ratio_min: float = 1.0,
    flat_volume_ratio: float = 0.98,
) -> np.ndarray:
    current_zx_ok = compute_zx_condition_mask(frame, frame["zxdq"], frame["zxdkx"])
    current_weekly_ok = frame["wma_bull"].to_numpy(dtype=bool)
    prior_lag = frame["_b2_prior_b1_lag"].to_numpy(dtype=np.int16)
    recent_b1_ok = prior_lag > 0

    if require_j_turn_up:
        if "_b2_j_turn_up" in frame.columns:
            j_turn_up_ok = frame["_b2_j_turn_up"].to_numpy(dtype=bool)
        else:
            j_turn_up_ok = (
                frame["J"].to_numpy(dtype=float)
                > frame["_b2_prior_b1_j"].to_numpy(dtype=float)
            )
    else:
        j_turn_up_ok = np.ones(len(frame), dtype=bool)

    j_ceiling_ok = frame["J"].to_numpy(dtype=float) < j_ceiling
    daily_return_ok = (
        frame["_b2_daily_return"].to_numpy(dtype=float)
        >= min_return - return_tolerance
    )
    open_ = frame["open"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    body_ok = (
        (close > open_)
        & (frame["_b2_today_body_pct"].to_numpy(dtype=float) >= min_today_body_pct)
    )
    volume_ratio = frame["_b2_volume_ratio"].to_numpy(dtype=float)
    strict_yang_bao_yin = frame["_b2_strict_yang_bao_yin"].to_numpy(dtype=bool)
    volume_ok = (
        (volume_ratio > volume_ratio_min)
        | ((volume_ratio >= flat_volume_ratio) & strict_yang_bao_yin)
    )

    return (
        current_zx_ok
        & current_weekly_ok
        & recent_b1_ok
        & j_turn_up_ok
        & j_ceiling_ok
        & daily_return_ok
        & body_ok
        & volume_ok
    )


def compute_volume_ratio(frame: pd.DataFrame) -> np.ndarray:
    volume = frame["volume"].to_numpy(dtype=float)
    prev_volume = np.empty_like(volume)
    prev_volume[0] = np.nan
    prev_volume[1:] = volume[:-1]
    out = np.full(len(frame), np.nan, dtype=float)
    np.divide(volume, prev_volume, out=out, where=prev_volume > 0)
    return out


def compute_strict_yang_bao_yin(
    frame: pd.DataFrame,
    *,
    min_today_body_pct: float = 0.003,
    min_yang_bao_yin_body_pct: float = 0.003,
) -> np.ndarray:
    open_ = frame["open"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)

    prev_open = np.empty_like(open_)
    prev_close = np.empty_like(close)
    prev_open[0] = np.nan
    prev_close[0] = np.nan
    prev_open[1:] = open_[:-1]
    prev_close[1:] = close[:-1]

    prev_body_pct = np.full(len(frame), np.nan, dtype=float)
    np.divide(prev_open - prev_close, prev_close, out=prev_body_pct, where=prev_close > 0)

    today_body_pct = np.full(len(frame), np.nan, dtype=float)
    np.divide(close - open_, open_, out=today_body_pct, where=open_ > 0)

    prev_bear_body_ok = (
        (prev_close < prev_open)
        & (prev_body_pct >= min_yang_bao_yin_body_pct)
    )
    today_bull_body_ok = (
        (close > open_)
        & (today_body_pct >= min_today_body_pct)
    )
    return (
        prev_bear_body_ok
        & today_bull_body_ok
        & (open_ <= prev_close)
        & (close >= prev_open)
    )


def compute_recent_b1_prior_lag(
    frame: pd.DataFrame,
    *,
    lookback: int = 2,
    pick_column: str = "_b1_pick",
) -> np.ndarray:
    if pick_column not in frame.columns:
        raise KeyError("RecentB1PickFilter requires precomputed '_b1_pick'.")
    b1_pick = frame[pick_column].to_numpy(dtype=bool)
    out = np.zeros(len(frame), dtype=np.int16)
    for lag in range(1, lookback + 1):
        shifted = np.zeros(len(frame), dtype=bool)
        shifted[lag:] = b1_pick[:-lag]
        fill = (out == 0) & shifted
        out[fill] = lag
    return out


def compute_recent_b1_prior_j(
    frame: pd.DataFrame,
    prior_lag: np.ndarray,
    *,
    lookback: int = 2,
    j_column: str = "J",
) -> np.ndarray:
    if j_column not in frame.columns:
        raise KeyError("RecentB1PickFilter requires precomputed 'J'.")
    j_values = frame[j_column].to_numpy(dtype=float)
    out = np.full(len(frame), np.nan, dtype=float)
    for lag in range(1, lookback + 1):
        mask = prior_lag == lag
        shifted_j = np.full(len(frame), np.nan, dtype=float)
        shifted_j[lag:] = j_values[:-lag]
        out[mask] = shifted_j[mask]
    return out


def compute_zx_lines(
    frame: pd.DataFrame,
    m1: int = 14,
    m2: int = 28,
    m3: int = 57,
    m4: int = 114,
    zxdq_span: int = 10,
) -> tuple[pd.Series, pd.Series]:
    close = frame["close"].astype(float)
    zxdq = close.ewm(span=zxdq_span, adjust=False).mean().ewm(span=zxdq_span, adjust=False).mean()
    zxdkx = (
        close.rolling(m1, min_periods=m1).mean()
        + close.rolling(m2, min_periods=m2).mean()
        + close.rolling(m3, min_periods=m3).mean()
        + close.rolling(m4, min_periods=m4).mean()
    ) / 4.0
    return zxdq, zxdkx


def compute_zxdq_ratio_mask(
    frame: pd.DataFrame,
    zxdq_values: np.ndarray | pd.Series,
    *,
    zxdq_ratio: float = 1.0,
) -> np.ndarray:
    zxdq = np.asarray(zxdq_values, dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    return (
        np.isfinite(zxdq)
        & (zxdq > 0)
        & (close < zxdq * zxdq_ratio)
    )


def compute_zx_condition_mask(
    frame: pd.DataFrame,
    zxdq_values: np.ndarray | pd.Series,
    zxdkx_values: np.ndarray | pd.Series,
    *,
    require_close_gt_long: bool = True,
    require_short_gt_long: bool = True,
) -> np.ndarray:
    zxdq = np.asarray(zxdq_values, dtype=float)
    zxdkx = np.asarray(zxdkx_values, dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    mask = np.isfinite(zxdq) & np.isfinite(zxdkx)
    if require_close_gt_long:
        mask &= close > zxdkx
    if require_short_gt_long:
        mask &= zxdq > zxdkx
    return mask


def compute_weekly_close(frame: pd.DataFrame) -> pd.Series:
    close = (
        frame["close"].astype(float)
        if isinstance(frame.index, pd.DatetimeIndex)
        else frame.set_index("date")["close"].astype(float)
    )
    idx = close.index
    iso_calendar = idx.isocalendar()
    year_week = (
        iso_calendar.year.astype(str)
        + "-"
        + iso_calendar.week.astype(str).str.zfill(2)
    )
    weekly = close.groupby(year_week).last()
    last_date_per_week = close.groupby(year_week).apply(lambda series: series.index[-1])
    weekly.index = pd.DatetimeIndex(last_date_per_week.values)
    return weekly.dropna()


def compute_weekly_ma_bull(
    frame: pd.DataFrame,
    ma_periods: tuple[int, int, int] = (20, 60, 120),
) -> pd.Series:
    weekly_close = compute_weekly_close(frame)
    short_period, mid_period, long_period = ma_periods
    ma_short = weekly_close.rolling(short_period, min_periods=short_period).mean()
    ma_mid = weekly_close.rolling(mid_period, min_periods=mid_period).mean()
    ma_long = weekly_close.rolling(long_period, min_periods=long_period).mean()
    bull = (ma_short > ma_mid) & (ma_mid > ma_long)

    daily_index = (
        frame.index
        if isinstance(frame.index, pd.DatetimeIndex)
        else pd.DatetimeIndex(frame["date"])
    )
    return bull.astype(float).reindex(daily_index).ffill().fillna(0.0).astype(bool)
