from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "src"))

import Selector as selector_module  # noqa: E402
from stocktrade.domain.selection import (  # noqa: E402
    compute_body_pct,
    compute_brick_chart,
    compute_brick_green_run,
    compute_brick_growth,
    compute_brick_pattern_mask,
    compute_brick_values,
    compute_daily_return,
    compute_kdj,
    compute_max_volume_not_bearish,
    compute_recent_b1_prior_j,
    compute_recent_b1_prior_lag,
    compute_strict_yang_bao_yin,
    compute_upper_shadow_ratio,
    compute_volume_ratio,
    compute_weekly_close,
    compute_weekly_ma_bull,
    compute_zx_lines,
    compute_zxdq_ratio_mask,
)


def reference_compute_kdj(frame: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    if frame.empty:
        return frame.assign(K=np.nan, D=np.nan, J=np.nan)

    low_n = frame["low"].rolling(window=n, min_periods=1).min()
    high_n = frame["high"].rolling(window=n, min_periods=1).max()
    rsv = ((frame["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy(dtype=np.float64)

    k = np.empty(len(rsv), dtype=np.float64)
    d = np.empty(len(rsv), dtype=np.float64)
    k[0] = d[0] = 50.0
    for i in range(1, len(rsv)):
        k[i] = 2.0 / 3.0 * k[i - 1] + 1.0 / 3.0 * rsv[i]
        d[i] = 2.0 / 3.0 * d[i - 1] + 1.0 / 3.0 * k[i]
    j = 3.0 * k - 2.0 * d
    return frame.assign(K=k, D=d, J=j)


def reference_compute_brick_values(
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
    high = frame["high"].to_numpy(dtype=np.float64)
    low = frame["low"].to_numpy(dtype=np.float64)
    close = frame["close"].to_numpy(dtype=np.float64)

    hhv = np.empty(len(frame), dtype=np.float64)
    llv = np.empty(len(frame), dtype=np.float64)
    for i in range(len(frame)):
        start = max(0, i - n + 1)
        hhv[i] = high[start : i + 1].max()
        llv[i] = low[start : i + 1].min()

    a1 = sma_w1 / m1
    b1 = 1.0 - a1
    var2a = np.empty(len(frame), dtype=np.float64)
    for i in range(len(frame)):
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
    var4a = np.empty(len(frame), dtype=np.float64)
    var5a = np.empty(len(frame), dtype=np.float64)
    for i in range(len(frame)):
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

    raw = np.empty(len(frame), dtype=np.float64)
    for i in range(len(frame)):
        diff = var5a[i] - var2a[i]
        raw[i] = diff - t if diff > t else 0.0

    brick = np.empty(len(frame), dtype=np.float64)
    brick[0] = 0.0
    for i in range(1, len(frame)):
        brick[i] = raw[i] - raw[i - 1]
    return brick


def reference_compute_brick_green_run(brick_values: np.ndarray) -> np.ndarray:
    out = np.zeros(len(brick_values), dtype=np.int32)
    for i in range(1, len(brick_values)):
        if brick_values[i - 1] < 0.0:
            out[i] = out[i - 1] + 1
        else:
            out[i] = 0
    return out


def reference_compute_brick_growth(brick_values: np.ndarray) -> np.ndarray:
    previous = np.empty_like(brick_values, dtype=float)
    previous[0] = np.nan
    previous[1:] = brick_values[:-1]
    previous_abs = np.abs(previous)
    safe = np.where(previous_abs > 0, previous_abs, 1.0)
    return np.where(previous_abs > 0, brick_values / safe, brick_values)


def reference_compute_brick_pattern_mask(
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
        green_run = reference_compute_brick_green_run(brick_values)
        cond_green_count = cond_green & (green_run >= min_prior_green_bars)
    return cond_ret & cond_red & cond_green_count & cond_growth


def reference_compute_max_volume_not_bearish(frame: pd.DataFrame, lookback: int = 20) -> np.ndarray:
    volume = frame["volume"].to_numpy(dtype=np.float64)
    open_ = frame["open"].to_numpy(dtype=np.float64)
    close = frame["close"].to_numpy(dtype=np.float64)
    mask = np.zeros(len(frame), dtype=np.bool_)
    for i in range(len(frame)):
        start = max(0, i - lookback + 1)
        max_volume = volume[start]
        max_index = start
        for j in range(start + 1, i + 1):
            if volume[j] > max_volume:
                max_volume = volume[j]
                max_index = j
        mask[i] = close[max_index] >= open_[max_index]
    return mask


def reference_compute_daily_return(frame: pd.DataFrame) -> np.ndarray:
    close = frame["close"].to_numpy(dtype=float)
    prev_close = np.empty_like(close)
    prev_close[0] = np.nan
    prev_close[1:] = close[:-1]
    out = np.full(len(close), np.nan, dtype=float)
    np.divide(close, prev_close, out=out, where=prev_close > 0)
    return out - 1.0


def reference_compute_body_pct(frame: pd.DataFrame) -> np.ndarray:
    open_ = frame["open"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    out = np.full(len(frame), np.nan, dtype=float)
    np.divide(close - open_, open_, out=out, where=open_ > 0)
    return out


def reference_compute_upper_shadow_ratio(frame: pd.DataFrame) -> np.ndarray:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    span = high - low
    out = np.zeros(len(frame), dtype=float)
    np.divide(high - close, span, out=out, where=span > 0)
    return out


def reference_compute_volume_ratio(frame: pd.DataFrame) -> np.ndarray:
    volume = frame["volume"].to_numpy(dtype=float)
    prev_volume = np.empty_like(volume)
    prev_volume[0] = np.nan
    prev_volume[1:] = volume[:-1]
    out = np.full(len(frame), np.nan, dtype=float)
    np.divide(volume, prev_volume, out=out, where=prev_volume > 0)
    return out


def reference_compute_strict_yang_bao_yin(
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


def reference_compute_recent_b1_prior_lag(frame: pd.DataFrame, lookback: int = 2) -> np.ndarray:
    b1_pick = frame["_b1_pick"].to_numpy(dtype=bool)
    out = np.zeros(len(frame), dtype=np.int16)
    for lag in range(1, lookback + 1):
        shifted = np.zeros(len(frame), dtype=bool)
        shifted[lag:] = b1_pick[:-lag]
        fill = (out == 0) & shifted
        out[fill] = lag
    return out


def reference_compute_recent_b1_prior_j(
    frame: pd.DataFrame,
    prior_lag: np.ndarray,
    lookback: int = 2,
) -> np.ndarray:
    j_values = frame["J"].to_numpy(dtype=float)
    out = np.full(len(frame), np.nan, dtype=float)
    for lag in range(1, lookback + 1):
        mask = prior_lag == lag
        shifted_j = np.full(len(frame), np.nan, dtype=float)
        shifted_j[lag:] = j_values[:-lag]
        out[mask] = shifted_j[mask]
    return out


def reference_compute_zx_lines(
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


def reference_compute_zxdq_ratio_mask(
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


def reference_compute_weekly_close(frame: pd.DataFrame) -> pd.Series:
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


def reference_compute_weekly_ma_bull(
    frame: pd.DataFrame,
    ma_periods: tuple[int, int, int] = (20, 60, 120),
) -> pd.Series:
    weekly_close = reference_compute_weekly_close(frame)
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


class SelectionIndicatorContractTests(unittest.TestCase):
    def test_product_kdj_matches_reference_formula(self) -> None:
        frame = pd.DataFrame(
            {
                "high": [10.0, 12.0, 12.0, 15.0, 16.0],
                "low": [9.0, 9.5, 10.0, 11.0, 12.0],
                "close": [9.5, 11.0, 10.5, 14.0, 15.0],
            },
            index=pd.to_datetime(["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]),
        )

        actual = compute_kdj(frame, n=3)
        expected = reference_compute_kdj(frame, n=3)

        pd.testing.assert_frame_equal(actual, expected)

    def test_product_kdj_preserves_empty_frame_contract(self) -> None:
        frame = pd.DataFrame(columns=["high", "low", "close"])

        actual = compute_kdj(frame)

        self.assertEqual(actual.columns.tolist(), ["high", "low", "close", "K", "D", "J"])
        self.assertTrue(actual.empty)

    def test_product_brick_values_match_reference_formula(self) -> None:
        frame = pd.DataFrame(
            {
                "high": [10.0, 12.0, 11.0, 14.0, 13.5, 15.0, 14.5],
                "low": [9.0, 10.0, 9.5, 11.0, 10.5, 12.0, 11.5],
                "close": [9.5, 11.5, 10.0, 13.0, 11.0, 14.0, 12.5],
            },
            index=pd.to_datetime(
                ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13"]
            ),
        )

        actual = compute_brick_values(frame, n=3, m1=4, m2=5, m3=6, t=3.0)
        expected = reference_compute_brick_values(frame, n=3, m1=4, m2=5, m3=6, t=3.0)

        np.testing.assert_allclose(actual, expected)

    def test_product_brick_chart_returns_named_series_with_original_index(self) -> None:
        frame = pd.DataFrame(
            {
                "high": [10.0, 12.0, 11.0, 14.0],
                "low": [9.0, 10.0, 9.5, 11.0],
                "close": [9.5, 11.5, 10.0, 13.0],
            },
            index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]),
        )

        actual = compute_brick_chart(frame, n=2, m1=4, m2=5, m3=6, t=3.0)
        expected = pd.Series(
            reference_compute_brick_values(frame, n=2, m1=4, m2=5, m3=6, t=3.0),
            index=frame.index,
            name="brick",
        )

        pd.testing.assert_series_equal(actual, expected)

    def test_product_brick_green_run_matches_reference_formula(self) -> None:
        brick_values = np.array([0.0, -1.0, -2.0, 3.0, -1.0, -2.0, 4.0])

        actual = compute_brick_green_run(brick_values)
        expected = reference_compute_brick_green_run(brick_values)

        np.testing.assert_array_equal(actual, expected)

    def test_product_brick_growth_matches_reference_formula(self) -> None:
        brick_values = np.array([0.0, -1.0, -2.0, 3.0, 0.0, 4.0])

        actual = compute_brick_growth(brick_values)
        expected = reference_compute_brick_growth(brick_values)

        np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_product_brick_pattern_mask_matches_reference_formula(self) -> None:
        frame = pd.DataFrame({"close": [10.0, 9.8, 9.6, 9.9, 10.0, 10.2, 10.0]})
        brick_values = np.array([0.0, -1.0, -2.0, 2.5, -1.0, -2.0, 3.0])

        actual = compute_brick_pattern_mask(
            frame,
            brick_values,
            daily_return_threshold=0.05,
            brick_growth_ratio=1.0,
            min_prior_green_bars=2,
        )
        expected = reference_compute_brick_pattern_mask(
            frame,
            brick_values,
            daily_return_threshold=0.05,
            brick_growth_ratio=1.0,
            min_prior_green_bars=2,
        )

        np.testing.assert_array_equal(actual, expected)

    def test_legacy_selector_brick_helpers_delegate_to_product_helpers_when_available(self) -> None:
        frame = pd.DataFrame(
            {
                "high": [10.0, 12.0, 11.0],
                "low": [9.0, 10.0, 9.5],
                "close": [9.5, 11.5, 10.0],
            }
        )

        def fake_product_compute_brick_chart(input_frame: pd.DataFrame, **kwargs: float) -> pd.Series:
            return pd.Series([float(kwargs["n"]), float(kwargs["m1"]), float(kwargs["t"])], index=input_frame.index)

        def fake_product_compute_brick_values(input_frame: pd.DataFrame, **kwargs: float) -> np.ndarray:
            return np.array([float(kwargs["m2"]), float(kwargs["m3"]), float(kwargs["sma_w1"])])

        with (
            patch.object(selector_module, "_product_compute_brick_chart", fake_product_compute_brick_chart),
            patch.object(selector_module, "_product_compute_brick_values", fake_product_compute_brick_values),
        ):
            series = selector_module.compute_brick_chart(frame, n=2, m1=3, m2=4, m3=5, t=6.0)
            values = selector_module.BrickComputeParams(n=2, m1=3, m2=4, m3=5).compute_arr(frame)

        self.assertEqual(series.tolist(), [2.0, 3.0, 6.0])
        np.testing.assert_allclose(values, np.array([4.0, 5.0, 1.0]))

    def test_legacy_selector_brick_pattern_helpers_delegate_to_product_helpers_when_available(self) -> None:
        frame = pd.DataFrame(
            {
                "close": [10.0, 9.8, 9.9],
                "brick": [0.0, -1.0, 2.0],
            }
        )

        def fake_product_compute_brick_pattern_mask(
            input_frame: pd.DataFrame,
            brick_values: np.ndarray,
            **kwargs: float,
        ) -> np.ndarray:
            self.assertEqual(kwargs["min_prior_green_bars"], 2)
            np.testing.assert_allclose(brick_values, np.array([0.0, -1.0, 2.0]))
            return np.array([False, False, True], dtype=np.bool_)

        def fake_product_compute_brick_growth(brick_values: np.ndarray) -> np.ndarray:
            np.testing.assert_allclose(brick_values, np.array([0.0, -1.0, 2.0]))
            return np.array([0.0, -1.0, 2.0])

        with (
            patch.object(
                selector_module,
                "_product_compute_brick_pattern_mask",
                fake_product_compute_brick_pattern_mask,
            ),
            patch.object(selector_module, "_product_compute_brick_growth", fake_product_compute_brick_growth),
        ):
            filter_ = selector_module.BrickPatternFilter(min_prior_green_bars=2)
            mask = filter_.vec_mask(frame)
            growth = filter_.brick_growth_arr(frame)

        np.testing.assert_array_equal(mask, np.array([False, False, True], dtype=np.bool_))
        np.testing.assert_allclose(growth, np.array([0.0, -1.0, 2.0]))

    def test_legacy_selector_compute_kdj_delegates_to_product_helper_when_available(self) -> None:
        frame = pd.DataFrame({"high": [1.0], "low": [1.0], "close": [1.0]})

        def fake_product_compute_kdj(input_frame: pd.DataFrame, n: int = 9) -> pd.DataFrame:
            return input_frame.assign(K=float(n), D=2.0, J=3.0)

        with patch.object(selector_module, "_product_compute_kdj", fake_product_compute_kdj):
            actual = selector_module.compute_kdj(frame, n=7)

        self.assertEqual(actual["K"].tolist(), [7.0])
        self.assertEqual(actual["D"].tolist(), [2.0])
        self.assertEqual(actual["J"].tolist(), [3.0])

    def test_product_max_volume_not_bearish_matches_reference_formula(self) -> None:
        frame = pd.DataFrame(
            {
                "volume": [100.0, 200.0, 150.0, 250.0, 240.0, 260.0],
                "open": [10.0, 12.0, 11.0, 15.0, 16.0, 14.0],
                "close": [11.0, 11.5, 12.0, 14.0, 17.0, 14.5],
            }
        )

        actual = compute_max_volume_not_bearish(frame, lookback=3)
        expected = reference_compute_max_volume_not_bearish(frame, lookback=3)

        np.testing.assert_array_equal(actual, expected)

    def test_legacy_max_volume_filter_delegates_to_product_helper_when_available(self) -> None:
        frame = pd.DataFrame(
            {
                "volume": [100.0, 200.0, 150.0],
                "open": [10.0, 11.0, 12.0],
                "close": [10.5, 10.0, 12.5],
            }
        )

        def fake_product_compute_max_volume_not_bearish(
            input_frame: pd.DataFrame,
            lookback: int = 20,
        ) -> np.ndarray:
            self.assertEqual(lookback, 2)
            return np.array([True, False, True], dtype=np.bool_)

        with patch.object(
            selector_module,
            "_product_compute_max_volume_not_bearish",
            fake_product_compute_max_volume_not_bearish,
        ):
            actual = selector_module.MaxVolNotBearishFilter(n=2).vec_mask(frame)

        np.testing.assert_array_equal(actual, np.array([True, False, True], dtype=np.bool_))

    def test_product_daily_return_matches_reference_formula(self) -> None:
        frame = pd.DataFrame({"close": [100.0, 104.0, 0.0, 10.0, 11.0]})

        actual = compute_daily_return(frame)
        expected = reference_compute_daily_return(frame)

        np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_product_body_pct_matches_reference_formula(self) -> None:
        frame = pd.DataFrame({"open": [10.0, 0.0, 12.0], "close": [11.0, 5.0, 11.5]})

        actual = compute_body_pct(frame)
        expected = reference_compute_body_pct(frame)

        np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_product_upper_shadow_ratio_matches_reference_formula(self) -> None:
        frame = pd.DataFrame(
            {
                "high": [12.0, 10.0, 15.0],
                "low": [10.0, 10.0, 12.0],
                "close": [11.0, 9.5, 14.0],
            }
        )

        actual = compute_upper_shadow_ratio(frame)
        expected = reference_compute_upper_shadow_ratio(frame)

        np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_legacy_b2_price_action_helpers_delegate_to_product_helpers_when_available(self) -> None:
        frame = pd.DataFrame(
            {
                "open": [10.0, 11.0, 12.0],
                "high": [11.0, 12.0, 13.0],
                "low": [9.0, 10.0, 11.0],
                "close": [10.5, 11.5, 12.5],
            }
        )

        with (
            patch.object(
                selector_module,
                "_product_compute_daily_return",
                lambda input_frame: np.array([np.nan, 0.1, 0.2], dtype=float),
            ),
            patch.object(
                selector_module,
                "_product_compute_body_pct",
                lambda input_frame: np.array([0.05, 0.06, 0.07], dtype=float),
            ),
            patch.object(
                selector_module,
                "_product_compute_upper_shadow_ratio",
                lambda input_frame: np.array([0.2, 0.1, 0.0], dtype=float),
            ),
        ):
            daily_return = selector_module.DailyReturnFilter().values(frame)
            body_pct = selector_module.BullBodyFilter().values(frame)
            upper_shadow = selector_module.B2Selector()._upper_shadow_ratio(frame)

        np.testing.assert_allclose(daily_return, np.array([np.nan, 0.1, 0.2]), equal_nan=True)
        np.testing.assert_allclose(body_pct, np.array([0.05, 0.06, 0.07]))
        np.testing.assert_allclose(upper_shadow, np.array([0.2, 0.1, 0.0]))

    def test_product_volume_ratio_matches_reference_formula(self) -> None:
        frame = pd.DataFrame({"volume": [100.0, 120.0, 0.0, 50.0]})

        actual = compute_volume_ratio(frame)
        expected = reference_compute_volume_ratio(frame)

        np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_product_strict_yang_bao_yin_matches_reference_formula(self) -> None:
        frame = pd.DataFrame(
            {
                "open": [10.0, 10.5, 9.9, 10.6, 9.0],
                "close": [10.1, 10.0, 10.6, 10.8, 9.5],
            }
        )

        actual = compute_strict_yang_bao_yin(
            frame,
            min_today_body_pct=0.003,
            min_yang_bao_yin_body_pct=0.003,
        )
        expected = reference_compute_strict_yang_bao_yin(
            frame,
            min_today_body_pct=0.003,
            min_yang_bao_yin_body_pct=0.003,
        )

        np.testing.assert_array_equal(actual, expected)

    def test_product_recent_b1_prior_arrays_match_reference_formula(self) -> None:
        frame = pd.DataFrame(
            {
                "J": [10.0, 20.0, 30.0, 15.0, 25.0],
                "_b1_pick": [True, False, False, True, False],
            }
        )

        actual_lag = compute_recent_b1_prior_lag(frame, lookback=2)
        expected_lag = reference_compute_recent_b1_prior_lag(frame, lookback=2)
        actual_j = compute_recent_b1_prior_j(frame, actual_lag, lookback=2)
        expected_j = reference_compute_recent_b1_prior_j(frame, expected_lag, lookback=2)

        np.testing.assert_array_equal(actual_lag, expected_lag)
        np.testing.assert_allclose(actual_j, expected_j, equal_nan=True)

    def test_legacy_b2_volume_and_recent_b1_helpers_delegate_to_product_helpers_when_available(self) -> None:
        frame = pd.DataFrame(
            {
                "open": [10.0, 11.0, 12.0],
                "close": [10.5, 11.5, 12.5],
                "volume": [100.0, 120.0, 110.0],
                "J": [10.0, 20.0, 30.0],
                "_b1_pick": [True, False, True],
            }
        )

        with (
            patch.object(
                selector_module,
                "_product_compute_volume_ratio",
                lambda input_frame: np.array([np.nan, 1.2, 0.9], dtype=float),
            ),
            patch.object(
                selector_module,
                "_product_compute_strict_yang_bao_yin",
                lambda input_frame, **kwargs: np.array([False, True, False], dtype=np.bool_),
            ),
            patch.object(
                selector_module,
                "_product_compute_recent_b1_prior_lag",
                lambda input_frame, lookback=2: np.array([0, 1, 0], dtype=np.int16),
            ),
            patch.object(
                selector_module,
                "_product_compute_recent_b1_prior_j",
                lambda input_frame, prior_lag, lookback=2: np.array([np.nan, 10.0, np.nan], dtype=float),
            ),
        ):
            volume_filter = selector_module.VolumeConfirmFilter()
            recent_filter = selector_module.RecentB1PickFilter(lookback=2)
            volume_ratio = volume_filter.volume_ratio_arr(frame)
            strict_yang_bao_yin = volume_filter.strict_yang_bao_yin_arr(frame)
            prior_lag = recent_filter.prior_lag_arr(frame)
            prior_j = recent_filter.prior_j_arr(frame, prior_lag)

        np.testing.assert_allclose(volume_ratio, np.array([np.nan, 1.2, 0.9]), equal_nan=True)
        np.testing.assert_array_equal(strict_yang_bao_yin, np.array([False, True, False], dtype=np.bool_))
        np.testing.assert_array_equal(prior_lag, np.array([0, 1, 0], dtype=np.int16))
        np.testing.assert_allclose(prior_j, np.array([np.nan, 10.0, np.nan]), equal_nan=True)

    def test_product_zx_lines_match_reference_formula(self) -> None:
        frame = pd.DataFrame(
            {"close": [10.0, 11.0, 10.5, 12.0, 13.0, 14.0]},
            index=pd.to_datetime(
                ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-25"]
            ),
        )

        actual_zxdq, actual_zxdkx = compute_zx_lines(frame, m1=2, m2=3, m3=4, m4=5, zxdq_span=3)
        expected_zxdq, expected_zxdkx = reference_compute_zx_lines(
            frame, m1=2, m2=3, m3=4, m4=5, zxdq_span=3
        )

        pd.testing.assert_series_equal(actual_zxdq, expected_zxdq)
        pd.testing.assert_series_equal(actual_zxdkx, expected_zxdkx)

    def test_legacy_selector_compute_zx_lines_delegates_to_product_helper_when_available(self) -> None:
        frame = pd.DataFrame({"close": [10.0, 11.0, 12.0]})

        def fake_product_compute_zx_lines(
            input_frame: pd.DataFrame,
            m1: int = 14,
            m2: int = 28,
            m3: int = 57,
            m4: int = 114,
            zxdq_span: int = 10,
        ) -> tuple[pd.Series, pd.Series]:
            zxdq = pd.Series([float(m1), float(m2), float(zxdq_span)], index=input_frame.index, name="zxdq")
            zxdkx = pd.Series([float(m3), float(m4), 99.0], index=input_frame.index, name="zxdkx")
            return zxdq, zxdkx

        with patch.object(selector_module, "_product_compute_zx_lines", fake_product_compute_zx_lines):
            zxdq, zxdkx = selector_module.compute_zx_lines(frame, m1=1, m2=2, m3=3, m4=4, zxdq_span=5)

        self.assertEqual(zxdq.tolist(), [1.0, 2.0, 5.0])
        self.assertEqual(zxdkx.tolist(), [3.0, 4.0, 99.0])

    def test_product_zxdq_ratio_mask_matches_reference_formula(self) -> None:
        frame = pd.DataFrame({"close": [9.0, 10.0, 11.0, 8.0, 7.0]})
        zxdq = np.array([10.0, 10.0, 10.0, 0.0, np.nan])

        actual = compute_zxdq_ratio_mask(frame, zxdq, zxdq_ratio=1.0)
        expected = reference_compute_zxdq_ratio_mask(frame, zxdq, zxdq_ratio=1.0)

        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual, np.array([True, False, False, False, False]))

    def test_legacy_selector_zxdq_ratio_filter_delegates_to_product_helper_when_available(self) -> None:
        frame = pd.DataFrame({"close": [9.0, 10.0, 11.0], "zxdq": [10.0, 10.0, 10.0]})

        def fake_product_compute_zxdq_ratio_mask(
            input_frame: pd.DataFrame,
            zxdq_values: np.ndarray,
            *,
            zxdq_ratio: float = 1.0,
        ) -> np.ndarray:
            self.assertIs(input_frame, frame)
            np.testing.assert_allclose(zxdq_values, np.array([10.0, 10.0, 10.0]))
            self.assertEqual(zxdq_ratio, 0.95)
            return np.array([False, True, False])

        with patch.object(
            selector_module,
            "_product_compute_zxdq_ratio_mask",
            fake_product_compute_zxdq_ratio_mask,
        ):
            actual = selector_module.ZXDQRatioFilter(zxdq_ratio=0.95).vec_mask(frame)

        np.testing.assert_array_equal(actual, np.array([False, True, False]))

    def test_product_weekly_close_matches_reference_formula_for_datetime_index(self) -> None:
        frame = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0, 9.0, 13.0, 14.0]},
            index=pd.to_datetime(
                ["2026-01-02", "2026-01-05", "2026-01-09", "2026-01-12", "2026-01-16", "2026-01-19"]
            ),
        )

        actual = compute_weekly_close(frame)
        expected = reference_compute_weekly_close(frame)

        pd.testing.assert_series_equal(actual, expected)

    def test_product_weekly_ma_bull_matches_reference_formula_for_date_column(self) -> None:
        dates = pd.date_range("2026-01-05", periods=60, freq="B")
        frame = pd.DataFrame(
            {
                "date": dates,
                "close": np.linspace(10.0, 40.0, num=len(dates)),
            }
        )

        actual = compute_weekly_ma_bull(frame, ma_periods=(2, 3, 4))
        expected = reference_compute_weekly_ma_bull(frame, ma_periods=(2, 3, 4))

        pd.testing.assert_series_equal(actual, expected)

    def test_legacy_selector_compute_weekly_close_delegates_to_product_helper_when_available(self) -> None:
        frame = pd.DataFrame({"close": [10.0, 11.0]})

        def fake_product_compute_weekly_close(input_frame: pd.DataFrame) -> pd.Series:
            return pd.Series([float(len(input_frame))], index=pd.to_datetime(["2026-01-09"]))

        with patch.object(selector_module, "_product_compute_weekly_close", fake_product_compute_weekly_close):
            actual = selector_module.compute_weekly_close(frame)

        self.assertEqual(actual.tolist(), [2.0])

    def test_legacy_selector_compute_weekly_ma_bull_delegates_to_product_helper_when_available(self) -> None:
        frame = pd.DataFrame({"close": [10.0, 11.0]}, index=pd.to_datetime(["2026-01-05", "2026-01-06"]))

        def fake_product_compute_weekly_ma_bull(
            input_frame: pd.DataFrame,
            ma_periods: tuple[int, int, int] = (20, 60, 120),
        ) -> pd.Series:
            return pd.Series(
                [ma_periods == (1, 2, 3), False],
                index=input_frame.index,
            )

        with patch.object(selector_module, "_product_compute_weekly_ma_bull", fake_product_compute_weekly_ma_bull):
            actual = selector_module.compute_weekly_ma_bull(frame, ma_periods=(1, 2, 3))

        self.assertEqual(actual.tolist(), [True, False])


if __name__ == "__main__":
    unittest.main()
