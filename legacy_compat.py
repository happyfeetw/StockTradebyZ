"""Shared notices for legacy compatibility entrypoints."""
from __future__ import annotations

import os
import sys

LEGACY_WRITE_FREEZE_PREFIX = "R7 legacy write freeze"
LEGACY_RETIREMENT_PREFIX = "R7 legacy retirement"
LEGACY_CHART_EXPORT_ENV = "STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT"
LEGACY_UI_FREEZE_NOTICE = (
    "R7 legacy write freeze: this Streamlit surface is compatibility-only. "
    "Use the React/FastAPI product for new workflows; keep this path only for "
    "migration, parity, and rollback evidence."
)


def print_legacy_write_freeze_notice(*, surface: str, replacement: str, writes: str) -> None:
    """Emit a short compatibility warning without changing legacy behavior."""
    if os.environ.get("STOCKTRADE_SUPPRESS_LEGACY_FREEZE_NOTICE") == "1":
        return
    print(
        f"[WARN] {LEGACY_WRITE_FREEZE_PREFIX}: {surface} is compatibility-only; "
        f"writes {writes}. Use {replacement} for product-owned workflows.",
        file=sys.stderr,
    )


def legacy_chart_export_enabled() -> bool:
    """Return whether the retired legacy chart exporter may run."""
    return os.environ.get(LEGACY_CHART_EXPORT_ENV) == "1"


def print_legacy_chart_export_retired_notice() -> None:
    """Explain the supported chart-export path and rollback-only override."""
    print(
        f"[ERROR] {LEGACY_RETIREMENT_PREFIX}: dashboard.export_kline_charts is retired "
        "by default. Use POST /api/runs/chart-export for product-owned chart "
        f"artifacts. For rollback or parity evidence only, set {LEGACY_CHART_EXPORT_ENV}=1.",
        file=sys.stderr,
    )
