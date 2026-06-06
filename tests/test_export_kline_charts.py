from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import export_kline_charts  # noqa: E402


class ExportKlineChartsTests(unittest.TestCase):
    def test_truncate_to_pick_date_removes_future_bars(self) -> None:
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-03-01", "2026-03-04", "2026-03-05"]),
                "close": [10, 11, 12],
            }
        )

        truncated = export_kline_charts._truncate_to_pick_date(df, "2026-03-04")

        self.assertEqual(truncated["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-03-01", "2026-03-04"])
        self.assertEqual(truncated["close"].tolist(), [10, 11])


if __name__ == "__main__":
    unittest.main()
