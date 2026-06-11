from __future__ import annotations

import json
import importlib.util
import logging
import os
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

    def test_codex_auth_mode_defaults_to_local_oauth(self) -> None:
        cfg = workbench_app.apply_codex_auth_mode({}, workbench_app.CODEX_AUTH_MODE_LOCAL_OAUTH)

        self.assertEqual(workbench_app.normalize_codex_auth_mode(cfg), workbench_app.CODEX_AUTH_MODE_LOCAL_OAUTH)
        self.assertEqual(cfg["auth_mode"], workbench_app.CODEX_AUTH_MODE_LOCAL_OAUTH)
        self.assertFalse(cfg["env_provider_enabled"])
        self.assertFalse(cfg["ignore_user_config"])

    def test_codex_auth_mode_supports_env_provider(self) -> None:
        cfg = workbench_app.apply_codex_auth_mode({}, workbench_app.CODEX_AUTH_MODE_ENV_PROVIDER)

        self.assertEqual(workbench_app.normalize_codex_auth_mode(cfg), workbench_app.CODEX_AUTH_MODE_ENV_PROVIDER)
        self.assertEqual(cfg["auth_mode"], workbench_app.CODEX_AUTH_MODE_ENV_PROVIDER)
        self.assertTrue(cfg["env_provider_enabled"])
        self.assertTrue(cfg["ignore_user_config"])

    def test_codex_auth_mode_infers_legacy_provider_flag(self) -> None:
        mode = workbench_app.normalize_codex_auth_mode({"env_provider_enabled": True})

        self.assertEqual(mode, workbench_app.CODEX_AUTH_MODE_ENV_PROVIDER)

    def test_latest_run_dir_after_refresh_restores_active_disk_run(self) -> None:
        old_runs_dir = workbench_app.RUNS_DIR
        old_st = workbench_app.st
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            invalid = runs_dir / "multi_model_20260607_111604"
            invalid.mkdir()
            older = runs_dir / "2026-06-11_173502"
            older.mkdir()
            (older / "run_state.json").write_text(
                json.dumps({"status": "finished", "runner_pid": 999999}, ensure_ascii=False),
                encoding="utf-8",
            )
            active = runs_dir / "2026-06-11_173519"
            active.mkdir()
            (active / "run_state.json").write_text(
                json.dumps({"status": "running", "runner_pid": os.getpid()}, ensure_ascii=False),
                encoding="utf-8",
            )
            (active / "run.log").write_text("[Step] 多模型复评与共识汇总\n", encoding="utf-8")
            session = SessionDict()
            try:
                workbench_app.RUNS_DIR = runs_dir
                workbench_app.st = SimpleNamespace(session_state=session)
                listed = workbench_app.list_run_dirs()
                latest = workbench_app.latest_run_dir()
            finally:
                workbench_app.RUNS_DIR = old_runs_dir
                workbench_app.st = old_st

        self.assertNotIn(invalid, listed)
        self.assertEqual(latest, active)
        self.assertEqual(session["last_run_dir"], str(active))

    def test_latest_run_dir_prefers_newer_disk_run_over_stale_session_run(self) -> None:
        old_runs_dir = workbench_app.RUNS_DIR
        old_st = workbench_app.st
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            stale = runs_dir / "2026-06-11_173519"
            stale.mkdir()
            (stale / "run_state.json").write_text(
                json.dumps({"status": "failed", "runner_pid": 999999}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stale / "run.log").write_text("[ERROR] old failure\n", encoding="utf-8")

            newer = runs_dir / "2026-06-12_agy_resume_consensus"
            logs = newer / "multi_model_logs"
            logs.mkdir(parents=True)
            (logs / "agy-cli-experimental__gemini-3.5-flash-high.log").write_text(
                "[INFO] AGY 实验复评完成：成功 136 支，失败/跳过 0 支\n",
                encoding="utf-8",
            )
            os.utime(stale / "run.log", (1000, 1000))
            os.utime(stale / "run_state.json", (1000, 1000))
            os.utime(stale, (1000, 1000))
            os.utime(logs / "agy-cli-experimental__gemini-3.5-flash-high.log", (2000, 2000))
            os.utime(newer, (2000, 2000))

            session = SessionDict({"last_run_dir": str(stale)})
            try:
                workbench_app.RUNS_DIR = runs_dir
                workbench_app.st = SimpleNamespace(session_state=session)
                listed = workbench_app.list_run_dirs()
                latest = workbench_app.latest_run_dir()
            finally:
                workbench_app.RUNS_DIR = old_runs_dir
                workbench_app.st = old_st

        self.assertIn(newer, listed)
        self.assertEqual(latest, newer)
        self.assertEqual(session["last_run_dir"], str(newer))

    def test_websocket_close_filter_only_suppresses_known_streamlit_disconnect_noise(self) -> None:
        filter_obj = workbench_app._ClosedWorkbenchWebSocketFilter()
        record = logging.LogRecord(
            name="asyncio",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Task exception was never retrieved",
            args=(),
            exc_info=None,
        )
        normal_error = RuntimeError("boom")
        record.exc_info = (RuntimeError, normal_error, None)
        self.assertTrue(filter_obj.filter(record))

        websocket_error_cls = workbench_app._TornadoWebSocketClosedError
        if websocket_error_cls is not None:
            record.exc_info = (websocket_error_cls, websocket_error_cls(), None)
            self.assertFalse(filter_obj.filter(record))

    def test_multi_model_progress_rows_keep_latest_status_per_model(self) -> None:
        log_text = "\n".join(
            [
                "[2026-06-11 10:00:00] [CONFIG] gemini-cli/gemini-3.1-pro-preview -> /tmp/gemini.yaml",
                "[2026-06-11 10:00:00] [CONFIG] codex-cli/gpt-5.5-high-standard -> /tmp/codex.yaml",
                "[2026-06-11 10:00:01] [START] [gemini-cli] 启动 gemini-cli/gemini-3.1-pro-preview",
                "[2026-06-11 10:00:01] [START] [codex-cli] 启动 codex-cli/gpt-5.5-high-standard",
                "[2026-06-11 10:00:31] [PROGRESS] 多模型复评进度 attempt=1",
                "  [codex-cli]",
                "    - codex-cli/gpt-5.5-high-standard: running, elapsed=30s, progress=处理到 5/104 (5%), latest=[1-5/104] codex batch",
                "  [gemini-cli]",
                "    - gemini-cli/gemini-3.1-pro-preview: running, elapsed=30s, progress=处理到 10/104 (10%), latest=[6-10/104] gemini batch",
                "[2026-06-11 10:01:01] [PROGRESS] 多模型复评进度 attempt=1",
                "  [codex-cli]",
                "    - codex-cli/gpt-5.5-high-standard: running, elapsed=1m00s, progress=处理到 15/104 (14%), latest=[11-15/104] codex batch",
                "  [gemini-cli]",
                "    - gemini-cli/gemini-3.1-pro-preview: finished, exit=0, elapsed=1m00s, progress=成功 107/136，失败/跳过 29 (100%), latest=[INFO] 评分完成：成功 107 支，失败/跳过 29 支",
            ]
        )

        rows = workbench_app.multi_model_progress_rows(log_text)
        by_key = {row["key"]: row for row in rows}

        self.assertEqual(rows[0]["key"], "gemini-cli/gemini-3.1-pro-preview")
        self.assertEqual(rows[1]["key"], "codex-cli/gpt-5.5-high-standard")
        self.assertEqual(by_key["gemini-cli/gemini-3.1-pro-preview"]["status_label"], "完成")
        self.assertEqual(by_key["gemini-cli/gemini-3.1-pro-preview"]["count_text"], "成功 107/136，失败/跳过 29")
        self.assertEqual(by_key["gemini-cli/gemini-3.1-pro-preview"]["percent"], 100)
        self.assertEqual(by_key["codex-cli/gpt-5.5-high-standard"]["status_label"], "运行中")
        self.assertEqual(by_key["codex-cli/gpt-5.5-high-standard"]["count_text"], "处理到 15/104")
        self.assertEqual(by_key["codex-cli/gpt-5.5-high-standard"]["percent"], 14)

    def test_compact_run_log_for_display_removes_repeated_multi_model_progress_blocks(self) -> None:
        log_text = "\n".join(
            [
                "[Step] 多模型复评与共识汇总",
                "[2026-06-11 10:00:01] [START] [gemini-cli] 启动 gemini-cli/gemini-3.1-pro-preview",
                "[2026-06-11 10:00:31] [PROGRESS] 多模型复评进度 attempt=1",
                "  [gemini-cli]",
                "    - gemini-cli/gemini-3.1-pro-preview: running, elapsed=30s, progress=处理到 10/104 (10%), latest=[6-10/104] gemini batch",
                "[2026-06-11 10:01:01] [PROGRESS] 多模型复评进度 attempt=1",
                "  [gemini-cli]",
                "    - gemini-cli/gemini-3.1-pro-preview: running, elapsed=1m00s, progress=处理到 20/104 (19%), latest=[16-20/104] gemini batch",
                "[2026-06-11 10:01:02] [DONE] [gemini-cli] gemini-cli/gemini-3.1-pro-preview 结束",
            ]
        )

        compacted = workbench_app.compact_run_log_for_display(log_text)

        self.assertIn("[Step] 多模型复评与共识汇总", compacted)
        self.assertIn("[DONE] [gemini-cli] gemini-cli/gemini-3.1-pro-preview 结束", compacted)
        self.assertNotIn("多模型复评进度 attempt=1", compacted)
        self.assertNotIn("progress=处理到 10/104", compacted)
        self.assertNotIn("progress=处理到 20/104", compacted)

    def test_multi_model_progress_html_does_not_indent_rows_as_markdown_code(self) -> None:
        html = workbench_app.multi_model_progress_html(
            [
                {
                    "key": "gemini-cli/gemini-3.1-pro-preview",
                    "status_label": "运行中",
                    "count_text": "5/136",
                    "percent": 4,
                    "elapsed": "30s",
                }
            ]
        )

        self.assertIn('<div class="review-progress-row">', html)
        self.assertNotRegex(html, r"\n\s{4,}<div class=\"review-progress-row\"")
        self.assertNotIn("&lt;div class=&quot;review-progress-row&quot;", html)

    def test_consensus_rows_merge_z_quality_decisions(self) -> None:
        models = ["m1", "m2", "m3"]
        decisions = [
            {
                "code": "600000",
                "strategy": "b1",
                "review_key": "600000_b1",
                "rank": 1,
                "decision_bucket": "single_model_recommended",
                "consensus_verdict": "FAIL",
                "consensus_score": 3.1,
                "agreement_score": 0.3,
                "recommended_by_model": {"m1": True, "m2": False, "m3": False},
                "verdicts_by_model": {"m1": "PASS", "m2": "FAIL", "m3": "FAIL"},
                "scores_by_model": {"m1": 4.4, "m2": 2.5, "m3": 2.4},
                "missing_models": [],
                "completed_count": 3,
                "total_models": 3,
            }
        ]
        z_by_key = {
            "600000_b1": {
                "z_quality_verdict": "A_SELECT",
                "z_quality_score": 4.7,
                "quality_reasons": ["结构亮点", "贴近支撑"],
                "quality_risks": ["次日不能追高"],
                "hard_vetoes": [],
                "watch_caps": ["support_too_far"],
            }
        }

        table_rows = workbench_app.consensus_decision_table_rows(decisions, models, z_by_key)
        export_rows = workbench_app.consensus_export_rows(decisions, models, z_by_key)

        self.assertEqual(table_rows[0]["Z裁决"], "A精选")
        self.assertEqual(table_rows[0]["Z分"], 4.7)
        self.assertEqual(table_rows[0]["Z观察限制"], "support_too_far")
        self.assertEqual(export_rows[0]["z_quality_verdict"], "A_SELECT")
        self.assertEqual(export_rows[0]["z_quality_label"], "A精选")
        self.assertEqual(export_rows[0]["z_quality_score"], 4.7)
        self.assertEqual(export_rows[0]["z_watch_caps"], ["support_too_far"])

    def test_consensus_tdx_z_presets_and_filters(self) -> None:
        rows = [
            {
                "code": "600000",
                "strategy": "b1",
                "decision_bucket_label": "单模型推荐",
                "consensus_verdict": "FAIL",
                "consensus_score": 3.1,
                "pass_count": 1,
                "watch_count": 0,
                "fail_count": 2,
                "missing_count": 0,
                "model_count": 3,
                "model_states": {"m1": "推荐", "m2": "不推荐", "m3": "不推荐"},
                "z_quality_verdict": "A_SELECT",
                "z_quality_score": 4.7,
                "z_hard_vetoes": [],
                "z_watch_caps": [],
            },
            {
                "code": "000001",
                "strategy": "b1",
                "decision_bucket_label": "无模型推荐",
                "consensus_verdict": "FAIL",
                "consensus_score": 2.2,
                "pass_count": 0,
                "watch_count": 1,
                "fail_count": 2,
                "missing_count": 0,
                "model_count": 3,
                "model_states": {"m1": "观察", "m2": "不推荐", "m3": "不推荐"},
                "z_quality_verdict": "B_WATCH",
                "z_quality_score": 3.8,
                "z_hard_vetoes": [],
                "z_watch_caps": ["support_too_far"],
            },
            {
                "code": "300001",
                "strategy": "brick",
                "decision_bucket_label": "无模型推荐",
                "consensus_verdict": "FAIL",
                "consensus_score": 1.8,
                "pass_count": 0,
                "watch_count": 0,
                "fail_count": 3,
                "missing_count": 0,
                "model_count": 3,
                "model_states": {"m1": "不推荐", "m2": "不推荐", "m3": "不推荐"},
                "z_quality_verdict": "REJECT",
                "z_quality_score": 2.4,
                "z_hard_vetoes": ["centipede_like"],
                "z_watch_caps": [],
            },
        ]

        z_select = workbench_app.apply_consensus_tdx_preset(rows, "Z精选")
        z_select_watch = workbench_app.apply_consensus_tdx_preset(rows, "Z精选+观察")
        filtered = workbench_app.filter_consensus_tdx_rows(
            rows,
            strategies=["b1"],
            verdicts=["FAIL"],
            bucket_labels=["单模型推荐", "无模型推荐"],
            selected_models=[],
            selected_model_states=[],
            model_match="任一选中模型满足",
            pass_range=(0, 3),
            watch_range=(0, 3),
            fail_range=(0, 3),
            score_range=(0.0, 5.0),
            z_verdicts=["A_SELECT", "B_WATCH"],
            z_score_range=(3.5, 5.0),
            exclude_z_hard_veto=True,
            exclude_z_watch_cap=True,
            complete_only=True,
        )

        self.assertEqual([row["code"] for row in z_select], ["600000"])
        self.assertEqual([row["code"] for row in z_select_watch], ["600000", "000001"])
        self.assertEqual([row["code"] for row in filtered], ["600000"])

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
