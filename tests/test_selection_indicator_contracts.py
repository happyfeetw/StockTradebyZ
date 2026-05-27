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
from stocktrade.domain.selection import compute_kdj, compute_zx_lines  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
