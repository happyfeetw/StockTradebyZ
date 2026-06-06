from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import agy_cli_review  # noqa: E402


def valid_review_payload(**overrides):
    payload = {
        "trend_reasoning": "趋势向上",
        "position_reasoning": "位置合理",
        "volume_reasoning": "量价配合",
        "abnormal_move_reasoning": "有资金异动",
        "signal_reasoning": "信号清晰",
        "classic_pattern_type": "brick_n_shape_launch",
        "classic_pattern_reasoning": "符合砖型图启动",
        "scores": {
            "trend_structure": 4,
            "price_position": 4,
            "volume_behavior": 4,
            "previous_abnormal_move": 4,
            "classic_pattern_match": 4,
        },
        "total_score": 4,
        "signal_type": "trend_start",
        "verdict": "PASS",
        "comment": "结构健康，可继续观察。",
    }
    payload.update(overrides)
    return payload


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

            def fake_run_agy(*, code: str, day_chart: Path, prompt_text: str, purpose: str = "review"):
                return subprocess.CompletedProcess(
                    args=["agy"],
                    returncode=0,
                    stdout=json.dumps(valid_review_payload(), ensure_ascii=False),
                    stderr="",
                )

            reviewer._run_agy = fake_run_agy  # type: ignore[method-assign]
            result = reviewer.review_stock("000001", chart, "prompt", strategy="brick")

        self.assertEqual(result["code"], "000001")
        self.assertEqual(result["strategy"], "brick")
        self.assertEqual(result["reviewer"], "agy-cli-experimental")
        self.assertEqual(result["model"], "Gemini 3.5 Flash (Medium)")
        self.assertEqual(result["model_evidence"]["control"], "per-call --model")
        self.assertEqual(result["json_output_mode"], "prompt-json")
        self.assertTrue(result["json_schema_valid"])
        self.assertFalse(result["json_repair_attempted"])
        self.assertFalse(result["json_repair_used"])

    def test_review_stock_repairs_invalid_json_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart = Path(tmp) / "000001_day.jpg"
            chart.write_bytes(b"fake")
            calls: list[str] = []

            def fake_run_agy(*, code: str, day_chart: Path, prompt_text: str, purpose: str = "review"):
                calls.append(purpose)
                if purpose == "review":
                    return subprocess.CompletedProcess(
                        args=["agy"],
                        returncode=0,
                        stdout='{"scores": {"trend_structure": 4',
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    args=["agy"],
                    returncode=0,
                    stdout=json.dumps(valid_review_payload(), ensure_ascii=False),
                    stderr="",
                )

            reviewer._run_agy = fake_run_agy  # type: ignore[method-assign]
            result = reviewer.review_stock("000001", chart, "prompt", strategy="brick")

        self.assertEqual(calls, ["review", "json_repair"])
        self.assertTrue(result["json_repair_attempted"])
        self.assertTrue(result["json_repair_used"])
        self.assertIn("无法从 AGY 输出提取合法 JSON", result["json_repair_reason"])

    def test_review_stock_rejects_schema_error_when_repair_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            reviewer.config["json_repair_enabled"] = False
            chart = Path(tmp) / "000001_day.jpg"
            chart.write_bytes(b"fake")

            def fake_run_agy(*, code: str, day_chart: Path, prompt_text: str, purpose: str = "review"):
                return subprocess.CompletedProcess(
                    args=["agy"],
                    returncode=0,
                    stdout=json.dumps({"scores": {"trend_structure": 4}}, ensure_ascii=False),
                    stderr="",
                )

            reviewer._run_agy = fake_run_agy  # type: ignore[method-assign]
            with self.assertRaises(agy_cli_review.AgyCliJsonContractError):
                reviewer.review_stock("000001", chart, "prompt", strategy="brick")

    def test_review_batch_parses_json_array_and_marks_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart1 = Path(tmp) / "000001_day.jpg"
            chart2 = Path(tmp) / "000002_day.jpg"
            chart1.write_bytes(b"fake")
            chart2.write_bytes(b"fake")

            def fake_run_agy(*, code: str, day_chart: Path, prompt_text: str, purpose: str = "review"):
                self.assertEqual(purpose, "batch_2")
                self.assertIn("JSON 数组", prompt_text)
                return subprocess.CompletedProcess(
                    args=["agy"],
                    returncode=0,
                    stdout=json.dumps(
                        [
                            valid_review_payload(code="000001", strategy="b1"),
                            valid_review_payload(code="000002", strategy="brick"),
                        ],
                        ensure_ascii=False,
                    ),
                    stderr="",
                )

            reviewer._run_agy = fake_run_agy  # type: ignore[method-assign]
            results = reviewer.review_batch(
                [
                    {"code": "000001", "strategy": "b1", "day_chart": chart1},
                    {"code": "000002", "strategy": "brick", "day_chart": chart2},
                ],
                "prompt",
            )

        self.assertEqual([item["code"] for item in results], ["000001", "000002"])
        self.assertEqual(results[0]["json_output_mode"], "prompt-json-array")
        self.assertEqual(results[1]["reviewer"], "agy-cli-experimental")


if __name__ == "__main__":
    unittest.main()
