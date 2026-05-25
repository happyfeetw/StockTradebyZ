from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from Selector import (  # noqa: E402
    BullBodyFilter,
    DailyReturnFilter,
    RecentB1PickFilter,
    VolumeConfirmFilter,
)


def frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["date"] = pd.date_range("2026-01-01", periods=len(df), freq="D")
    return df.set_index("date")


class B2StrategyFilterTests(unittest.TestCase):
    def test_daily_return_is_greater_than_or_equal_to_threshold(self) -> None:
        df = frame(
            [
                {"close": 100.0},
                {"close": 104.0},
                {"close": 108.15},
            ]
        )

        mask = DailyReturnFilter(min_return=0.04).vec_mask(df)

        self.assertEqual(mask.tolist(), [False, True, False])

    def test_bull_body_rejects_fake_bearish_and_tiny_body(self) -> None:
        df = frame(
            [
                {"open": 105.0, "close": 104.5},
                {"open": 104.0, "close": 104.1},
                {"open": 100.0, "close": 101.0},
            ]
        )

        mask = BullBodyFilter(min_body_pct=0.003).vec_mask(df)

        self.assertEqual(mask.tolist(), [False, False, True])

    def test_volume_up_does_not_require_engulfing_but_flat_volume_does(self) -> None:
        df = frame(
            [
                {"open": 10.0, "close": 10.1, "volume": 100.0},
                {"open": 10.5, "close": 10.0, "volume": 110.0},
                {"open": 9.9, "close": 10.6, "volume": 108.0},
                {"open": 10.6, "close": 10.8, "volume": 100.0},
            ]
        )

        filter_ = VolumeConfirmFilter(
            volume_ratio_min=1.0,
            flat_volume_ratio=0.98,
            min_today_body_pct=0.003,
            min_yang_bao_yin_body_pct=0.003,
        )

        self.assertEqual(filter_.vec_mask(df).tolist(), [False, True, True, False])
        self.assertEqual(filter_.strict_yang_bao_yin_arr(df).tolist(), [False, False, True, False])

    def test_recent_b1_uses_only_t_minus_one_or_t_minus_two(self) -> None:
        df = frame(
            [
                {"J": 10.0, "_b1_pick": True},
                {"J": 20.0, "_b1_pick": False},
                {"J": 30.0, "_b1_pick": False},
                {"J": 15.0, "_b1_pick": True},
                {"J": 25.0, "_b1_pick": False},
            ]
        )
        filter_ = RecentB1PickFilter(lookback=2)

        lag = filter_.prior_lag_arr(df)
        prior_j = filter_.prior_j_arr(df, lag)

        self.assertEqual(lag.tolist(), [0, 1, 2, 0, 1])
        self.assertTrue(pd.isna(prior_j[0]))
        self.assertEqual(float(prior_j[1]), 10.0)
        self.assertEqual(float(prior_j[2]), 10.0)
        self.assertTrue(pd.isna(prior_j[3]))
        self.assertEqual(float(prior_j[4]), 15.0)


if __name__ == "__main__":
    unittest.main()
