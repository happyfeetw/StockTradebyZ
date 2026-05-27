from __future__ import annotations

import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GeminiApiRetirementHarnessTests(unittest.TestCase):
    def test_gemini_api_script_stops_before_legacy_review_execution_by_default(self) -> None:
        text = (ROOT / "agent" / "gemini_review.py").read_text(encoding="utf-8")

        guard_index = text.index("if not legacy_gemini_api_review_enabled()")
        load_config_index = text.index("config = load_config", guard_index)
        reviewer_index = text.index("reviewer = GeminiReviewer", guard_index)

        self.assertLess(guard_index, load_config_index)
        self.assertLess(guard_index, reviewer_index)
        self.assertIn("LEGACY_GEMINI_API_REVIEW_RETIRED_NOTICE", text)
        self.assertIn("POST /api/runs/review/provider", text)
        self.assertIn("return 2", text)

    def test_gemini_api_rollback_flag_is_explicit_and_suppressed_by_default(self) -> None:
        from legacy_compat import LEGACY_GEMINI_API_REVIEW_ENV, legacy_gemini_api_review_enabled

        previous = os.environ.get(LEGACY_GEMINI_API_REVIEW_ENV)
        os.environ.pop(LEGACY_GEMINI_API_REVIEW_ENV, None)
        try:
            self.assertFalse(legacy_gemini_api_review_enabled())
            os.environ[LEGACY_GEMINI_API_REVIEW_ENV] = "1"
            self.assertTrue(legacy_gemini_api_review_enabled())
            os.environ[LEGACY_GEMINI_API_REVIEW_ENV] = "true"
            self.assertFalse(legacy_gemini_api_review_enabled())
        finally:
            if previous is None:
                os.environ.pop(LEGACY_GEMINI_API_REVIEW_ENV, None)
            else:
                os.environ[LEGACY_GEMINI_API_REVIEW_ENV] = previous


if __name__ == "__main__":
    unittest.main()
