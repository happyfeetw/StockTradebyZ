"""Shared notices for legacy compatibility entrypoints."""
from __future__ import annotations

import os
import sys

LEGACY_WRITE_FREEZE_PREFIX = "R7 legacy write freeze"
LEGACY_RETIREMENT_PREFIX = "R7 legacy retirement"
LEGACY_CHART_EXPORT_ENV = "STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT"
LEGACY_PRESELECT_CLI_ENV = "STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI"
LEGACY_PRESELECT_CLI_RETIRED_NOTICE = (
    "R7 preselect CLI retirement: pipeline.cli preselect is retired by default. "
    f"Set {LEGACY_PRESELECT_CLI_ENV}=1 only for migration, parity, or rollback checks."
)
LEGACY_ARCHIVE_RESULTS_ENV = "STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS"
LEGACY_ARCHIVE_RESULTS_RETIRED_NOTICE = (
    "R7 archive writer retirement: pipeline.archive_results is retired by default. "
    f"Set {LEGACY_ARCHIVE_RESULTS_ENV}=1 only for migration, parity, or rollback checks."
)
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
LEGACY_GEMINI_CLI_REVIEW_ENV = "STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW"
LEGACY_GEMINI_CLI_REVIEW_RETIRED_NOTICE = (
    "R7 Gemini CLI reviewer retirement: agent/gemini_cli_review.py is retired by default. "
    f"Set {LEGACY_GEMINI_CLI_REVIEW_ENV}=1 only for migration, parity, or rollback checks."
)


def legacy_gemini_api_review_enabled() -> bool:
    return os.environ.get(LEGACY_GEMINI_API_REVIEW_ENV) == "1"


def legacy_gemini_cli_review_enabled() -> bool:
    return os.environ.get(LEGACY_GEMINI_CLI_REVIEW_ENV) == "1"


def legacy_preselect_cli_enabled() -> bool:
    return os.environ.get(LEGACY_PRESELECT_CLI_ENV) == "1"


def legacy_archive_results_enabled() -> bool:
    return os.environ.get(LEGACY_ARCHIVE_RESULTS_ENV) == "1"


LEGACY_DASHBOARD_ENV = "STOCKTRADE_ALLOW_LEGACY_DASHBOARD"
LEGACY_DASHBOARD_RETIRED_NOTICE = (
    "R7 dashboard retirement: dashboard/app.py is retired by default. "
    f"Set {LEGACY_DASHBOARD_ENV}=1 only for migration, parity, or rollback checks."
)
LEGACY_WORKBENCH_ENV = "STOCKTRADE_ALLOW_LEGACY_WORKBENCH"
LEGACY_WORKBENCH_RETIRED_NOTICE = (
    "R7 workbench retirement: workbench/app.py and start_workbench are retired by default. "
    f"Set {LEGACY_WORKBENCH_ENV}=1 only for migration, parity, or rollback checks."
)


def legacy_dashboard_enabled() -> bool:
    return os.environ.get(LEGACY_DASHBOARD_ENV) == "1"


def legacy_workbench_enabled() -> bool:
    return os.environ.get(LEGACY_WORKBENCH_ENV) == "1"


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
