"""Review domain boundary for the product rewrite."""

from .suggestions import (
    classic_pattern_score,
    generate_suggestion,
    has_classic_pattern_review,
    normalize_scores,
    review_key,
)

__all__ = [
    "classic_pattern_score",
    "generate_suggestion",
    "has_classic_pattern_review",
    "normalize_scores",
    "review_key",
]
