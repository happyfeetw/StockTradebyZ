#!/usr/bin/env python3
"""Lightweight agent harness checks for StockTradebyZ.

These checks are intentionally credential-free and safe in a clean clone.
They validate the repository map, important source contracts, and fast Python
health checks. Runtime checks that need Tushare, Gemini, or local data stay out
of this entrypoint.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DOCS = [
    Path("AGENTS.md"),
    Path("ARCHITECTURE.md"),
    Path("README.md"),
    Path("docs/agent-harness/index.md"),
    Path("docs/agent-harness/harness-engineering-principles.md"),
    Path("docs/agent-harness/issue-pr-governance.md"),
    Path("docs/agent-harness/product-refactor-charter.md"),
    Path("docs/agent-harness/business-logic-spec.md"),
    Path("docs/agent-harness/refactor-execution.md"),
    Path("docs/agent-harness/refactor-contracts.md"),
    Path("docs/agent-harness/refactor-preconditions.md"),
    Path("docs/agent-harness/uiux-quality-bar.md"),
    Path("docs/agent-harness/architecture-quality-bar.md"),
    Path("docs/agent-harness/target-architecture-design.md"),
    Path("docs/agent-harness/validation-gates.md"),
    Path("docs/agent-harness/workflows.md"),
    Path("docs/agent-harness/quality-scorecard.md"),
]

LOCAL_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
OPTIONAL_GENERATED_LINK_PREFIXES = ("data/",)


class HarnessError(RuntimeError):
    pass


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read(path: str | Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(path: str | Path, needles: list[str]) -> None:
    text = read(path)
    missing = [needle for needle in needles if needle not in text]
    if missing:
        raise HarnessError(f"{path} missing required text: {', '.join(missing)}")


def iter_markdown_files() -> list[Path]:
    files = [ROOT / "AGENTS.md", ROOT / "ARCHITECTURE.md", ROOT / "README.md"]
    docs_dir = ROOT / "docs"
    if docs_dir.exists():
        files.extend(sorted(docs_dir.rglob("*.md")))
    return [path for path in files if path.exists()]


def check_local_markdown_links() -> None:
    errors: list[str] = []
    for path in iter_markdown_files():
        text = path.read_text(encoding="utf-8")
        for match in LOCAL_LINK_RE.finditer(text):
            target = match.group(1).strip()
            if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            if target.startswith("#"):
                continue
            clean = target.split("#", 1)[0]
            if not clean:
                continue
            if clean == "data" or clean.startswith(OPTIONAL_GENERATED_LINK_PREFIXES):
                continue
            linked = (path.parent / clean).resolve()
            try:
                linked.relative_to(ROOT)
            except ValueError:
                errors.append(f"{rel(path)} links outside repo: {target}")
                continue
            if not linked.exists():
                errors.append(f"{rel(path)} broken link: {target}")
    if errors:
        raise HarnessError("\n".join(errors))


def check_docs() -> None:
    missing = [rel(ROOT / path) for path in REQUIRED_DOCS if not (ROOT / path).exists()]
    if missing:
        raise HarnessError(f"missing docs: {', '.join(missing)}")

    assert_contains(
        "AGENTS.md",
        [
            "ARCHITECTURE.md",
            "docs/agent-harness/index.md",
            "docs/agent-harness/issue-pr-governance.md",
            "docs/agent-harness/product-refactor-charter.md",
            "docs/agent-harness/business-logic-spec.md",
            "docs/agent-harness/refactor-execution.md",
            "docs/agent-harness/refactor-contracts.md",
            "docs/agent-harness/refactor-preconditions.md",
            "docs/agent-harness/uiux-quality-bar.md",
            "docs/agent-harness/architecture-quality-bar.md",
            "docs/agent-harness/target-architecture-design.md",
            "scripts/harness/check.sh quick",
            "scripts/harness/check.sh product-refactor-readiness",
            "(code, strategy)",
        ],
    )
    assert_contains(
        "docs/agent-harness/index.md",
        [
            "Give agents a map",
            "issue-pr-governance.md",
            "target-architecture-design.md",
            "scripts/harness/check.sh quick",
            "scripts/harness/check.sh product-refactor-readiness",
            "scripts/harness/check.sh refactor-readiness",
            "Maintenance Rule",
        ],
    )
    check_local_markdown_links()
    print("[docs] ok")


def check_contracts() -> None:
    assert_contains(
        "pipeline/pipeline_io.py",
        [
            "def merge_same_date_by_strategy",
            "candidate.code, candidate.strategy",
            "candidates_latest.json",
            "merge_same_date",
        ],
    )
    assert_contains(
        "pipeline/archive_results.py",
        [
            "def review_key",
            "review_matches_strategy",
            "recommended",
            "unreviewed",
            "data/history",
        ],
    )
    assert_contains(
        "agent/gemini_cli_review.py",
        [
            "gemini_cli_review_checkpoint.json",
            "retry_backoff_seconds",
            "save_raw_cli_io",
            "skip_existing",
            "Gemini CLI raw log",
        ],
    )
    assert_contains(
        "paper_trading/core.py",
        [
            "data/trading",
            "position_key",
            "plan_path",
            "fills",
            "auto_execute_confirmed_plan",
        ],
    )
    print("[contracts] ok")


def check_product_refactor_readiness() -> None:
    check_docs()
    check_contracts()
    assert_contains(
        "docs/agent-harness/product-refactor-charter.md",
        [
            "full product-level rewrite",
            "UI/UX",
            "React/Vite/TypeScript",
            "FastAPI",
            "Core business logic remains Python",
            "frontend architecture",
            "backend architecture",
            "system architecture",
            "data architecture",
            "core business logic implementation",
        ],
    )
    assert_contains(
        "docs/agent-harness/issue-pr-governance.md",
        [
            "Every task starts with a GitHub issue",
            "Every repository change lands through a PR",
            "issue #23",
            "scripts/harness/check.sh product-refactor-readiness",
            "scripts/harness/check.sh quick",
        ],
    )
    assert_contains(
        "docs/agent-harness/business-logic-spec.md",
        [
            "Business logic means observable product behavior",
            "Golden Master Rule",
            "behavior parity",
            "(code, strategy)",
            "--merge-same-date",
            "Gemini Review",
            "Out of Scope",
        ],
    )
    assert_contains(
        "docs/agent-harness/refactor-execution.md",
        [
            "legacy system is the behavior",
            "R0 Product Charter and Decision Freeze",
            "R1 Business Logic Specification",
            "R2 Target Architecture and Data Model",
            "R3 Core Domain Rewrite",
            "R4 Backend Runtime and APIs",
            "R5 Frontend Product UI/UX",
            "R6 Data Migration and Storage Cutover",
            "R7 Hardening, Performance, and Legacy Retirement",
            "Stop Conditions",
            "rollback",
        ],
    )
    assert_contains(
        "docs/agent-harness/refactor-contracts.md",
        [
            "(code, strategy)",
            "candidates_latest.json",
            "--merge-same-date",
            "review_key",
            "suggestion.json",
            "data/history",
            "run_state.json",
            "gemini_cli_review_checkpoint.json",
            "Legacy Paper Trading State",
        ],
    )
    assert_contains(
        "docs/agent-harness/refactor-preconditions.md",
        [
            "Confirmed Decisions",
            "React + Vite + TypeScript",
            "FastAPI",
            "Core business logic remains Python",
            "SQLite",
            "DuckDB",
            "SQLAlchemy 2.x",
            "Alembic",
            "var/db/app.sqlite",
            "var/db/analytics.duckdb",
            "Remaining Confirmation Triggers",
            "strict-parity business behavior",
            "UI acceptance baseline",
            "Runtime resource envelope",
        ],
    )
    assert_contains(
        "docs/agent-harness/uiux-quality-bar.md",
        [
            "practical, attractive, modern, friendly",
            "Not overdesigned",
            "Accessible contrast",
            "Responsive layouts",
            "Loading states",
            "error states",
            "Playwright",
            "desktop screenshot",
            "mobile-width screenshot",
        ],
    )
    assert_contains(
        "docs/agent-harness/architecture-quality-bar.md",
        [
            "Frontend Architecture",
            "React + Vite + TypeScript",
            "TanStack Query",
            "TanStack Table",
            "Apache ECharts",
            "Backend Architecture",
            "FastAPI",
            "Pydantic v2",
            "SQLAlchemy 2.x",
            "Alembic",
            "Storage Architecture",
            "SQLite",
            "DuckDB",
            "var/db/app.sqlite",
            "var/db/analytics.duckdb",
            "Parquet",
            "System Architecture",
            "React web frontend plus FastAPI backend",
            "Runtime resource",
            "Observability",
            "Schema migrations",
            "External calls",
        ],
    )
    assert_contains(
        "docs/agent-harness/target-architecture-design.md",
        [
            "Issue: #29",
            "React + Vite + TypeScript",
            "FastAPI",
            "SQLite Ownership",
            "DuckDB Ownership",
            "FastAPI Route Groups",
            "Job Runtime",
            "Legacy Import and Migration",
            "Backup Format",
            "simulated trading is out of scope",
            "var/db/app.sqlite",
            "var/db/analytics.duckdb",
            "migration_quarantine",
        ],
    )
    print("[product-refactor-readiness] ok")


def check_refactor_readiness() -> None:
    check_product_refactor_readiness()
    print("[refactor-readiness] alias ok")


def python_files() -> list[str]:
    roots = ["agent", "dashboard", "paper_trading", "pipeline", "tests"]
    files: list[str] = ["run_all.py", "scripts/harness/check.py"]
    for root in roots:
        root_path = ROOT / root
        if root_path.exists():
            files.extend(rel(path) for path in sorted(root_path.rglob("*.py")))
    return files


def run_command(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    printable = " ".join(cmd)
    print(f"[run] {printable}")
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        raise HarnessError(f"command failed ({result.returncode}): {printable}")


def check_python() -> None:
    run_command([sys.executable, "-m", "py_compile", *python_files()])
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "agent"),
            str(ROOT / "pipeline"),
            env.get("PYTHONPATH", ""),
        ]
    )
    run_command([sys.executable, "-m", "unittest", "discover", "-s", "tests"], env=env)
    print("[python] ok")


def check_quick() -> None:
    check_docs()
    check_contracts()
    check_python()
    print("[quick] ok")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="StockTradebyZ agent harness checks")
    parser.add_argument(
        "gate",
        choices=[
            "docs",
            "contracts",
            "python",
            "product-refactor-readiness",
            "refactor-readiness",
            "quick",
        ],
        help="Validation gate to run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.gate == "docs":
            check_docs()
        elif args.gate == "contracts":
            check_contracts()
        elif args.gate == "python":
            check_python()
        elif args.gate == "product-refactor-readiness":
            check_product_refactor_readiness()
        elif args.gate == "refactor-readiness":
            check_refactor_readiness()
        elif args.gate == "quick":
            check_quick()
        else:
            raise HarnessError(f"unknown gate: {args.gate}")
    except HarnessError as exc:
        print(f"[harness] failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
