from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import z_quality_review  # noqa: E402
from pipeline.z_features import compute_z_features  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_sample_raw(path: Path, *, code: str = "000001") -> str:
    path.mkdir(parents=True, exist_ok=True)
    start = date(2026, 1, 1)
    rows = ["date,open,close,high,low,volume"]
    close = 10.0
    pick_date = ""
    for i in range(30):
        day = start + timedelta(days=i)
        open_price = close
        if i == 29:
            close_price = close - 0.65
            volume = 700
        else:
            close_price = close + 0.10
            volume = 3000 if i == 12 else 1000
        high = max(open_price, close_price) + 0.08
        low = min(open_price, close_price) - 0.08
        rows.append(f"{day.isoformat()},{open_price:.2f},{close_price:.2f},{high:.2f},{low:.2f},{volume}")
        close = close_price
        pick_date = day.isoformat()
    (path / f"{code}.csv").write_text("\n".join(rows), encoding="utf-8")
    return pick_date


class ZQualityReviewTests(unittest.TestCase):
    def test_compute_z_features_from_project_raw_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            pick_date = write_sample_raw(raw_dir)

            features = compute_z_features(raw_dir, "000001", pick_date=pick_date)

        self.assertTrue(features["data_available"])
        self.assertEqual(features["effective_date"], pick_date)
        self.assertEqual(features["volume"]["largest_volume_direction_20"], "up")
        self.assertTrue(features["volume"]["pullback_shrink"])
        self.assertTrue(features["price_position"]["near_support"])
        self.assertFalse(features["price_position"]["overhead_pressure_close"])
        self.assertIn("amount_missing", features["data_limitations"])

    def test_run_z_quality_review_writes_summary_decisions_and_llm_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            raw_dir = project / "raw"
            pick_date = write_sample_raw(raw_dir)
            batch_id = "batch_z"
            review_key = "000001_b1"
            consensus_dir = project / "consensus" / batch_id
            review_dir = project / "runs" / "model-a" / pick_date
            review_file = review_dir / f"{review_key}.json"
            write_json(
                review_file,
                {
                    "code": "000001",
                    "strategy": "b1",
                    "review_key": review_key,
                    "total_score": 4.6,
                    "verdict": "PASS",
                    "comment": "低位回踩贴近支撑，缩量健康。",
                    "common_gate_status": "PASS",
                    "common_gate": {"status": "PASS", "hard_veto": False, "hard_veto_reasons": []},
                    "scores": {
                        "trend_structure": 4,
                        "price_position": 4,
                        "volume_behavior": 4,
                        "previous_abnormal_move": 4,
                        "classic_pattern_match": 4,
                    },
                    "classic_pattern_type": "b1_type1_low_pullback",
                },
            )
            decision = {
                "batch_id": batch_id,
                "pick_date": pick_date,
                "rank": 1,
                "review_key": review_key,
                "code": "000001",
                "strategy": "b1",
                "decision_bucket": "all_models_recommended",
                "consensus_score": 4.6,
                "consensus_verdict": "PASS",
                "recommended_count": 1,
                "completed_count": 1,
                "total_models": 1,
                "missing_models": [],
            }
            detail = {
                "batch_id": batch_id,
                "pick_date": pick_date,
                "rank": 1,
                "review_key": review_key,
                "code": "000001",
                "strategy": "b1",
                "model_key": "model/a",
                "reviewer": "test",
                "model": "test-model",
                "total_score": 4.6,
                "verdict": "PASS",
                "recommended": True,
                "status": "reviewed",
                "comment": "ok",
                "file": str(review_file),
            }
            summary = {
                "batch_id": batch_id,
                "pick_date": pick_date,
                "files": {
                    "summary": str(consensus_dir / "summary.json"),
                    "decisions": str(consensus_dir / "decisions.json"),
                    "details": str(consensus_dir / "details.json"),
                },
            }
            write_json(consensus_dir / "summary.json", summary)
            write_json(consensus_dir / "decisions.json", [decision])
            write_json(consensus_dir / "details.json", [detail])

            z_summary = z_quality_review.run_z_quality_review(
                {
                    "ruleset_version": "test_z",
                    "raw_dir": str(raw_dir),
                    "kline_dir": str(project / "kline"),
                    "include_incomplete": False,
                    "thresholds": {
                        "a_select_min_quality_score": 4.2,
                        "b_watch_min_quality_score": 3.4,
                        "max_reject_score": 2.6,
                    },
                },
                summary_path=consensus_dir / "summary.json",
                output_root=project / "z_quality",
            )
            decisions = json.loads(Path(z_summary["files"]["decisions"]).read_text(encoding="utf-8"))
            llm_inputs = json.loads(Path(z_summary["files"]["llm_inputs"]).read_text(encoding="utf-8"))
            llm_input_exists = Path(llm_inputs[0]["input_file"]).exists()

        self.assertEqual(z_summary["processed_count"], 1)
        self.assertEqual(decisions[0]["z_quality_verdict"], "A_SELECT")
        self.assertEqual(decisions[0]["result_mode"], "local_rules_dry_run")
        self.assertEqual(len(llm_inputs), 1)
        self.assertTrue(llm_input_exists)


if __name__ == "__main__":
    unittest.main()
