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


def compute_kdj(frame: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    if frame.empty:
        return frame.assign(K=np.nan, D=np.nan, J=np.nan)

    low_n = frame["low"].rolling(window=n, min_periods=1).min()
    high_n = frame["high"].rolling(window=n, min_periods=1).max()
    rsv = ((frame["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy(dtype=np.float64)

    k, d, j = _kdj_core(rsv)
    return frame.assign(K=k, D=d, J=j)


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


def compute_upper_shadow_ratio(frame: pd.DataFrame) -> np.ndarray:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    span = high - low
    out = np.zeros(len(frame), dtype=float)
    np.divide(high - close, span, out=out, where=span > 0)
    return out


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
