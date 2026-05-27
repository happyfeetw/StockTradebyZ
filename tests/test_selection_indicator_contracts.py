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
    compute_daily_return,
    compute_kdj,
    compute_max_volume_not_bearish,
    compute_upper_shadow_ratio,
    compute_weekly_close,
    compute_weekly_ma_bull,
    compute_zx_lines,
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
