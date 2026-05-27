#!/usr/bin/env python3
"""Collect credential-free R7 resource envelope evidence.

The fixture runs the supported product API path in-process:
preselect -> chart export -> provider review -> archive. It never calls live
Tushare, Gemini, or legacy paper-trading code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_LIMITS = {
    "startup_seconds": 5.0,
    "workflow_seconds": 30.0,
    "python_peak_mb": 768.0,
    "process_peak_rss_mb": 2048.0,
    "sqlite_growth_mb": 64.0,
    "duckdb_growth_mb": 256.0,
    "artifact_mb": 128.0,
}


@dataclass(frozen=True)
class StorageSnapshot:
    sqlite_bytes: int
    duckdb_bytes: int
    artifact_bytes: int
    artifact_files: int

    def as_dict(self) -> dict[str, int]:
        return {
            "sqlite_bytes": self.sqlite_bytes,
            "duckdb_bytes": self.duckdb_bytes,
            "artifact_bytes": self.artifact_bytes,
            "artifact_files": self.artifact_files,
        }


class FixturePreselectService:
    def __init__(self) -> None:
        self.parameters: list[Any] = []

    def run(self, parameters: Any) -> Any:
        from stocktrade.domain.selection import PreselectResult, SelectionCandidate

        self.parameters.append(parameters)
        pick_date = parameters.pick_date or "2026-05-25"
        return PreselectResult(
            run_date="2026-05-27",
            pick_date=pick_date,
            candidates=[
                SelectionCandidate(
                    code="000001",
                    date=pick_date,
                    strategy="b2",
                    close=10.8,
                    turnover_n=2.3,
                    extra={"fixture_source": "r7_resource_envelope"},
                ),
                SelectionCandidate(
                    code="000002",
                    date=pick_date,
                    strategy="brick",
                    close=12.6,
                    turnover_n=1.7,
                    brick_growth=0.18,
                    extra={"fixture_source": "r7_resource_envelope"},
                ),
            ],
            meta={
                "mode": "r7_resource_envelope_fixture",
                "strategy_candidate_counts": {"b2": 1, "brick": 1},
                "data_dir": parameters.data_dir,
            },
        )


class FixtureReviewProviderExecutor:
    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root

    def run(self, request: Any) -> list[dict[str, Any]]:
        state_root = self.artifact_root / "resource-provider" / request.candidate_batch_id / "gemini-cli"
        raw_dir = state_root / "runs" / "call-1"
        result_cache_dir = state_root / "results"
        raw_dir.mkdir(parents=True, exist_ok=True)
        result_cache_dir.mkdir(parents=True, exist_ok=True)
        evidence_files = [
            ("raw_prompt", raw_dir / "prompt.txt", "r7 resource envelope prompt"),
            ("raw_meta", raw_dir / "meta.json", '{"status":"finished","fixture":true}'),
            ("raw_stdout", raw_dir / "stdout.jsonl", '{"role":"assistant","fixture":true}\n'),
            ("raw_stderr", raw_dir / "stderr.log", ""),
            ("checkpoint", state_root / "gemini_cli_review_checkpoint.json", '{"status":"finished"}'),
            ("usage", state_root / ".gemini_cli_usage.json", '{"count":1}'),
        ]
        for _role, path, body in evidence_files:
            path.write_text(body, encoding="utf-8")

        results: list[dict[str, Any]] = []
        for item in request.items:
            cache_path = result_cache_dir / f"{item.review_key}.json"
            cache_path.write_text('{"cached":true,"fixture":true}', encoding="utf-8")
            result = review_result(item.code, item.strategy)
            result["provider_evidence_files"] = [
                {"role": role, "path": str(path)}
                for role, path, _body in evidence_files
            ]
            result["provider_evidence_files"].append({"role": "result_cache", "path": str(cache_path)})
            results.append(result)
        return results


def review_result(code: str, strategy: str) -> dict[str, Any]:
    return {
        "code": code,
        "strategy": strategy,
        "signal_type": "resource-envelope-fixture",
        "comment": f"{code} {strategy} R7 resource envelope fixture",
        "scores": {
            "trend_structure": 5,
            "price_position": 5,
            "volume_behavior": 5,
            "previous_abnormal_move": 5,
            "classic_pattern_match": 5,
        },
    }


def write_raw_csv(raw_dir: Path, code: str) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    lines = ["date,open,high,low,close,volume"]
    for day in range(1, 8):
        open_price = 10.0 + day
        close = open_price + 0.3
        lines.append(
            f"2026-05-{day:02d},{open_price:.2f},{open_price + 0.6:.2f},"
            f"{open_price - 0.4:.2f},{close:.2f},{1000 + day * 100}"
        )
    (raw_dir / f"{code}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def path_size(path: Path) -> int:
    return path.stat().st_size if path.exists() and path.is_file() else 0


def directory_size(root: Path) -> tuple[int, int]:
    if not root.exists():
        return 0, 0
    total = 0
    files = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
            files += 1
    return total, files


def storage_snapshot(sqlite_path: Path, duckdb_path: Path, artifact_root: Path) -> StorageSnapshot:
    artifact_bytes, artifact_files = directory_size(artifact_root)
    return StorageSnapshot(
        sqlite_bytes=path_size(sqlite_path),
        duckdb_bytes=path_size(duckdb_path),
        artifact_bytes=artifact_bytes,
        artifact_files=artifact_files,
    )


def size_delta(before: StorageSnapshot, after: StorageSnapshot) -> dict[str, int]:
    return {
        "sqlite_bytes": after.sqlite_bytes - before.sqlite_bytes,
        "duckdb_bytes": after.duckdb_bytes - before.duckdb_bytes,
        "artifact_bytes": after.artifact_bytes - before.artifact_bytes,
        "artifact_files": after.artifact_files - before.artifact_files,
    }


def current_rss_mb() -> float | None:
    try:
        import resource
    except ImportError:
        return None

    rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return rss / (1024 * 1024)
    return rss / 1024


def sqlite_counts(sqlite_path: Path) -> dict[str, int]:
    tables = [
        "runs",
        "job_steps",
        "job_events",
        "artifacts",
        "candidate_batches",
        "candidates",
        "review_runs",
        "reviews",
        "recommendations",
        "archive_snapshots",
        "archive_rows",
    ]
    with sqlite3.connect(sqlite_path) as connection:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def duckdb_counts(duckdb_path: Path) -> dict[str, int]:
    from stocktrade_api.storage.duckdb import connect_duckdb

    tables = ["candidate_facts", "review_facts", "archive_facts", "strategy_run_metrics"]
    with connect_duckdb(duckdb_path, read_only=True) as connection:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def migrate_sqlite(db_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    sqlite_migrations = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"
    config = Config()
    config.set_main_option("script_location", str(sqlite_migrations))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")


async def run_product_workflow(work_root: Path) -> dict[str, Any]:
    import httpx

    from stocktrade_api.dependencies import get_preselect_service
    from stocktrade_api.main import create_app
    from stocktrade_api.storage.duckdb import apply_migrations

    sqlite_path = work_root / "db" / "app.sqlite"
    duckdb_path = work_root / "db" / "analytics.duckdb"
    artifact_root = work_root / "artifacts"
    backup_root = work_root / "backups"
    raw_dir = work_root / "raw"

    work_root.mkdir(parents=True, exist_ok=True)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    for code in ("000001", "000002"):
        write_raw_csv(raw_dir, code)

    before_startup = storage_snapshot(sqlite_path, duckdb_path, artifact_root)
    startup_started = time.perf_counter()
    migrate_sqlite(sqlite_path)
    apply_migrations(duckdb_path)
    app = create_app(
        sqlite_path=sqlite_path,
        duckdb_path=duckdb_path,
        backup_root=backup_root,
        artifact_root=artifact_root,
    )
    app.dependency_overrides[get_preselect_service] = lambda: FixturePreselectService()
    app.state.review_provider_executor = FixtureReviewProviderExecutor(artifact_root)
    transport = httpx.ASGITransport(app=app)
    startup_seconds = time.perf_counter() - startup_started
    after_startup = storage_snapshot(sqlite_path, duckdb_path, artifact_root)

    workflow_started = time.perf_counter()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/api/health")
        ensure_status(health_response, 200, "health")

        preselect_response = await client.post(
            "/api/runs/preselect",
            json={"pick_date": "2026-05-25", "data_dir": raw_dir.as_posix()},
        )
        ensure_status(preselect_response, 200, "preselect")
        preselect = preselect_response.json()
        batch_id = preselect["batch"]["id"]

        chart_response = await client.post(
            "/api/runs/chart-export",
            json={"candidate_batch_id": batch_id, "raw_dir": raw_dir.as_posix(), "bars": 7},
        )
        ensure_status(chart_response, 200, "chart-export")
        chart_payload = chart_response.json()

        review_response = await client.post(
            "/api/runs/review/provider",
            json={"candidate_batch_id": batch_id, "provider": "gemini-cli", "min_score": 4.0},
        )
        ensure_status(review_response, 200, "review-provider")
        review_payload = review_response.json()

        archive_response = await client.post(
            "/api/runs/archive",
            json={"candidate_batch_id": batch_id, "review_run_id": review_payload["review_run"]["id"]},
        )
        ensure_status(archive_response, 200, "archive")
        archive_payload = archive_response.json()

        runs_response = await client.get("/api/runs")
        ensure_status(runs_response, 200, "runs")

    workflow_seconds = time.perf_counter() - workflow_started
    after_workflow = storage_snapshot(sqlite_path, duckdb_path, artifact_root)

    if app.state.sqlite_engine is not None:
        app.state.sqlite_engine.dispose()

    return {
        "paths": {
            "work_root": work_root.as_posix(),
            "sqlite_path": sqlite_path.as_posix(),
            "duckdb_path": duckdb_path.as_posix(),
            "artifact_root": artifact_root.as_posix(),
        },
        "timing": {
            "startup_seconds": round(startup_seconds, 4),
            "workflow_seconds": round(workflow_seconds, 4),
        },
        "storage": {
            "before_startup": before_startup.as_dict(),
            "after_startup": after_startup.as_dict(),
            "after_workflow": after_workflow.as_dict(),
            "startup_delta": size_delta(before_startup, after_startup),
            "workflow_delta": size_delta(after_startup, after_workflow),
            "total_delta": size_delta(before_startup, after_workflow),
        },
        "workflow": {
            "candidate_batch_id": batch_id,
            "preselect_run_id": preselect["run"]["id"],
            "chart_run_id": chart_payload["run"]["id"],
            "review_run_id": review_payload["review_run"]["id"],
            "archive_run_id": archive_payload["run"]["id"],
            "candidate_count": preselect["batch"]["total"],
            "chart_artifact_count": len(chart_payload["artifacts"]),
            "review_count": len(review_payload["reviews"]),
            "recommendation_count": len(review_payload["recommendations"]),
            "archive_row_count": len(archive_payload["rows"]),
        },
        "sqlite_counts": sqlite_counts(sqlite_path),
        "duckdb_counts": duckdb_counts(duckdb_path),
    }


def ensure_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        raise RuntimeError(f"{label} returned {response.status_code}: {response.text}")


def threshold_failures(report: dict[str, Any], limits: dict[str, float]) -> list[str]:
    timing = report["timing"]
    memory = report["memory"]
    total_delta = report["storage"]["total_delta"]
    checks = {
        "startup_seconds": float(timing["startup_seconds"]),
        "workflow_seconds": float(timing["workflow_seconds"]),
        "python_peak_mb": float(memory["python_peak_mb"]),
        "sqlite_growth_mb": total_delta["sqlite_bytes"] / (1024 * 1024),
        "duckdb_growth_mb": total_delta["duckdb_bytes"] / (1024 * 1024),
        "artifact_mb": total_delta["artifact_bytes"] / (1024 * 1024),
    }
    if memory["process_peak_rss_mb"] is not None:
        checks["process_peak_rss_mb"] = float(memory["process_peak_rss_mb"])
    failures = []
    for key, value in checks.items():
        limit = limits[key]
        if value > limit:
            failures.append(f"{key}={value:.2f} exceeds {limit:.2f}")
    return failures


async def collect(work_root: Path, *, limits: dict[str, float], enforce_limits: bool) -> dict[str, Any]:
    tracemalloc.start()
    try:
        workflow_report = await run_product_workflow(work_root)
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    report = {
        "schema_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fixture": "credential-free-r7-product-workflow",
        "limits": limits,
        **workflow_report,
        "memory": {
            "python_current_mb": round(current / (1024 * 1024), 4),
            "python_peak_mb": round(peak / (1024 * 1024), 4),
            "process_peak_rss_mb": process_peak_rss_mb(),
        },
    }
    failures = threshold_failures(report, limits)
    report["thresholds"] = {
        "status": "passed" if not failures else "failed",
        "failures": failures,
    }
    if failures and enforce_limits:
        raise RuntimeError("resource envelope exceeded limits: " + "; ".join(failures))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect R7 product resource envelope evidence")
    parser.add_argument("--work-root", default=None, help="directory for temporary product DB/artifact state")
    parser.add_argument("--output", default=None, help="optional JSON report path")
    parser.add_argument("--keep-state", action="store_true", help="keep an auto-created temporary work root")
    parser.add_argument("--no-thresholds", action="store_true", help="collect evidence without failing on limits")
    return parser.parse_args()


def process_peak_rss_mb() -> float | None:
    peak = current_rss_mb()
    return None if peak is None else round(float(peak), 4)


def main() -> int:
    args = parse_args()
    cleanup_root = False
    if args.work_root:
        work_root = Path(args.work_root).expanduser()
        if not work_root.is_absolute():
            work_root = ROOT / work_root
        if work_root.exists():
            shutil.rmtree(work_root)
        work_root.mkdir(parents=True)
    else:
        work_root = Path(tempfile.mkdtemp(prefix="stocktrade-r7-resource-"))
        cleanup_root = not args.keep_state

    try:
        report = asyncio.run(
            collect(work_root, limits=DEFAULT_LIMITS, enforce_limits=not args.no_thresholds)
        )
        rendered = json.dumps(report, indent=2, sort_keys=True)
        if args.output:
            output = Path(args.output).expanduser()
            if not output.is_absolute():
                output = ROOT / output
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0
    finally:
        if cleanup_root:
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[resource-envelope] failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
