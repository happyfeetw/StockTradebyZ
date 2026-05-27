from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ChartExportRetirementHarnessTests(unittest.TestCase):
    def test_legacy_chart_export_is_retired_by_default_before_reading_files(self) -> None:
        env = os.environ.copy()
        env.pop("STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT", None)

        result = subprocess.run(
            [
                sys.executable,
                "dashboard/export_kline_charts.py",
                "--candidates",
                "does-not-exist.json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("R7 legacy retirement", result.stderr)
        self.assertIn("POST /api/runs/chart-export", result.stderr)
        self.assertIn("STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT=1", result.stderr)
        self.assertNotIn("候选文件不存在", result.stdout + result.stderr)

    def test_legacy_chart_export_override_is_explicit(self) -> None:
        from legacy_compat import LEGACY_CHART_EXPORT_ENV, legacy_chart_export_enabled

        previous = os.environ.get(LEGACY_CHART_EXPORT_ENV)
        os.environ.pop(LEGACY_CHART_EXPORT_ENV, None)
        try:
            self.assertFalse(legacy_chart_export_enabled())
            os.environ[LEGACY_CHART_EXPORT_ENV] = "1"
            self.assertTrue(legacy_chart_export_enabled())
            os.environ[LEGACY_CHART_EXPORT_ENV] = "true"
            self.assertFalse(legacy_chart_export_enabled())
        finally:
            if previous is None:
                os.environ.pop(LEGACY_CHART_EXPORT_ENV, None)
            else:
                os.environ[LEGACY_CHART_EXPORT_ENV] = previous


if __name__ == "__main__":
    unittest.main()
