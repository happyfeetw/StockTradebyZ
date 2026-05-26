from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "pipeline"))

from archive_results import build_rows, build_summary  # noqa: E402
from base_reviewer import BaseReviewer  # noqa: E402
from pipeline_io import merge_same_date_by_strategy  # noqa: E402
from schemas import CandidateRun  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "golden_master"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def normalize_chart_paths(rows: list[dict]) -> list[dict]:
    normalized = []
    for row in rows:
        item = dict(row)
        item["chart"] = Path(str(item["chart"])).name if item.get("chart") else ""
        normalized.append(item)
    return normalized


class GoldenMasterContractTests(unittest.TestCase):
    def test_candidate_merge_preserves_code_strategy_identity(self) -> None:
        existing = CandidateRun.from_dict(load_fixture("candidate_merge_existing.json"))
        incoming = CandidateRun.from_dict(load_fixture("candidate_merge_incoming_b2.json"))
        expected = load_fixture("candidate_merge_expected.json")

        actual = merge_same_date_by_strategy(existing, incoming).to_dict()

        self.assertEqual(actual, expected)

    def test_archive_rows_and_summary_match_golden_master(self) -> None:
        case = load_fixture("archive_review_case.json")
        pick_date = str(case["pick_date"])
        run_id = str(case["run_id"])

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            review_dir = tmp / "review"
            review_dir.mkdir(parents=True)
            for filename, payload in case["review_files"].items():
                (review_dir / filename).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            chart_date_dir = tmp / "kline" / pick_date
            chart_date_dir.mkdir(parents=True)
            for filename in case["chart_files"]:
                (chart_date_dir / filename).write_text("fixture chart", encoding="utf-8")

            rows = build_rows(
                candidates_data=case["candidates_data"],
                suggestion=case["suggestion"],
                review_dir=review_dir,
                kline_dir=tmp / "kline",
                pick_date=pick_date,
                run_id=run_id,
            )

            self.assertEqual(normalize_chart_paths(rows), case["expected_rows"])
            summary = build_summary(
                pick_date=pick_date,
                run_id=run_id,
                candidates_data=case["candidates_data"],
                suggestion=case["suggestion"],
                rows=rows,
            )
            summary.pop("archived_at", None)
            self.assertEqual(summary, case["expected_summary"])

    def test_review_suggestion_normalization_matches_golden_master(self) -> None:
        case = load_fixture("review_suggestion_case.json")
        reviewer = BaseReviewer.__new__(BaseReviewer)
        normalized_results = [
            BaseReviewer.normalize_scores(deepcopy(result), case["classic_pattern_config"])
            for result in case["results"]
        ]

        suggestion = reviewer.generate_suggestion(
            pick_date=case["pick_date"],
            all_results=normalized_results,
            min_score=case["min_score"],
            candidates=case["candidates"],
        )

        self.assertEqual(suggestion, case["expected_suggestion"])


if __name__ == "__main__":
    unittest.main()
