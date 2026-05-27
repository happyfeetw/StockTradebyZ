from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from stocktrade_api.services.gemini_cli_provider import (  # noqa: E402
    GeminiCliCompleted,
    GeminiCliProviderError,
    GeminiCliReviewProviderExecutor,
)
from stocktrade_api.services.review_provider_runs import ReviewProviderInput, ReviewProviderItem  # noqa: E402


class FakeGeminiRunner:
    def __init__(self, responses: list[GeminiCliCompleted]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        prompt_text: str,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> GeminiCliCompleted:
        self.calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "prompt_text": prompt_text,
                "timeout_seconds": timeout_seconds,
                "env": env,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected Gemini CLI call")
        return self.responses.pop(0)


def provider_input(tmp: Path, *, provider_config: dict | None = None) -> ReviewProviderInput:
    artifact_root = tmp / "artifacts"
    chart_dir = artifact_root / "run-chart" / "charts" / "batch-history"
    chart_dir.mkdir(parents=True)
    (chart_dir / "000001_day.jpg").write_bytes(b"chart-000001")
    (chart_dir / "000002_day.jpg").write_bytes(b"chart-000002")
    return ReviewProviderInput(
        candidate_batch_id="batch-history",
        pick_date="2026-05-25",
        provider="gemini-cli",
        reviewer="gemini-cli",
        items=[
            ReviewProviderItem(
                candidate_id=1,
                code="000001",
                strategy="b2",
                review_key="000001_b2",
                candidate={"batch_id": "batch-history", "code": "000001", "strategy": "b2"},
                chart_artifact_id="artifact-chart-history-000001",
                chart_path="run-chart/charts/batch-history/000001_day.jpg",
            ),
            ReviewProviderItem(
                candidate_id=2,
                code="000002",
                strategy="brick",
                review_key="000002_brick",
                candidate={"batch_id": "batch-history", "code": "000002", "strategy": "brick"},
                chart_artifact_id="artifact-chart-history-000002",
                chart_path="run-chart/charts/batch-history/000002_day.jpg",
            ),
        ],
        provider_config={
            "prompt": "Score the chart.",
            "validate_gemini_bin": False,
            "retry_backoff_seconds": [0],
            "retry_jitter_ratio": 0,
            "request_delay": 0,
            "batch_size": 2,
            "timeout_seconds": 12,
            "raw_log_dir": str(tmp / "gemini-runs"),
            "checkpoint_path": str(tmp / "checkpoint.json"),
            "result_cache_dir": str(tmp / "cache"),
            "usage_file": str(tmp / "usage.json"),
            **(provider_config or {}),
        },
    )


def stream_json(items: list[dict]) -> str:
    return json.dumps({"role": "assistant", "content": [{"text": json.dumps(items)}]}) + "\n"


def review_payload(code: str) -> dict:
    return {
        "code": code,
        "signal_type": "gemini-cli",
        "comment": f"{code} ok",
        "scores": {
            "trend_structure": 5,
            "price_position": 5,
            "volume_behavior": 5,
            "previous_abnormal_move": 5,
            "classic_pattern_match": 5,
        },
    }


class GeminiCliProviderContractTests(unittest.TestCase):
    def test_provider_retries_rate_limit_and_writes_checkpoint_raw_logs_and_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            request = provider_input(tmp)
            runner = FakeGeminiRunner(
                [
                    GeminiCliCompleted(returncode=1, stdout="", stderr="HTTP 429 rate limit"),
                    GeminiCliCompleted(
                        returncode=0,
                        stdout=stream_json([review_payload("000001"), review_payload("000002")]),
                        stderr="",
                    ),
                ]
            )
            executor = GeminiCliReviewProviderExecutor(
                artifact_root=tmp / "artifacts",
                runner=runner,
                sleeper=lambda _seconds: None,
            )

            results = executor.run(request)

            self.assertEqual(len(runner.calls), 2)
            self.assertEqual([item["review_key"] for item in results], ["000001_b2", "000002_brick"])
            self.assertEqual(results[0]["strategy"], "b2")
            self.assertEqual(results[0]["reviewer"], "gemini-cli")
            self.assertIn("provider_raw_log_dir", results[0])
            self.assertIn("--output-format", runner.calls[0]["cmd"])
            self.assertIn("@000001_day.jpg", runner.calls[0]["prompt_text"])

            checkpoint = json.loads((tmp / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["status"], "finished")
            usage = json.loads((tmp / "usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["count"], 2)
            raw_dirs = sorted((tmp / "gemini-runs").iterdir())
            self.assertEqual(len(raw_dirs), 2)
            self.assertEqual(json.loads((raw_dirs[0] / "meta.json").read_text(encoding="utf-8"))["exit_code"], 1)
            self.assertEqual(json.loads((raw_dirs[1] / "meta.json").read_text(encoding="utf-8"))["status"], "finished")
            self.assertEqual(
                json.loads((tmp / "cache" / "000001_b2.json").read_text(encoding="utf-8"))["code"],
                "000001",
            )

    def test_provider_uses_skip_existing_result_cache_without_cli_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            request = provider_input(tmp)
            cache = tmp / "cache"
            cache.mkdir()
            (cache / "000001_b2.json").write_text(
                json.dumps({**review_payload("000001"), "strategy": "b2", "review_key": "000001_b2"}),
                encoding="utf-8",
            )
            (cache / "000002_brick.json").write_text(
                json.dumps({**review_payload("000002"), "strategy": "brick", "review_key": "000002_brick"}),
                encoding="utf-8",
            )
            runner = FakeGeminiRunner([])
            executor = GeminiCliReviewProviderExecutor(
                artifact_root=tmp / "artifacts",
                runner=runner,
                sleeper=lambda _seconds: None,
            )

            results = executor.run(request)

            self.assertEqual(len(runner.calls), 0)
            self.assertEqual([item["review_key"] for item in results], ["000001_b2", "000002_brick"])
            self.assertEqual(json.loads((tmp / "checkpoint.json").read_text(encoding="utf-8"))["status"], "finished")

    def test_provider_rejects_batch_order_mismatch_when_fallback_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            request = provider_input(tmp, provider_config={"fallback_to_single_on_batch_error": False})
            runner = FakeGeminiRunner(
                [
                    GeminiCliCompleted(
                        returncode=0,
                        stdout=stream_json([review_payload("000002"), review_payload("000001")]),
                        stderr="",
                    )
                ]
            )
            executor = GeminiCliReviewProviderExecutor(
                artifact_root=tmp / "artifacts",
                runner=runner,
                sleeper=lambda _seconds: None,
            )

            with self.assertRaisesRegex(GeminiCliProviderError, "order mismatch"):
                executor.run(request)


if __name__ == "__main__":
    unittest.main()
