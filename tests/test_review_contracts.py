from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "pipeline"))

from base_reviewer import BaseReviewer  # noqa: E402
from archive_results import review_matches_strategy  # noqa: E402


class ReviewContractTests(unittest.TestCase):
    def test_replaced_strategy_candidates_are_reviewed_first(self) -> None:
        reviewer = BaseReviewer.__new__(BaseReviewer)
        reviewer.config = {}
        candidates_data = {
            "meta": {"replaced_strategies": ["b2"]},
            "candidates": [
                {"code": "000001", "strategy": "b1"},
                {"code": "000002", "strategy": "brick"},
                {"code": "000003", "strategy": "b2"},
                {"code": "000004", "strategy": "b2"},
            ],
        }

        ordered = reviewer.order_candidates_for_review(candidates_data)

        self.assertEqual([item["code"] for item in ordered], ["000003", "000004", "000001", "000002"])

    def test_legacy_review_must_match_requested_strategy(self) -> None:
        self.assertTrue(review_matches_strategy({"strategy": "b2"}, "000001", "b2"))
        self.assertFalse(review_matches_strategy({"strategy": "brick"}, "000001", "b2"))
        self.assertTrue(review_matches_strategy({"review_key": "000001_b2"}, "000001", "b2"))
        self.assertFalse(review_matches_strategy({"review_key": "000001_brick"}, "000001", "b2"))
        self.assertFalse(review_matches_strategy({"total_score": 4.0}, "000001", "b2"))
        self.assertTrue(review_matches_strategy({"total_score": 4.0}, "000001", ""))

    def test_suggestion_preserves_classic_pattern_fields(self) -> None:
        reviewer = BaseReviewer.__new__(BaseReviewer)
        result = {
            "code": "000001",
            "strategy": "b1",
            "review_key": "000001_b1",
            "total_score": 4.2,
            "classic_pattern_type": "b1_type1_low_breakout_pullback",
            "classic_pattern_reasoning": "缩量回踩匹配第一类",
            "scores": {
                "classic_pattern_match": 5,
            },
        }

        suggestion = reviewer.generate_suggestion(
            pick_date="2026-05-22",
            all_results=[result],
            min_score=4.0,
            candidates=[{"code": "000001", "strategy": "b1"}],
        )

        recommendation = suggestion["recommendations"][0]
        self.assertEqual(recommendation["classic_pattern_type"], "b1_type1_low_breakout_pullback")
        self.assertEqual(recommendation["classic_pattern_match"], 5.0)
        self.assertEqual(recommendation["classic_pattern_reasoning"], "缩量回踩匹配第一类")

    def test_classic_pattern_strategy_adds_bonus_to_base_score(self) -> None:
        result = {
            "strategy": "brick",
            "total_score": 1.0,
            "verdict": "FAIL",
            "scores": {
                "trend_structure": 4,
                "price_position": 3,
                "volume_behavior": 5,
                "previous_abnormal_move": 2,
                "classic_pattern_match": 5,
            },
        }

        normalized = BaseReviewer.normalize_scores(result)

        self.assertEqual(normalized["scores"]["previous_abnormal_move"], 2.0)
        self.assertEqual(normalized["scores"]["classic_pattern_match"], 5.0)
        self.assertEqual(normalized["total_score"], 3.9)
        self.assertEqual(normalized["verdict"], "WATCH")

    def test_classic_pattern_non_match_does_not_lower_base_score(self) -> None:
        result = {
            "strategy": "b2",
            "total_score": 1.0,
            "scores": {
                "trend_structure": 4,
                "price_position": 4,
                "volume_behavior": 4,
                "previous_abnormal_move": 4,
                "classic_pattern_match": 1,
            },
        }

        enabled = BaseReviewer.normalize_scores(deepcopy(result), {"classic_pattern_enabled": True})
        disabled = BaseReviewer.normalize_scores(deepcopy(result), {"classic_pattern_enabled": False})

        self.assertEqual(enabled["total_score"], 4.0)
        self.assertEqual(disabled["total_score"], 4.0)
        self.assertEqual(enabled["verdict"], "PASS")

    def test_classic_pattern_active_zero_is_normalized_to_no_bonus(self) -> None:
        result = {
            "strategy": "b1",
            "total_score": 1.0,
            "scores": {
                "trend_structure": 4,
                "price_position": 4,
                "volume_behavior": 4,
                "previous_abnormal_move": 4,
                "classic_pattern_match": 0,
            },
        }

        normalized = BaseReviewer.normalize_scores(result, {"classic_pattern_enabled": True})

        self.assertEqual(normalized["scores"]["classic_pattern_match"], 1.0)
        self.assertEqual(normalized["total_score"], 4.0)

    def test_hard_volume_veto_keeps_score_below_recommendation_threshold(self) -> None:
        result = {
            "strategy": "b1",
            "total_score": 5.0,
            "scores": {
                "trend_structure": 5,
                "price_position": 5,
                "volume_behavior": 1,
                "previous_abnormal_move": 5,
                "classic_pattern_match": 5,
            },
        }

        normalized = BaseReviewer.normalize_scores(result, {"classic_pattern_enabled": True})

        self.assertLess(normalized["total_score"], 4.0)
        self.assertEqual(normalized["verdict"], "FAIL")
        self.assertEqual(normalized["score_before_hard_veto"], 4.2)
        self.assertEqual(normalized["hard_veto_reason"], "volume_behavior <= 1")

    def test_composite_strategy_uses_base_four_dimension_weight(self) -> None:
        result = {
            "strategy": "b1+brick",
            "total_score": 4.6,
            "classic_pattern_type": "brick_green_to_red_reversal",
            "classic_pattern_reasoning": "误用了 brick 子条件",
            "scores": {
                "trend_structure": 5,
                "price_position": 4,
                "volume_behavior": 3,
                "previous_abnormal_move": 2,
                "classic_pattern_match": 5,
            },
        }

        normalized = BaseReviewer.normalize_scores(result)

        self.assertEqual(normalized["total_score"], 3.3)
        self.assertEqual(normalized["verdict"], "WATCH")
        self.assertEqual(normalized["classic_pattern_type"], "none")
        self.assertEqual(normalized["classic_pattern_reasoning"], "")
        self.assertEqual(normalized["scores"]["classic_pattern_match"], 0.0)

    def test_classic_pattern_switch_controls_defined_strategies(self) -> None:
        brick_result = {
            "strategy": "brick",
            "total_score": 1.0,
            "scores": {
                "trend_structure": 4,
                "price_position": 3,
                "volume_behavior": 5,
                "previous_abnormal_move": 2,
                "classic_pattern_match": 5,
            },
        }
        unknown_result = {
            "strategy": "custom_single",
            "total_score": 1.0,
            "scores": {
                "trend_structure": 5,
                "price_position": 5,
                "volume_behavior": 5,
                "previous_abnormal_move": 1,
                "classic_pattern_match": 5,
            },
        }

        enabled = BaseReviewer.normalize_scores(deepcopy(brick_result), {"classic_pattern_enabled": True})
        disabled = BaseReviewer.normalize_scores(deepcopy(brick_result), {"classic_pattern_enabled": False})
        unknown = BaseReviewer.normalize_scores(deepcopy(unknown_result), {"classic_pattern_enabled": True})

        self.assertEqual(enabled["total_score"], 3.9)
        self.assertEqual(disabled["total_score"], 3.5)
        self.assertEqual(disabled["scores"]["classic_pattern_match"], 0.0)
        self.assertEqual(unknown["total_score"], 3.8)
        self.assertEqual(unknown["scores"]["classic_pattern_match"], 0.0)

    def test_legacy_classic_pattern_strategy_list_remains_compatible(self) -> None:
        result = {
            "strategy": "custom_single",
            "total_score": 1.0,
            "scores": {
                "trend_structure": 5,
                "price_position": 5,
                "volume_behavior": 5,
                "previous_abnormal_move": 1,
                "classic_pattern_match": 5,
            },
        }

        defaulted = BaseReviewer.normalize_scores(deepcopy(result))
        normalized = BaseReviewer.normalize_scores(deepcopy(result), ["custom_single"])
        disabled = BaseReviewer.normalize_scores(deepcopy(result), [])

        self.assertEqual(defaulted["total_score"], 3.8)
        self.assertEqual(normalized["total_score"], 4.2)
        self.assertEqual(disabled["total_score"], 3.8)


if __name__ == "__main__":
    unittest.main()
