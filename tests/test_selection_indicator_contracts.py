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
from stocktrade.domain.selection import compute_kdj  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
