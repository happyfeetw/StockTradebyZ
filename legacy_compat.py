"""Shared notices for legacy compatibility entrypoints."""
from __future__ import annotations

import os
import sys

LEGACY_WRITE_FREEZE_PREFIX = "R7 legacy write freeze"
LEGACY_UI_FREEZE_NOTICE = (
    "R7 legacy write freeze: this Streamlit surface is compatibility-only. "
    "Use the React/FastAPI product for new workflows; keep this path only for "
    "migration, parity, and rollback evidence."
)
LEGACY_GEMINI_API_REVIEW_ENV = "STOCKTRADE_ALLOW_LEGACY_GEMINI_API_REVIEW"
LEGACY_GEMINI_API_REVIEW_RETIRED_NOTICE = (
    "R7 Gemini API reviewer retirement: agent/gemini_review.py is retired by default. "
    f"Set {LEGACY_GEMINI_API_REVIEW_ENV}=1 only for migration, parity, or rollback checks."
)


def legacy_gemini_api_review_enabled() -> bool:
    return os.environ.get(LEGACY_GEMINI_API_REVIEW_ENV) == "1"


def print_legacy_write_freeze_notice(*, surface: str, replacement: str, writes: str) -> None:
    """Emit a short compatibility warning without changing legacy behavior."""
    if os.environ.get("STOCKTRADE_SUPPRESS_LEGACY_FREEZE_NOTICE") == "1":
        return
    print(
        f"[WARN] {LEGACY_WRITE_FREEZE_PREFIX}: {surface} is compatibility-only; "
        f"writes {writes}. Use {replacement} for product-owned workflows.",
        file=sys.stderr,
    )
