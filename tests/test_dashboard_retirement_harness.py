from __future__ import annotations

import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DashboardRetirementHarnessTests(unittest.TestCase):
    def test_dashboard_app_stops_before_loading_legacy_chart_surface_by_default(self) -> None:
        text = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")

        guard_index = text.index("if not legacy_dashboard_enabled()")
        stop_index = text.index("st.stop()", guard_index)
        chart_import_index = text.index("from components.charts import make_daily_chart")

        self.assertLess(guard_index, chart_import_index)
        self.assertLess(stop_index, chart_import_index)
        self.assertIn("LEGACY_DASHBOARD_RETIRED_NOTICE", text)
        self.assertIn("LEGACY_DASHBOARD_ENV", text)

    def test_legacy_dashboard_flag_is_explicit_and_suppressed_by_default(self) -> None:
        from legacy_compat import LEGACY_DASHBOARD_ENV, legacy_dashboard_enabled

        previous = os.environ.get(LEGACY_DASHBOARD_ENV)
        os.environ.pop(LEGACY_DASHBOARD_ENV, None)
        try:
            self.assertFalse(legacy_dashboard_enabled())
            os.environ[LEGACY_DASHBOARD_ENV] = "1"
            self.assertTrue(legacy_dashboard_enabled())
            os.environ[LEGACY_DASHBOARD_ENV] = "true"
            self.assertFalse(legacy_dashboard_enabled())
        finally:
            if previous is None:
                os.environ.pop(LEGACY_DASHBOARD_ENV, None)
            else:
                os.environ[LEGACY_DASHBOARD_ENV] = previous


if __name__ == "__main__":
    unittest.main()
