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
LEGACY_DASHBOARD_ENV = "STOCKTRADE_ALLOW_LEGACY_DASHBOARD"
LEGACY_DASHBOARD_RETIRED_NOTICE = (
    "R7 dashboard retirement: dashboard/app.py is retired by default. "
    f"Set {LEGACY_DASHBOARD_ENV}=1 only for migration, parity, or rollback checks."
)


def legacy_dashboard_enabled() -> bool:
    return os.environ.get(LEGACY_DASHBOARD_ENV) == "1"


def print_legacy_write_freeze_notice(*, surface: str, replacement: str, writes: str) -> None:
    """Emit a short compatibility warning without changing legacy behavior."""
    if os.environ.get("STOCKTRADE_SUPPRESS_LEGACY_FREEZE_NOTICE") == "1":
        return
    print(
        f"[WARN] {LEGACY_WRITE_FREEZE_PREFIX}: {surface} is compatibility-only; "
        f"writes {writes}. Use {replacement} for product-owned workflows.",
        file=sys.stderr,
    )
