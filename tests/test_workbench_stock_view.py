from __future__ import annotations

import json
import importlib.util
import sys
import tempfile
import unittest
import types
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workbench"))

if importlib.util.find_spec("streamlit") is None:
    streamlit_stub = types.ModuleType("streamlit")
    streamlit_stub.session_state = {}
    streamlit_stub.fragment = lambda *args, **kwargs: (lambda func: func)
    streamlit_stub.dialog = lambda *args, **kwargs: (lambda func: func)
    components_stub = types.ModuleType("streamlit.components")
    components_v1_stub = types.ModuleType("streamlit.components.v1")
    components_v1_stub.html = lambda *args, **kwargs: None
    components_stub.v1 = components_v1_stub
    sys.modules["streamlit"] = streamlit_stub
    sys.modules["streamlit.components"] = components_stub
    sys.modules["streamlit.components.v1"] = components_v1_stub

import app as workbench_app  # noqa: E402


class SessionDict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class WorkbenchStockViewTests(unittest.TestCase):
    def write_agy_review_fixture(self, project: Path, pick_date: str = "2026-06-04") -> None:
        candidates_dir = project / "data" / "candidates"
        candidates_dir.mkdir(parents=True)
        candidates_payload = {
            "pick_date": pick_date,
            "candidates": [
                {
                    "code": "300001",
                    "strategy": "brick",
                    "close": 12.3,
                    "brick_growth": 0.08,
                }
            ],
        }
        (candidates_dir / f"candidates_{pick_date}.json").write_text(
            json.dumps(candidates_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        review_dir = project / "data" / "review" / "agy_cli_experimental" / pick_date
        review_dir.mkdir(parents=True)
        (review_dir / "300001_brick.json").write_text(
            json.dumps(
                {
                    "code": "300001",
                    "strategy": "brick",
                    "review_key": "300001_brick",
                    "reviewer": "agy-cli-experimental",
                    "total_score": 4.2,
                    "verdict": "PASS",
                    "signal_type": "breakout",
                    "comment": "AGY result",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (review_dir / "suggestion.json").write_text(
            json.dumps(
                {
                    "pick_date": pick_date,
                    "recommendations": [
                        {
                            "code": "300001",
                            "strategy": "brick",
                            "review_key": "300001_brick",
                            "rank": 1,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

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

    def test_result_center_can_read_agy_experimental_review_source(self) -> None:
        old_root = workbench_app.ROOT
        old_history = workbench_app.HISTORY_DIR
        old_st = workbench_app.st
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self.write_agy_review_fixture(project)
            try:
                workbench_app.ROOT = project
                workbench_app.HISTORY_DIR = project / "data" / "history"
                workbench_app.st = SimpleNamespace(session_state=SessionDict())
                dates = workbench_app.result_center_dates()
                sources = workbench_app.review_sources_for_date("2026-06-04")
                rows = workbench_app.result_rows_for_date(
                    "2026-06-04",
                    workbench_app.AGY_REVIEW_SOURCE,
                )
            finally:
                workbench_app.ROOT = old_root
                workbench_app.HISTORY_DIR = old_history
                workbench_app.st = old_st

        self.assertEqual(dates, ["2026-06-04"])
        self.assertEqual(sources, [workbench_app.AGY_REVIEW_SOURCE])
        self.assertEqual(rows[0]["代码"], "300001")
        self.assertEqual(rows[0]["复评状态"], "推荐")
        self.assertEqual(rows[0]["结论"], "PASS")
        self.assertEqual(rows[0]["推荐"], "是")

    def test_stock_view_can_read_agy_experimental_review_source(self) -> None:
        old_root = workbench_app.ROOT
        old_history = workbench_app.HISTORY_DIR
        old_st = workbench_app.st
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self.write_agy_review_fixture(project)
            try:
                workbench_app.ROOT = project
                workbench_app.HISTORY_DIR = project / "data" / "history"
                workbench_app.st = SimpleNamespace(session_state=SessionDict())
                rows = workbench_app.stock_view_rows_for_date(
                    "2026-06-04",
                    workbench_app.AGY_REVIEW_SOURCE,
                )
            finally:
                workbench_app.ROOT = old_root
                workbench_app.HISTORY_DIR = old_history
                workbench_app.st = old_st

        self.assertEqual(rows[0]["code"], "300001")
        self.assertEqual(rows[0]["review"]["reviewer"], "agy-cli-experimental")
        self.assertEqual(rows[0]["review_source"], workbench_app.AGY_REVIEW_SOURCE)
        self.assertEqual(rows[0]["status"], "recommended")
        self.assertEqual(rows[0]["rank"], 1)

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

    def test_command_plan_uses_selected_review_backend(self) -> None:
        old_st = workbench_app.st
        try:
            session = SessionDict(
                {
                    "run_cfg": {"reviewer": "gemini-cli"},
                    "agy_review_cfg": {"output_dir": "data/review/agy_cli_experimental"},
                    "codex_review_cfg": {"output_dir": "data/review/codex_cli"},
                }
            )
            workbench_app.st = SimpleNamespace(session_state=session)
            gemini_steps = workbench_app.command_plan("只跑复评", Path("/tmp/run-gemini"))
            session["run_cfg"] = {"reviewer": "agy-cli-experimental"}
            agy_steps = workbench_app.command_plan("只跑复评", Path("/tmp/run-agy"))
            session["run_cfg"] = {"reviewer": "codex-cli"}
            codex_steps = workbench_app.command_plan("只跑复评", Path("/tmp/run-codex"))
            session["run_cfg"] = {"reviewer": "multi-model"}
            multi_steps = workbench_app.command_plan("只跑复评", Path("/tmp/run-multi"))
        finally:
            workbench_app.st = old_st

        self.assertEqual(gemini_steps[0][0], "Gemini CLI 复评")
        self.assertIn("agent/gemini_cli_review.py", gemini_steps[0][1])
        self.assertEqual(agy_steps[0][0], "AGY CLI 实验复评")
        self.assertIn("agent/agy_cli_review.py", agy_steps[0][1])
        self.assertEqual(len(agy_steps), 1)
        self.assertEqual(codex_steps[0][0], "Codex GPT-5.5 复评")
        self.assertIn("agent/codex_cli_review.py", codex_steps[0][1])
        self.assertEqual(len(codex_steps), 1)
        self.assertEqual(multi_steps[0][0], "多模型复评与共识汇总")
        self.assertIn("agent/multi_model_review.py", multi_steps[0][1])
        self.assertEqual(len(multi_steps), 1)

    def test_reviewer_widget_sync_preserves_first_selection_change(self) -> None:
        old_st = workbench_app.st
        try:
            session = SessionDict({"run_cfg": {"reviewer": "gemini-cli"}})
            workbench_app.st = SimpleNamespace(session_state=session)

            workbench_app.ensure_reviewer_widget_state()
            self.assertEqual(session[workbench_app.REVIEWER_WIDGET_KEY], "gemini-cli")

            session[workbench_app.REVIEWER_WIDGET_KEY] = "agy-cli-experimental"
            workbench_app.sync_reviewer_from_widget()
            workbench_app.ensure_reviewer_widget_state()

            self.assertEqual(session["run_cfg"]["reviewer"], "agy-cli-experimental")
            self.assertEqual(session[workbench_app.REVIEWER_WIDGET_KEY], "agy-cli-experimental")
        finally:
            workbench_app.st = old_st

    def test_parse_agy_models_output_preserves_exact_names(self) -> None:
        output = "\n".join(
            [
                "Gemini 3.5 Flash (Medium)",
                "Gemini 3.5 Flash (High)",
                "Claude Sonnet 4.6 (Thinking)",
                "",
            ]
        )

        self.assertEqual(
            workbench_app.parse_agy_models_output(output),
            [
                "Gemini 3.5 Flash (Medium)",
                "Gemini 3.5 Flash (High)",
                "Claude Sonnet 4.6 (Thinking)",
            ],
        )

    def test_cached_agy_model_options_uses_session_state_without_cli(self) -> None:
        old_st = workbench_app.st
        try:
            session = SessionDict(
                {
                    workbench_app.AGY_MODELS_CACHE_KEY: {
                        "agy": {
                            "models": ["Gemini 3.5 Flash (Medium)"],
                            "error": "",
                            "fetched_at": "2026-06-05T16:00:00",
                        }
                    }
                }
            )
            workbench_app.st = SimpleNamespace(session_state=session)
            with patch.object(workbench_app.subprocess, "run") as run_mock:
                models, error, fetched_at = workbench_app.cached_agy_model_options("agy")
        finally:
            workbench_app.st = old_st

        self.assertEqual(models, ["Gemini 3.5 Flash (Medium)"])
        self.assertEqual(error, "")
        self.assertEqual(fetched_at, "2026-06-05T16:00:00")
        run_mock.assert_not_called()

    def test_fetch_agy_model_options_reads_cli_models(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout="Gemini 3.5 Flash (Medium)\nGPT-OSS 120B (Medium)\n",
            stderr="",
        )
        with patch.object(workbench_app.subprocess, "run", return_value=completed) as run_mock:
            models, error = workbench_app.fetch_agy_model_options("agy")

        self.assertEqual(error, "")
        self.assertEqual(models, ["Gemini 3.5 Flash (Medium)", "GPT-OSS 120B (Medium)"])
        run_mock.assert_called_once()

    def test_store_agy_model_options_updates_session_cache(self) -> None:
        old_st = workbench_app.st
        try:
            session = SessionDict()
            workbench_app.st = SimpleNamespace(session_state=session)
            workbench_app.store_agy_model_options("agy", ["Gemini 3.5 Flash (High)"])
            models, error, fetched_at = workbench_app.cached_agy_model_options("agy")
        finally:
            workbench_app.st = old_st

        self.assertEqual(models, ["Gemini 3.5 Flash (High)"])
        self.assertEqual(error, "")
        self.assertTrue(fetched_at)


if __name__ == "__main__":
    unittest.main()
