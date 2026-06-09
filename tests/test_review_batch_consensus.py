from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import multi_model_review  # noqa: E402
from pipeline.review_batch import freeze_review_batch  # noqa: E402
from pipeline.review_consensus import build_consensus  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def review_payload(code: str, strategy: str, score: float, verdict: str = "PASS") -> dict:
    return {
        "code": code,
        "strategy": strategy,
        "review_key": f"{code}_{strategy}",
        "reviewer": "test",
        "model": "test-model",
        "total_score": score,
        "verdict": verdict,
        "comment": "ok",
    }


class ReviewBatchConsensusTests(unittest.TestCase):
    def test_freeze_review_batch_records_manifest_and_frozen_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            candidates_path = project / "data" / "candidates" / "candidates_latest.json"
            payload = {
                "pick_date": "2026-06-01",
                "meta": {"strategy_candidate_counts": {"b1": 1, "brick": 1}},
                "candidates": [
                    {"code": "000001", "strategy": "b1"},
                    {"code": "000002", "strategy": "brick"},
                ],
            }
            write_json(candidates_path, payload)
            kline_dir = project / "data" / "kline"
            (kline_dir / "2026-06-01").mkdir(parents=True)
            (kline_dir / "2026-06-01" / "000001_day.jpg").write_bytes(b"fake")
            (kline_dir / "2026-06-01" / "000002_day.png").write_bytes(b"fake")
            prompt = project / "agent" / "prompt.md"
            prompt.parent.mkdir(parents=True)
            prompt.write_text("prompt", encoding="utf-8")

            manifest = freeze_review_batch(
                candidates_path=candidates_path,
                batch_root=project / "data" / "review_batches",
                kline_dir=kline_dir,
                prompt_path=prompt,
                expected_strategies=["b1", "brick"],
            )

            self.assertTrue(manifest["complete"])
            frozen = json.loads(Path(manifest["candidates_file"]).read_text(encoding="utf-8"))
            self.assertEqual(frozen["meta"]["review_batch_id"], manifest["batch_id"])
            self.assertEqual(manifest["actual_strategy_counts"], {"b1": 1, "brick": 1})

    def test_freeze_review_batch_strict_rejects_missing_expected_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            candidates_path = project / "candidates.json"
            write_json(candidates_path, {"pick_date": "2026-06-01", "candidates": [{"code": "000001", "strategy": "b1"}]})
            kline_dir = project / "kline"
            (kline_dir / "2026-06-01").mkdir(parents=True)
            (kline_dir / "2026-06-01" / "000001_day.jpg").write_bytes(b"fake")
            prompt = project / "prompt.md"
            prompt.write_text("prompt", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                freeze_review_batch(
                    candidates_path=candidates_path,
                    batch_root=project / "batches",
                    kline_dir=kline_dir,
                    prompt_path=prompt,
                    expected_strategies=["b1", "b2"],
                )

    def test_build_consensus_groups_all_recommended_and_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            candidates_path = project / "batches" / "batch1" / "candidates.json"
            write_json(
                candidates_path,
                {
                    "pick_date": "2026-06-01",
                    "candidates": [
                        {"code": "000001", "strategy": "b1"},
                        {"code": "000002", "strategy": "brick"},
                    ],
                },
            )
            manifest = {
                "batch_id": "batch1",
                "pick_date": "2026-06-01",
                "candidates_file": str(candidates_path),
            }
            out_a = project / "runs" / "a"
            out_b = project / "runs" / "b"
            write_json(out_a / "2026-06-01" / "000001_b1.json", review_payload("000001", "b1", 4.2))
            write_json(out_b / "2026-06-01" / "000001_b1.json", review_payload("000001", "b1", 4.1))
            write_json(out_a / "2026-06-01" / "000002_brick.json", review_payload("000002", "brick", 4.5))

            summary = build_consensus(
                batch_manifest=manifest,
                run_specs=[
                    {"model_key": "model/a", "reviewer": "a", "model": "a", "output_dir": str(out_a)},
                    {"model_key": "model/b", "reviewer": "b", "model": "b", "output_dir": str(out_b)},
                ],
                output_dir=project / "consensus" / "batch1",
                threshold=4.0,
            )

            self.assertFalse(summary["complete"])
            self.assertEqual(summary["decision_bucket_counts"]["all_models_recommended"], 1)
            self.assertEqual(summary["decision_bucket_counts"]["incomplete"], 1)
            decisions = json.loads((project / "consensus" / "batch1" / "decisions.json").read_text(encoding="utf-8"))
            self.assertTrue(decisions[0]["all_models_recommended"])
            self.assertEqual(decisions[1]["missing_models"], ["model/b"])

    def test_build_consensus_uses_strategy_profile_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            candidates_path = project / "batches" / "batch1" / "candidates.json"
            write_json(
                candidates_path,
                {
                    "pick_date": "2026-06-01",
                    "candidates": [
                        {"code": "000001", "strategy": "brick"},
                    ],
                },
            )
            manifest = {
                "batch_id": "batch1",
                "pick_date": "2026-06-01",
                "candidates_file": str(candidates_path),
            }
            out_a = project / "runs" / "a"
            out_b = project / "runs" / "b"
            write_json(out_a / "2026-06-01" / "000001_brick.json", review_payload("000001", "brick", 4.1))
            write_json(out_b / "2026-06-01" / "000001_brick.json", review_payload("000001", "brick", 4.3))

            summary = build_consensus(
                batch_manifest=manifest,
                run_specs=[
                    {"model_key": "model/a", "reviewer": "a", "model": "a", "output_dir": str(out_a)},
                    {"model_key": "model/b", "reviewer": "b", "model": "b", "output_dir": str(out_b)},
                ],
                output_dir=project / "consensus" / "batch1",
                threshold=4.0,
            )

            decisions = json.loads((project / "consensus" / "batch1" / "decisions.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["complete"])
            self.assertEqual(decisions[0]["strategy_pass_min"], 4.2)
            self.assertEqual(decisions[0]["recommended_count"], 1)
            self.assertEqual(decisions[0]["decision_bucket"], "single_model_recommended")
            self.assertEqual(decisions[0]["consensus_verdict"], "WATCH")

    def test_build_consensus_accepts_direct_review_scoring_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            candidates_path = project / "batches" / "batch1" / "candidates.json"
            write_json(
                candidates_path,
                {
                    "pick_date": "2026-06-01",
                    "candidates": [
                        {"code": "000001", "strategy": "brick"},
                    ],
                },
            )
            manifest = {
                "batch_id": "batch1",
                "pick_date": "2026-06-01",
                "candidates_file": str(candidates_path),
            }
            out_a = project / "runs" / "a"
            out_b = project / "runs" / "b"
            write_json(out_a / "2026-06-01" / "000001_brick.json", review_payload("000001", "brick", 4.3))
            write_json(out_b / "2026-06-01" / "000001_brick.json", review_payload("000001", "brick", 4.6))

            build_consensus(
                batch_manifest=manifest,
                run_specs=[
                    {"model_key": "model/a", "reviewer": "a", "model": "a", "output_dir": str(out_a)},
                    {"model_key": "model/b", "reviewer": "b", "model": "b", "output_dir": str(out_b)},
                ],
                output_dir=project / "consensus" / "batch1",
                threshold=4.0,
                review_scoring={
                    "strategy_profiles": {
                        "brick": {
                            "pass_min": 4.5,
                            "watch_min": 3.8,
                        }
                    }
                },
            )

            decisions = json.loads((project / "consensus" / "batch1" / "decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(decisions[0]["strategy_pass_min"], 4.5)
            self.assertEqual(decisions[0]["strategy_watch_min"], 3.8)
            self.assertEqual(decisions[0]["recommended_count"], 1)
            self.assertEqual(decisions[0]["decision_bucket"], "single_model_recommended")

    def test_prepare_reviewer_config_rejects_model_substitution_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            base_config = project / "reviewer.yaml"
            base_config.write_text("skip_existing: true\n", encoding="utf-8")
            manifest = {
                "batch_id": "batch1",
                "candidates_file": str(project / "candidates.json"),
                "kline_dir": str(project / "kline"),
                "prompt_path": str(project / "prompt.md"),
            }
            spec = {
                "reviewer_key": "gemini-cli",
                "script": "agent/gemini_cli_review.py",
                "config": str(base_config),
                "model": "gemini-3.1-pro-preview",
                "model_profile": "gemini-3.1-pro-preview",
                "fallback_model": "gemini-2.5-pro",
            }

            with self.assertRaises(RuntimeError):
                multi_model_review.prepare_reviewer_config(
                    spec=spec,
                    multi_cfg={"no_model_substitution": True},
                    manifest=manifest,
                    run_dir=project,
                    review_runs_dir=project / "review_runs",
                )

    def test_read_failure_info_extracts_reason_from_log_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.log"
            path.write_text(
                "\n".join(
                    [
                        "[INFO] start",
                        "Traceback: ignored older line",
                        "[ERROR] FAILED_PRECONDITION: User location is not supported for the API use.",
                    ]
                ),
                encoding="utf-8",
            )

            info = multi_model_review.read_failure_info(path)

        self.assertIn("FAILED_PRECONDITION", info["summary"])
        self.assertIn("User location is not supported", info["log_tail"])


if __name__ == "__main__":
    unittest.main()
