from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import agy_cli_review  # noqa: E402


class AgyCliReviewerTests(unittest.TestCase):
    def make_reviewer(self, tmp_path: Path) -> agy_cli_review.AgyCliReviewer:
        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("评分规则", encoding="utf-8")
        config = {
            **agy_cli_review.DEFAULT_CONFIG,
            "agy_bin": "/bin/echo",
            "model": "Gemini 3.5 Flash (Medium)",
            "prompt_path": prompt_path,
            "kline_dir": tmp_path,
            "output_dir": tmp_path / "review",
            "settings_path": tmp_path / "missing-settings.json",
        }
        return agy_cli_review.AgyCliReviewer(config)

    def test_build_command_passes_model_before_print_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart = Path(tmp) / "000001_day.jpg"
            chart.write_bytes(b"fake")

            cmd = reviewer._build_command(chart, "prompt text")

        self.assertEqual(cmd[0], "/bin/echo")
        self.assertEqual(cmd[1:3], ["--model", "Gemini 3.5 Flash (Medium)"])
        self.assertIn("--print-timeout", cmd)
        self.assertEqual(cmd[-2:], ["--print", "prompt text"])

    def test_review_stock_marks_agy_metadata_and_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart = Path(tmp) / "000001_day.jpg"
            chart.write_bytes(b"fake")

            def fake_run_agy(*, code: str, day_chart: Path, prompt_text: str):
                return subprocess.CompletedProcess(
                    args=["agy"],
                    returncode=0,
                    stdout='{"scores":{"trend_structure":4,"price_position":4,"volume_behavior":4,"previous_abnormal_move":4,"classic_pattern_match":4},"total_score":4,"verdict":"PASS"}',
                    stderr="",
                )

            reviewer._run_agy = fake_run_agy  # type: ignore[method-assign]
            result = reviewer.review_stock("000001", chart, "prompt", strategy="brick")

        self.assertEqual(result["code"], "000001")
        self.assertEqual(result["strategy"], "brick")
        self.assertEqual(result["reviewer"], "agy-cli-experimental")
        self.assertEqual(result["model"], "Gemini 3.5 Flash (Medium)")
        self.assertEqual(result["model_evidence"]["control"], "per-call --model")


if __name__ == "__main__":
    unittest.main()
