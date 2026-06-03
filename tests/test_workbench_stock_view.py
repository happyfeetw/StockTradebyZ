from __future__ import annotations

import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workbench"))

import app as workbench_app  # noqa: E402


class WorkbenchStockViewTests(unittest.TestCase):
    def test_stock_view_rows_read_selected_history_date(self) -> None:
        old_root = workbench_app.ROOT
        old_history = workbench_app.HISTORY_DIR
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            history_dir = project / "data" / "history" / "2026-06-03"
            history_dir.mkdir(parents=True)
            (history_dir / "all.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "code": "600000",
                                "strategy": "b1",
                                "status": "recommended",
                                "rank": 1,
                                "review": {"total_score": 4.5, "verdict": "PASS"},
                            },
                            {
                                "code": "000001",
                                "strategy": "b2",
                                "status": "reviewed",
                                "review": {"total_score": 3.2, "verdict": "WATCH"},
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            try:
                workbench_app.ROOT = project
                workbench_app.HISTORY_DIR = project / "data" / "history"
                rows = workbench_app.stock_view_rows_for_date("2026-06-03")
            finally:
                workbench_app.ROOT = old_root
                workbench_app.HISTORY_DIR = old_history

        self.assertEqual([row["code"] for row in rows], ["600000", "000001"])
        self.assertEqual(workbench_app.stock_row_status_label(rows[0]), "推荐")

    def test_filter_stock_view_rows_by_strategy_recommendation_and_score(self) -> None:
        rows = [
            {"code": "600000", "strategy": "b1", "status": "recommended", "review": {"total_score": 4.6}},
            {"code": "000001", "strategy": "b1", "status": "reviewed", "review": {"total_score": 3.4}},
            {"code": "300001", "strategy": "brick", "status": "unreviewed", "review": {}},
        ]

        recommended = workbench_app.filter_stock_view_rows(rows, "b1", "仅推荐", (4.0, 5.0), False)
        reviewed_not_recommended = workbench_app.filter_stock_view_rows(
            rows,
            "全部",
            "已复评未推荐",
            (0.0, 3.5),
            False,
        )
        unreviewed = workbench_app.filter_stock_view_rows(rows, "全部", "未复评", (4.0, 5.0), False)

        self.assertEqual([row["code"] for row in recommended], ["600000"])
        self.assertEqual([row["code"] for row in reviewed_not_recommended], ["000001"])
        self.assertEqual([row["code"] for row in unreviewed], ["300001"])

    def test_stock_view_selected_index_uses_dataframe_selection(self) -> None:
        self.assertEqual(workbench_app.stock_view_selected_index({"selection": {"rows": [2]}}, 5), 2)
        state = SimpleNamespace(selection=SimpleNamespace(rows=[1]))
        self.assertEqual(workbench_app.stock_view_selected_index(state, 5), 1)
        self.assertEqual(workbench_app.stock_view_selected_index({"selection": {"rows": [9]}}, 5), 0)
        self.assertEqual(workbench_app.stock_view_selected_index({"selection": {"rows": []}}, 5), 0)


if __name__ == "__main__":
    unittest.main()
