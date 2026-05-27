from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkbenchRetirementHarnessTests(unittest.TestCase):
    def test_start_workbench_exits_before_token_lookup_or_streamlit_by_default(self) -> None:
        env = os.environ.copy()
        env.pop("STOCKTRADE_ALLOW_LEGACY_WORKBENCH", None)

        result = subprocess.run(
            ["bash", "start_workbench"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        combined_output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2)
        self.assertIn("R7 workbench retirement", combined_output)
        self.assertIn("./start_product", combined_output)
        self.assertIn("STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1", combined_output)
        self.assertNotIn("Missing .venv/bin/streamlit", combined_output)

        text = (ROOT / "start_workbench").read_text(encoding="utf-8")
        guard_index = text.index("STOCKTRADE_ALLOW_LEGACY_WORKBENCH")
        token_lookup_index = text.index("zsh -ic")
        streamlit_index = text.index("streamlit run workbench/app.py")
        self.assertLess(guard_index, token_lookup_index)
        self.assertLess(guard_index, streamlit_index)

    def test_workbench_app_stops_before_legacy_dependencies_by_default(self) -> None:
        text = (ROOT / "workbench" / "app.py").read_text(encoding="utf-8")

        dependency_def_index = text.index("def _load_workbench_dependencies()")
        guard_index = text.index("if not legacy_workbench_enabled()")
        dependency_load_index = text.index("_load_workbench_dependencies()", guard_index)
        session_load_index = text.index("ensure_session_state()", guard_index)
        chart_import_index = text.index("from dashboard.components.charts import")
        paper_import_index = text.index("from paper_trading.core import")

        self.assertLess(guard_index, dependency_load_index)
        self.assertLess(guard_index, session_load_index)
        self.assertGreater(chart_import_index, dependency_def_index)
        self.assertGreater(paper_import_index, dependency_def_index)
        self.assertIn("LEGACY_WORKBENCH_RETIRED_NOTICE", text)
        self.assertIn("LEGACY_WORKBENCH_ENV", text)

    def test_workbench_runner_exits_before_run_config_read_by_default(self) -> None:
        env = os.environ.copy()
        env.pop("STOCKTRADE_ALLOW_LEGACY_WORKBENCH", None)

        result = subprocess.run(
            [sys.executable, "-m", "workbench.runner", "/tmp/stocktrade-missing-run-dir"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        combined_output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2)
        self.assertIn("R7 workbench retirement", combined_output)
        self.assertIn("STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1", combined_output)
        self.assertNotIn("run_config.json", combined_output)

        text = (ROOT / "workbench" / "runner.py").read_text(encoding="utf-8")
        guard_index = text.index("if not legacy_workbench_enabled()")
        config_index = text.index('config = load_json(run_dir / "run_config.json")')
        self.assertLess(guard_index, config_index)

    def test_workbench_rollback_flag_is_explicit_and_suppressed_by_default(self) -> None:
        from legacy_compat import LEGACY_WORKBENCH_ENV, legacy_workbench_enabled

        previous = os.environ.get(LEGACY_WORKBENCH_ENV)
        os.environ.pop(LEGACY_WORKBENCH_ENV, None)
        try:
            self.assertFalse(legacy_workbench_enabled())
            os.environ[LEGACY_WORKBENCH_ENV] = "1"
            self.assertTrue(legacy_workbench_enabled())
            os.environ[LEGACY_WORKBENCH_ENV] = "true"
            self.assertFalse(legacy_workbench_enabled())
        finally:
            if previous is None:
                os.environ.pop(LEGACY_WORKBENCH_ENV, None)
            else:
                os.environ[LEGACY_WORKBENCH_ENV] = previous


if __name__ == "__main__":
    unittest.main()
