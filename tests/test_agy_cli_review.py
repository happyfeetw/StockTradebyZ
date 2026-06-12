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
        "common_gate": {
            "scores": {
                "trend_qualification": 4,
                "support_stop_loss_control": 4,
                "overhead_room": 4,
                "volume_health": 4,
                "post_entry_discipline": 4,
            },
            "hard_veto": False,
            "hard_veto_reasons": [],
            "comment": "公共条件健康",
        },
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
            "model": "Gemini 3.5 Flash (High)",
            "prompt_path": prompt_path,
            "kline_dir": tmp_path,
            "output_dir": tmp_path / "review",
            "settings_path": tmp_path / "missing-settings.json",
            "auth_recovery_enabled": False,
        }
        return agy_cli_review.AgyCliReviewer(config)

    def test_build_command_passes_model_before_print_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart = Path(tmp) / "000001_day.jpg"
            chart.write_bytes(b"fake")

            cmd = reviewer._build_command(chart, "prompt text")

        self.assertEqual(cmd[0], "/bin/echo")
        self.assertEqual(cmd[1:3], ["--model", "Gemini 3.5 Flash (High)"])
        self.assertIn("--print-timeout", cmd)
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        self.assertEqual(cmd[-2:], ["--print", "prompt text"])

    def test_build_command_can_enable_permission_skip_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            reviewer.dangerously_skip_permissions = True
            chart = Path(tmp) / "000001_day.jpg"
            chart.write_bytes(b"fake")

            cmd = reviewer._build_command(chart, "prompt text")

        self.assertIn("--dangerously-skip-permissions", cmd)
        self.assertLess(cmd.index("--dangerously-skip-permissions"), cmd.index("--print"))

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
        self.assertEqual(result["reviewer"], "agy-cli")
        self.assertEqual(result["model"], "Gemini 3.5 Flash (High)")
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

    def test_review_stock_auth_output_does_not_attempt_json_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart = Path(tmp) / "000001_day.jpg"
            chart.write_bytes(b"fake")
            calls: list[str] = []

            def fake_run_agy(*, code: str, day_chart: Path, prompt_text: str, purpose: str = "review"):
                calls.append(purpose)
                return subprocess.CompletedProcess(
                    args=["agy"],
                    returncode=0,
                    stdout="Authentication required\nWaiting for authentication\nError: authentication timed out",
                    stderr="",
                )

            reviewer._run_agy = fake_run_agy  # type: ignore[method-assign]
            with self.assertRaises(agy_cli_review.AgyCliAuthError):
                reviewer.review_stock("000001", chart, "prompt", strategy="brick")

        self.assertEqual(calls, ["review"])

    def test_run_agy_can_feed_live_auth_code_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_agy = tmp_path / "fake_agy.py"
            fake_agy.write_text(
                "\n".join(
                    [
                        f"#!{sys.executable}",
                        "import sys",
                        "print('Authentication required. Please visit the URL to log in:', flush=True)",
                        "print('  https://accounts.google.com/o/oauth2/auth?state=test', flush=True)",
                        "print('Or, paste the authorization code here and press Enter:', flush=True)",
                        "code = sys.stdin.readline().strip()",
                        "print('CODE=' + code, flush=True)",
                    ]
                ),
                encoding="utf-8",
            )
            fake_agy.chmod(0o755)
            reviewer = self.make_reviewer(tmp_path)
            reviewer.agy_bin = str(fake_agy)
            reviewer.raw_log_root = tmp_path / "raw"
            reviewer.stdin_mode = "pipe"
            reviewer.config["stdin_mode"] = "pipe"
            reviewer.config["auth_code_wait_seconds"] = 2
            reviewer.config["auth_code_poll_interval"] = 0.2
            code_path = reviewer.raw_log_root / "auth_code.txt"
            code_path.parent.mkdir(parents=True, exist_ok=True)
            code_path.write_text("auth-code-123\n", encoding="utf-8")
            chart = tmp_path / "000001_day.jpg"
            chart.write_bytes(b"fake")

            result = reviewer._run_agy(
                code="000001",
                day_chart=chart,
                prompt_text="prompt",
                purpose="review",
            )

            status = json.loads((reviewer.raw_log_root / "auth_recovery_status.json").read_text(encoding="utf-8"))
            code_file_removed = not code_path.exists()

        self.assertEqual(result.returncode, 0)
        self.assertIn("CODE=auth-code-123", result.stdout)
        self.assertTrue(code_file_removed)
        self.assertEqual(status["status"], "code_sent")

    def test_run_agy_uses_devnull_stdin_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_agy = tmp_path / "fake_agy.py"
            fake_agy.write_text(
                "\n".join(
                    [
                        f"#!{sys.executable}",
                        "import sys",
                        "data = sys.stdin.read()",
                        "print('STDIN_EOF=' + str(data == ''), flush=True)",
                    ]
                ),
                encoding="utf-8",
            )
            fake_agy.chmod(0o755)
            reviewer = self.make_reviewer(tmp_path)
            reviewer.agy_bin = str(fake_agy)
            reviewer.timeout_seconds = 1
            reviewer.raw_log_root = tmp_path / "raw"
            chart = tmp_path / "000001_day.jpg"
            chart.write_bytes(b"fake")

            result = reviewer._run_agy(
                code="000001",
                day_chart=chart,
                prompt_text="prompt",
                purpose="review",
            )
            meta_path = next(reviewer.raw_log_root.glob("*/meta.json"))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0)
        self.assertIn("STDIN_EOF=True", result.stdout)
        self.assertEqual(meta["stdin_mode"], "devnull")

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
        self.assertEqual(results[1]["reviewer"], "agy-cli")

    def test_review_batch_auth_output_does_not_fallback_to_single(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart1 = Path(tmp) / "000001_day.jpg"
            chart2 = Path(tmp) / "000002_day.jpg"
            chart1.write_bytes(b"fake")
            chart2.write_bytes(b"fake")
            calls: list[str] = []

            def fake_run_agy(*, code: str, day_chart: Path, prompt_text: str, purpose: str = "review"):
                calls.append(purpose)
                return subprocess.CompletedProcess(
                    args=["agy"],
                    returncode=0,
                    stdout="Print mode: not authenticated\nAuthentication required\nError: auth timed out",
                    stderr="",
                )

            def fake_review_stock(*, code: str, day_chart: Path, prompt: str, strategy: str = ""):
                raise AssertionError("auth failures must not fall back to single-stock review")

            reviewer._run_agy = fake_run_agy  # type: ignore[method-assign]
            reviewer.review_stock = fake_review_stock  # type: ignore[method-assign]
            items = [
                {
                    "index": 1,
                    "code": "000001",
                    "strategy": "b1",
                    "review_key": "000001__b1",
                    "day_chart": chart1,
                    "out_file": Path(tmp) / "000001__b1.json",
                },
                {
                    "index": 2,
                    "code": "000002",
                    "strategy": "b1",
                    "review_key": "000002__b1",
                    "day_chart": chart2,
                    "out_file": Path(tmp) / "000002__b1.json",
                },
            ]

            with self.assertRaises(agy_cli_review.AgyCliAuthError):
                reviewer._review_batch_items(items, total_candidates=2)

        self.assertEqual(calls, ["batch_2"])

    def test_review_batch_timeout_does_not_fallback_to_single(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart1 = Path(tmp) / "000001_day.jpg"
            chart2 = Path(tmp) / "000002_day.jpg"
            chart1.write_bytes(b"fake")
            chart2.write_bytes(b"fake")
            calls: list[str] = []

            def fake_run_agy(*, code: str, day_chart: Path, prompt_text: str, purpose: str = "review"):
                calls.append(purpose)
                return subprocess.CompletedProcess(
                    args=["agy"],
                    returncode=-9,
                    stdout="",
                    stderr="AGY CLI timed out after 900s",
                )

            def fake_review_stock(*, code: str, day_chart: Path, prompt: str, strategy: str = ""):
                raise AssertionError("timeouts must not fall back to single-stock review")

            reviewer._run_agy = fake_run_agy  # type: ignore[method-assign]
            reviewer.review_stock = fake_review_stock  # type: ignore[method-assign]
            items = [
                {
                    "index": 1,
                    "code": "000001",
                    "strategy": "b1",
                    "review_key": "000001_b1",
                    "day_chart": chart1,
                    "out_file": Path(tmp) / "000001_b1.json",
                },
                {
                    "index": 2,
                    "code": "000002",
                    "strategy": "b1",
                    "review_key": "000002_b1",
                    "day_chart": chart2,
                    "out_file": Path(tmp) / "000002_b1.json",
                },
            ]

            with self.assertRaises(agy_cli_review.AgyCliTimeoutError):
                reviewer._review_batch_items(items, total_candidates=2)

        self.assertEqual(calls, ["batch_2"])

    def test_batch_items_waits_for_auth_recovery_and_retries_same_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            reviewer.config["auth_recovery_enabled"] = True
            reviewer.config["auth_recovery_wait_seconds"] = 1
            reviewer.config["auth_recovery_check_interval"] = 1
            chart1 = Path(tmp) / "000001_day.jpg"
            chart2 = Path(tmp) / "000002_day.jpg"
            chart1.write_bytes(b"fake")
            chart2.write_bytes(b"fake")
            calls: list[str] = []
            probes: list[str] = []

            def fake_review_batch(*, items: list[dict], prompt: str):
                calls.append(",".join(str(item["code"]) for item in items))
                if len(calls) == 1:
                    raise agy_cli_review.AgyCliAuthError(
                        "AGY CLI 批量调用认证失败: Authentication required. "
                        "https://accounts.google.com/o/oauth2/auth?state=test"
                    )
                return [
                    valid_review_payload(code="000001", strategy="b1"),
                    valid_review_payload(code="000002", strategy="b1"),
                ]

            def fake_probe():
                probes.append("probe")
                return True, "OK"

            reviewer.review_batch = fake_review_batch  # type: ignore[method-assign]
            reviewer._probe_auth_recovered = fake_probe  # type: ignore[method-assign]
            items = [
                {
                    "index": 1,
                    "code": "000001",
                    "strategy": "b1",
                    "review_key": "000001_b1",
                    "day_chart": chart1,
                    "out_file": Path(tmp) / "000001_b1.json",
                },
                {
                    "index": 2,
                    "code": "000002",
                    "strategy": "b1",
                    "review_key": "000002_b1",
                    "day_chart": chart2,
                    "out_file": Path(tmp) / "000002_b1.json",
                },
            ]

            results, failed = reviewer._review_batch_items(items, total_candidates=2)
            first_written = (Path(tmp) / "000001_b1.json").exists()
            second_written = (Path(tmp) / "000002_b1.json").exists()

        self.assertEqual(calls, ["000001,000002", "000001,000002"])
        self.assertEqual(probes, ["probe"])
        self.assertEqual([item["code"] for item in results], ["000001", "000002"])
        self.assertEqual(failed, [])
        self.assertTrue(first_written)
        self.assertTrue(second_written)

    def test_single_items_auth_error_stops_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart1 = Path(tmp) / "000001_day.jpg"
            chart2 = Path(tmp) / "000002_day.jpg"
            chart1.write_bytes(b"fake")
            chart2.write_bytes(b"fake")
            calls: list[str] = []

            def fake_review_stock(*, code: str, day_chart: Path, prompt: str, strategy: str = ""):
                calls.append(code)
                raise agy_cli_review.AgyCliAuthError("AGY CLI 认证失败")

            reviewer.review_stock = fake_review_stock  # type: ignore[method-assign]
            items = [
                {
                    "index": 1,
                    "code": "000001",
                    "strategy": "b1",
                    "review_key": "000001__b1",
                    "day_chart": chart1,
                    "out_file": Path(tmp) / "000001__b1.json",
                },
                {
                    "index": 2,
                    "code": "000002",
                    "strategy": "b1",
                    "review_key": "000002__b1",
                    "day_chart": chart2,
                    "out_file": Path(tmp) / "000002__b1.json",
                },
            ]

            with self.assertRaises(agy_cli_review.AgyCliAuthError):
                reviewer._review_single_items(items, total_candidates=2)

        self.assertEqual(calls, ["000001"])

    def test_single_items_timeout_stops_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self.make_reviewer(Path(tmp))
            chart1 = Path(tmp) / "000001_day.jpg"
            chart2 = Path(tmp) / "000002_day.jpg"
            chart1.write_bytes(b"fake")
            chart2.write_bytes(b"fake")
            calls: list[str] = []

            def fake_review_stock(*, code: str, day_chart: Path, prompt: str, strategy: str = ""):
                calls.append(code)
                raise agy_cli_review.AgyCliTimeoutError("AGY CLI timed out after 900s")

            reviewer.review_stock = fake_review_stock  # type: ignore[method-assign]
            items = [
                {
                    "index": 1,
                    "code": "000001",
                    "strategy": "b1",
                    "review_key": "000001_b1",
                    "day_chart": chart1,
                    "out_file": Path(tmp) / "000001_b1.json",
                },
                {
                    "index": 2,
                    "code": "000002",
                    "strategy": "b1",
                    "review_key": "000002_b1",
                    "day_chart": chart2,
                    "out_file": Path(tmp) / "000002_b1.json",
                },
            ]

            with self.assertRaises(agy_cli_review.AgyCliTimeoutError):
                reviewer._review_single_items(items, total_candidates=2)

        self.assertEqual(calls, ["000001"])


if __name__ == "__main__":
    unittest.main()
