from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "pipeline"))

import pandas as pd  # noqa: E402

import select_stock  # noqa: E402
from archive_results import build_rows, build_summary  # noqa: E402
from base_reviewer import BaseReviewer  # noqa: E402
from pipeline_io import merge_same_date_by_strategy  # noqa: E402
from schemas import CandidateRun  # noqa: E402
from stocktrade.domain.review import generate_suggestion, normalize_scores  # noqa: E402

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


class FixtureB1Selector:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["_vec_pick"] = prepared["_fixture_b1"].astype(bool)
        return prepared

    def vec_picks_from_prepared(
        self,
        df: pd.DataFrame,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> list[pd.Timestamp]:
        mask = df["_vec_pick"].astype(bool)
        if start is not None:
            mask = mask & (df.index >= start)
        if end is not None:
            mask = mask & (df.index <= end)
        return list(df.index[mask])


class FixtureB2Selector(FixtureB1Selector):
    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["_vec_pick"] = prepared["_fixture_b2"].astype(bool)
        prepared["_b2_daily_return"] = 0.11
        prepared["_b2_today_body_pct"] = 0.05
        prepared["_b2_volume_ratio"] = 1.25
        prepared["_b2_prior_b1_lag"] = 1
        prepared["_b2_prior_b1_j"] = 20.0
        prepared["J"] = 35.0
        prepared["_b2_j_turn_up"] = True
        prepared["_b2_strict_yang_bao_yin"] = False
        prepared["_b2_upper_shadow_ratio"] = 0.02
        prepared["_b2_quality_score"] = prepared["_fixture_b2_quality_score"].astype(float)
        return prepared


class FixtureBrickSelector(FixtureB1Selector):
    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["_vec_pick"] = prepared["_fixture_brick"].astype(bool)
        prepared["brick_growth"] = prepared["_fixture_brick_growth"].astype(float)
        return prepared


def write_strategy_case_files(tmp: Path, case: dict) -> tuple[Path, Path]:
    raw_dir = tmp / "raw"
    raw_dir.mkdir()
    for code, rows in case["raw_data"].items():
        pd.DataFrame(rows).to_csv(raw_dir / f"{code}.csv", index=False)

    config = deepcopy(case["config"])
    config["global"]["data_dir"] = str(raw_dir)
    config_path = tmp / "rules_preselect.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw_dir, config_path


class GoldenMasterContractTests(unittest.TestCase):
    def test_preselect_pipeline_output_matches_golden_master(self) -> None:
        case = load_fixture("strategy_preselect_case.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            _, config_path = write_strategy_case_files(Path(tmpdir), case)
            with (
                patch.object(select_stock, "B1Selector", FixtureB1Selector),
                patch.object(select_stock, "B2Selector", FixtureB2Selector),
                patch.object(select_stock, "BrickChartSelector", FixtureBrickSelector),
            ):
                pick_ts, candidates = select_stock.run_preselect(
                    config_path=str(config_path),
                    pick_date=case["requested_pick_date"],
                )

        self.assertEqual(pick_ts.strftime("%Y-%m-%d"), case["expected_pick_date"])
        self.assertEqual([candidate.to_dict() for candidate in candidates], case["expected_candidates"])

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

        domain_results = [
            normalize_scores(deepcopy(result), case["classic_pattern_config"])
            for result in case["results"]
        ]
        domain_suggestion = generate_suggestion(
            pick_date=case["pick_date"],
            all_results=domain_results,
            min_score=case["min_score"],
            candidates=case["candidates"],
        )
        self.assertEqual(domain_results, normalized_results)
        self.assertEqual(domain_suggestion, case["expected_suggestion"])

    def test_review_domain_imports_without_legacy_agent_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "src"))
from stocktrade.domain.review import generate_suggestion, normalize_scores
print(callable(generate_suggestion))
print(callable(normalize_scores))
print("base_reviewer" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["True", "True", "False", "False"])


if __name__ == "__main__":
    unittest.main()
