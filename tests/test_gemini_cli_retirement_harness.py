from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GeminiCliRetirementHarnessTests(unittest.TestCase):
    def test_gemini_cli_reviewer_exits_before_config_load_by_default(self) -> None:
        env = os.environ.copy()
        env.pop("STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW", None)

        result = subprocess.run(
            [
                sys.executable,
                "agent/gemini_cli_review.py",
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
        self.assertIn("R7 Gemini CLI reviewer retirement", combined_output)
        self.assertIn("POST /api/runs/review/provider", combined_output)
        self.assertIn("provider=gemini-cli", combined_output)
        self.assertIn("STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1", combined_output)
        self.assertNotIn("does-not-exist.yaml", combined_output)

    def test_gemini_cli_guard_runs_before_legacy_config_and_runner(self) -> None:
        text = (ROOT / "agent" / "gemini_cli_review.py").read_text(encoding="utf-8")

        guard_index = text.index("if not legacy_gemini_cli_review_enabled()")
        config_index = text.index("config = load_config", guard_index)
        runner_index = text.index("reviewer.run()", guard_index)

        self.assertLess(guard_index, config_index)
        self.assertLess(guard_index, runner_index)
        self.assertIn("LEGACY_GEMINI_CLI_REVIEW_RETIRED_NOTICE", text)
        self.assertIn("raise SystemExit(2)", text)

    def test_gemini_cli_helpers_remain_importable_for_parity_tests(self) -> None:
        sys.path.insert(0, str(ROOT / "agent"))
        try:
            from gemini_cli_review import GeminiCliReviewer, _is_transient_error_text
        finally:
            sys.path.pop(0)

        self.assertTrue(callable(GeminiCliReviewer))
        self.assertTrue(_is_transient_error_text("ERR_STREAM_PREMATURE_CLOSE"))
        self.assertTrue(_is_transient_error_text("HTTP 429 No capacity available"))

    def test_gemini_cli_rollback_flag_is_explicit_and_suppressed_by_default(self) -> None:
        from legacy_compat import LEGACY_GEMINI_CLI_REVIEW_ENV, legacy_gemini_cli_review_enabled

        previous = os.environ.get(LEGACY_GEMINI_CLI_REVIEW_ENV)
        os.environ.pop(LEGACY_GEMINI_CLI_REVIEW_ENV, None)
        try:
            self.assertFalse(legacy_gemini_cli_review_enabled())
            os.environ[LEGACY_GEMINI_CLI_REVIEW_ENV] = "1"
            self.assertTrue(legacy_gemini_cli_review_enabled())
            os.environ[LEGACY_GEMINI_CLI_REVIEW_ENV] = "true"
            self.assertFalse(legacy_gemini_cli_review_enabled())
        finally:
            if previous is None:
                os.environ.pop(LEGACY_GEMINI_CLI_REVIEW_ENV, None)
            else:
                os.environ[LEGACY_GEMINI_CLI_REVIEW_ENV] = previous


if __name__ == "__main__":
    unittest.main()
