from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import codex_cli_review  # noqa: E402


def valid_review_payload(**overrides):
    payload = {
        "code": "000001",
        "strategy": "b1",
        "trend_reasoning": "趋势向上",
        "position_reasoning": "位置合理",
        "volume_reasoning": "量价配合",
        "abnormal_move_reasoning": "有资金异动",
        "signal_reasoning": "信号清晰",
        "classic_pattern_type": "b1_type1_low_breakout_pullback",
        "classic_pattern_reasoning": "符合经典图形",
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


class CodexCliReviewerTests(unittest.TestCase):
    def make_reviewer(self, tmp_path: Path) -> codex_cli_review.CodexCliReviewer:
        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("评分规则", encoding="utf-8")
        config = {
            **codex_cli_review.DEFAULT_CONFIG,
            "codex_bin": "/bin/echo",
            "prompt_path": prompt_path,
            "kline_dir": tmp_path,
            "output_dir": tmp_path / "review",
        }
        return codex_cli_review.CodexCliReviewer(config)

    def test_build_command_pins_model_reasoning_and_standard_speed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart = Path(tmp) / "000001_day.jpg"
            schema = Path(tmp) / "schema.json"
            output = Path(tmp) / "last_message.json"
            chart.write_bytes(b"fake")
            schema.write_text("{}", encoding="utf-8")

            cmd = reviewer._build_command(
                image_paths=[chart],
                schema_path=schema,
                output_path=output,
                work_dir=Path(tmp),
                prompt="prompt text",
            )

        self.assertEqual(cmd[:4], ["/bin/echo", "--ask-for-approval", "never", "exec"])
        self.assertIn("--ignore-user-config", cmd)
        self.assertIn("--ignore-rules", cmd)
        self.assertIn("gpt-5.5", cmd)
        self.assertIn('model_reasoning_effort="high"', cmd)
        self.assertIn("fast_default_opt_out=true", cmd)
        self.assertIn("--output-schema", cmd)
        self.assertEqual(cmd[-1], "prompt text")

    def test_parse_result_marks_fixed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            result = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout=json.dumps({"reviews": [valid_review_payload()]}, ensure_ascii=False),
                stderr="",
            )

            parsed = reviewer._parse_result(
                result,
                items=[{"code": "000001", "strategy": "b1"}],
            )

        self.assertEqual(parsed[0]["reviewer"], "codex-cli")
        self.assertEqual(parsed[0]["model"], "gpt-5.5")
        self.assertEqual(parsed[0]["model_profile"], "gpt-5.5-high-standard")
        self.assertEqual(parsed[0]["reasoning_effort"], "high")
        self.assertEqual(parsed[0]["speed_tier"], "standard")
        self.assertEqual(parsed[0]["json_output_mode"], "output-schema")

    def test_output_schema_is_strict_for_codex_structured_output(self) -> None:
        schema = codex_cli_review.output_schema(2)
        review_item = schema["properties"]["reviews"]["items"]
        scores = review_item["properties"]["scores"]

        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(review_item["additionalProperties"])
        self.assertFalse(scores["additionalProperties"])
        self.assertIn("strategy", review_item["required"])
        self.assertEqual(set(review_item["properties"]), set(review_item["required"]))
        self.assertEqual(set(scores["properties"]), set(scores["required"]))

    def test_review_batch_prefers_output_file_over_echoed_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            items = []
            payloads = []
            for index in range(5):
                code = f"00000{index}"
                chart = Path(tmp) / f"{code}_day.jpg"
                chart.write_bytes(b"fake image")
                items.append({"code": code, "strategy": "b1", "day_chart": chart})
                payloads.append(valid_review_payload(code=code))
            structured = json.dumps({"reviews": payloads}, ensure_ascii=False)

            def fake_run(cmd, **kwargs):
                output_path = Path(cmd[cmd.index("-o") + 1])
                output_path.write_text(structured, encoding="utf-8")
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=structured + "\n",
                    stderr="tokens used\n100\n",
                )

            with patch.object(codex_cli_review.subprocess, "run", side_effect=fake_run):
                parsed = reviewer.review_batch(items, "评分规则")

        self.assertEqual([item["code"] for item in parsed], [item["code"] for item in items])
        self.assertTrue(all(item["json_schema_valid"] for item in parsed))

    def test_load_config_ignores_model_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex.yaml"
            prompt = Path(tmp) / "prompt.md"
            prompt.write_text("prompt", encoding="utf-8")
            path.write_text(
                "\n".join(
                    [
                        f"prompt_path: {prompt}",
                        f"kline_dir: {tmp}",
                        f"output_dir: {tmp}/review",
                        "candidates: data/candidates/candidates_latest.json",
                        "model: not-gpt",
                        "reasoning_effort: low",
                        "speed_tier: fast",
                    ]
                ),
                encoding="utf-8",
            )

            cfg = codex_cli_review.load_config(path)

        self.assertEqual(cfg["model"], "gpt-5.5")
        self.assertEqual(cfg["reasoning_effort"], "high")
        self.assertEqual(cfg["speed_tier"], "standard")


if __name__ == "__main__":
    unittest.main()
