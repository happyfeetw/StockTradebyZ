from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PreselectCliRetirementHarnessTests(unittest.TestCase):
    def test_preselect_cli_exits_before_legacy_execution_by_default(self) -> None:
        env = os.environ.copy()
        env.pop("STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI", None)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pipeline.cli",
                "preselect",
                "--config",
                "does-not-exist.yaml",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        combined_output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2)
        self.assertIn("R7 preselect CLI retirement", combined_output)
        self.assertIn("POST /api/runs/preselect", combined_output)
        self.assertIn("STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1", combined_output)
        self.assertNotIn("找不到配置文件", combined_output)
        self.assertNotIn("===== 量化初选开始 =====", combined_output)

    def test_preselect_cli_guard_runs_before_config_load_and_candidate_write(self) -> None:
        text = (ROOT / "pipeline" / "cli.py").read_text(encoding="utf-8")

        guard_index = text.index("if not legacy_preselect_cli_enabled()")
        config_index = text.index("_enabled_strategies", guard_index)
        run_preselect_index = text.index("run_preselect(", guard_index)
        save_candidates_index = text.index("save_candidates(", guard_index)

        self.assertLess(guard_index, config_index)
        self.assertLess(guard_index, run_preselect_index)
        self.assertLess(guard_index, save_candidates_index)
        self.assertIn("LEGACY_PRESELECT_CLI_RETIRED_NOTICE", text)
        self.assertIn("raise SystemExit(2)", text)

    def test_preselect_cli_rollback_flag_is_explicit_and_suppressed_by_default(self) -> None:
        from legacy_compat import LEGACY_PRESELECT_CLI_ENV, legacy_preselect_cli_enabled

        previous = os.environ.get(LEGACY_PRESELECT_CLI_ENV)
        os.environ.pop(LEGACY_PRESELECT_CLI_ENV, None)
        try:
            self.assertFalse(legacy_preselect_cli_enabled())
            os.environ[LEGACY_PRESELECT_CLI_ENV] = "1"
            self.assertTrue(legacy_preselect_cli_enabled())
            os.environ[LEGACY_PRESELECT_CLI_ENV] = "true"
            self.assertFalse(legacy_preselect_cli_enabled())
        finally:
            if previous is None:
                os.environ.pop(LEGACY_PRESELECT_CLI_ENV, None)
            else:
                os.environ[LEGACY_PRESELECT_CLI_ENV] = previous


if __name__ == "__main__":
    unittest.main()
