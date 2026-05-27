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


def compute_kdj(frame: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    if frame.empty:
        return frame.assign(K=np.nan, D=np.nan, J=np.nan)

    low_n = frame["low"].rolling(window=n, min_periods=1).min()
    high_n = frame["high"].rolling(window=n, min_periods=1).max()
    rsv = ((frame["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy(dtype=np.float64)

    k, d, j = _kdj_core(rsv)
    return frame.assign(K=k, D=d, J=j)


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
