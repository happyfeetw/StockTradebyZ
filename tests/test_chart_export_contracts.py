from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from base_reviewer import BaseReviewer  # noqa: E402
from dashboard.export_kline_charts import (  # noqa: E402
    _export_daily_chart_pillow,
    _load_candidates,
    _safe_strategy_suffix,
)
from pipeline.archive_results import find_chart  # noqa: E402


class ChartExportContractTests(unittest.TestCase):
    def test_load_candidates_keeps_code_strategy_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.json"
            path.write_text(
                json.dumps(
                    {
                        "pick_date": "2026-05-25",
                        "candidates": [
                            {"code": "000001", "strategy": "b1"},
                            {"code": "000001", "strategy": "b2"},
                            {"code": "000001", "strategy": "b1"},
                            {"code": "000002"},
                            {"strategy": "brick"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                items, pick_date = _load_candidates(path)

        self.assertEqual(pick_date, "2026-05-25")
        self.assertEqual(
            items,
            [
                {"code": "000001", "strategy": "b1"},
                {"code": "000001", "strategy": "b2"},
                {"code": "000002", "strategy": ""},
            ],
        )

    def test_strategy_suffix_matches_reviewer_contract(self) -> None:
        self.assertEqual(_safe_strategy_suffix("b1+brick"), "b1_brick")
        self.assertEqual(_safe_strategy_suffix("b2"), "b2")
        self.assertEqual(_safe_strategy_suffix("砖形图"), "_")

    def test_find_chart_images_prefers_strategy_specific_chart(self) -> None:
        reviewer = BaseReviewer.__new__(BaseReviewer)
        with tempfile.TemporaryDirectory() as tmp:
            date_dir = Path(tmp) / "2026-05-25"
            date_dir.mkdir(parents=True)
            legacy = date_dir / "000001_day.jpg"
            strategy = date_dir / "000001_b2_day.jpg"
            legacy.write_text("legacy", encoding="utf-8")
            strategy.write_text("strategy", encoding="utf-8")
            reviewer.kline_dir = Path(tmp)

            self.assertEqual(reviewer.find_chart_images("2026-05-25", "000001", "b2"), strategy)
            self.assertEqual(reviewer.find_chart_images("2026-05-25", "000001", "b1"), legacy)

    def test_find_chart_images_supports_strategy_png_and_legacy_fallback(self) -> None:
        reviewer = BaseReviewer.__new__(BaseReviewer)
        with tempfile.TemporaryDirectory() as tmp:
            date_dir = Path(tmp) / "2026-05-25"
            date_dir.mkdir(parents=True)
            strategy_png = date_dir / "000001_brick_day.png"
            strategy_png.write_text("strategy", encoding="utf-8")
            legacy_png = date_dir / "000002_day.png"
            legacy_png.write_text("legacy", encoding="utf-8")
            reviewer.kline_dir = Path(tmp)

            self.assertEqual(reviewer.find_chart_images("2026-05-25", "000001", "brick"), strategy_png)
            self.assertEqual(reviewer.find_chart_images("2026-05-25", "000002", "b1"), legacy_png)

    def test_archive_chart_path_prefers_strategy_specific_chart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            date_dir = Path(tmp) / "2026-05-25"
            date_dir.mkdir(parents=True)
            legacy = date_dir / "000001_day.jpg"
            strategy = date_dir / "000001_b2_day.jpg"
            legacy.write_text("legacy", encoding="utf-8")
            strategy.write_text("strategy", encoding="utf-8")

            self.assertEqual(find_chart(Path(tmp), "2026-05-25", "000001", "b2"), str(strategy))
            self.assertEqual(find_chart(Path(tmp), "2026-05-25", "000001", "b1"), str(legacy))

    def test_pillow_daily_export_supports_zx_and_brick_panel(self) -> None:
        rows = []
        for idx, date in enumerate(pd.date_range("2025-12-01", periods=130, freq="B")):
            close = 10 + idx * 0.03
            open_price = close * (0.99 if idx % 2 == 0 else 1.01)
            rows.append(
                {
                    "date": date,
                    "open": open_price,
                    "high": max(open_price, close) * 1.02,
                    "low": min(open_price, close) * 0.98,
                    "close": close,
                    "volume": 100000 + idx * 1000,
                }
            )
        df = pd.DataFrame(rows)

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "000001_brick_day.jpg"
            _export_daily_chart_pillow(
                df,
                "000001",
                out_path,
                width=900,
                height=600,
                bars=80,
                show_zx_lines=True,
                show_brick_panel=True,
                show_ma_lines=False,
                zx_params={"zxdq_span": 10, "m1": 14, "m2": 28, "m3": 57, "m4": 114},
                brick_params={"n": 8, "m1": 3, "m2": 12, "m3": 12, "t": 8},
            )

            self.assertTrue(out_path.exists())
            self.assertGreater(out_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
