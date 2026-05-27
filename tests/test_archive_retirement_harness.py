from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ArchiveRetirementHarnessTests(unittest.TestCase):
    def test_archive_writer_exits_before_legacy_file_reads_by_default(self) -> None:
        env = os.environ.copy()
        env.pop("STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS", None)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pipeline.archive_results",
                "--candidates",
                "does-not-exist.json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        combined_output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2)
        self.assertIn("R7 archive writer retirement", combined_output)
        self.assertIn("POST /api/runs/archive", combined_output)
        self.assertIn("STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS=1", combined_output)
        self.assertNotIn("候选文件不存在", combined_output)

    def test_archive_guard_runs_before_candidate_read_and_history_write(self) -> None:
        text = (ROOT / "pipeline" / "archive_results.py").read_text(encoding="utf-8")

        guard_index = text.index("if not legacy_archive_results_enabled()")
        candidate_read_index = text.index("candidates_data = load_json", guard_index)
        history_write_index = text.index('atomic_write_json(date_dir / "all.json"', guard_index)

        self.assertLess(guard_index, candidate_read_index)
        self.assertLess(guard_index, history_write_index)
        self.assertIn("LEGACY_ARCHIVE_RESULTS_RETIRED_NOTICE", text)
        self.assertIn("raise SystemExit(2)", text)

    def test_archive_helpers_remain_importable_for_parity_tests(self) -> None:
        sys.path.insert(0, str(ROOT / "pipeline"))
        try:
            from archive_results import build_rows, build_summary, review_matches_strategy
        finally:
            sys.path.pop(0)

        self.assertTrue(callable(build_rows))
        self.assertTrue(callable(build_summary))
        self.assertTrue(review_matches_strategy({"strategy": "b2"}, "000001", "b2"))

    def test_archive_rollback_flag_is_explicit_and_suppressed_by_default(self) -> None:
        from legacy_compat import LEGACY_ARCHIVE_RESULTS_ENV, legacy_archive_results_enabled

        previous = os.environ.get(LEGACY_ARCHIVE_RESULTS_ENV)
        os.environ.pop(LEGACY_ARCHIVE_RESULTS_ENV, None)
        try:
            self.assertFalse(legacy_archive_results_enabled())
            os.environ[LEGACY_ARCHIVE_RESULTS_ENV] = "1"
            self.assertTrue(legacy_archive_results_enabled())
            os.environ[LEGACY_ARCHIVE_RESULTS_ENV] = "true"
            self.assertFalse(legacy_archive_results_enabled())
        finally:
            if previous is None:
                os.environ.pop(LEGACY_ARCHIVE_RESULTS_ENV, None)
            else:
                os.environ[LEGACY_ARCHIVE_RESULTS_ENV] = previous


if __name__ == "__main__":
    unittest.main()
