from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RunAllRetirementHarnessTests(unittest.TestCase):
    def test_run_all_exits_before_legacy_subprocesses_and_file_reads_by_default(self) -> None:
        env = os.environ.copy()
        env.pop("STOCKTRADE_ALLOW_LEGACY_RUN_ALL", None)

        result = subprocess.run(
            [
                sys.executable,
                "run_all.py",
                "--skip-fetch",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        combined_output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2)
        self.assertIn("R7 run_all retirement", combined_output)
        self.assertIn("./start_product", combined_output)
        self.assertIn("POST /api/runs/preselect", combined_output)
        self.assertIn("POST /api/runs/review/provider", combined_output)
        self.assertIn("STOCKTRADE_ALLOW_LEGACY_RUN_ALL=1", combined_output)
        self.assertNotIn("[步骤]", combined_output)
        self.assertNotIn("pipeline.cli", combined_output)
        self.assertNotIn("找不到 candidates_latest", combined_output)

    def test_run_all_guard_runs_before_subprocess_calls_and_recommendation_reads(self) -> None:
        text = (ROOT / "run_all.py").read_text(encoding="utf-8")

        guard_index = text.index("if not legacy_run_all_enabled()")
        start_index = text.index("start = args.start_from", guard_index)
        subprocess_index = text.index("_run(", guard_index)
        recommendation_index = text.index("_print_recommendations()", guard_index)

        self.assertLess(guard_index, start_index)
        self.assertLess(guard_index, subprocess_index)
        self.assertLess(guard_index, recommendation_index)
        self.assertIn("LEGACY_RUN_ALL_RETIRED_NOTICE", text)
        self.assertIn("raise SystemExit(2)", text)

    def test_run_all_rollback_flag_is_explicit_and_suppressed_by_default(self) -> None:
        from legacy_compat import LEGACY_RUN_ALL_ENV, legacy_run_all_enabled

        previous = os.environ.get(LEGACY_RUN_ALL_ENV)
        os.environ.pop(LEGACY_RUN_ALL_ENV, None)
        try:
            self.assertFalse(legacy_run_all_enabled())
            os.environ[LEGACY_RUN_ALL_ENV] = "1"
            self.assertTrue(legacy_run_all_enabled())
            os.environ[LEGACY_RUN_ALL_ENV] = "true"
            self.assertFalse(legacy_run_all_enabled())
        finally:
            if previous is None:
                os.environ.pop(LEGACY_RUN_ALL_ENV, None)
            else:
                os.environ[LEGACY_RUN_ALL_ENV] = previous


if __name__ == "__main__":
    unittest.main()
