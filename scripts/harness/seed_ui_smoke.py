#!/usr/bin/env python3
"""Seed credential-free product UI smoke data.

The fixture is intentionally small and deterministic. It writes only to paths
provided by CLI flags, defaulting to ignored `var/ui-smoke/` state.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from stocktrade_api.schemas.settings import ProductPreferenceSettings  # noqa: E402
from stocktrade_api.storage.duckdb import DuckDBAnalyticsWriter  # noqa: E402
from stocktrade_api.storage.settings_repository import PRODUCT_PREFERENCES_KEY  # noqa: E402
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import (  # noqa: E402
    AppSetting,
    ArchiveRow,
    ArchiveSnapshot,
    Artifact,
    Candidate,
    CandidateBatch,
    JobEvent,
    JobStep,
    MigrationQuarantine,
    MigrationRun,
    Recommendation,
    Review,
    ReviewRun,
    Run,
)

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"
DEFAULT_SMOKE_ROOT = ROOT / "var" / "ui-smoke"


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


def remove_database(path: Path) -> None:
    for candidate in [path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")]:
        if candidate.exists():
            candidate.unlink()


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def prepare_targets(sqlite_path: Path, duckdb_path: Path, artifact_root: Path, *, force: bool) -> None:
    existing = [path for path in [sqlite_path, duckdb_path] if path.exists()]
    if existing and not force:
        formatted = ", ".join(display_path(path) for path in existing)
        raise SystemExit(f"refusing to overwrite existing smoke database(s): {formatted}; pass --force")

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)

    if force:
        remove_database(sqlite_path)
        remove_database(duckdb_path)
        for dirname in ["run-ui-smoke-active", "run-ui-smoke-chart"]:
            shutil.rmtree(artifact_root / dirname, ignore_errors=True)


def write_artifacts(artifact_root: Path) -> dict[str, str]:
    active_path = Path("run-ui-smoke-active") / "summary.txt"
    chart_path = Path("run-ui-smoke-chart") / "charts" / "batch-ui-smoke" / "000001_b2.svg"

    active_file = artifact_root / active_path
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text(
        "UI smoke run artifact\n"
        "Used to verify Run Center artifact links, event context, and running-state layout.\n",
        encoding="utf-8",
    )

    chart_file = artifact_root / chart_path
    chart_file.parent.mkdir(parents=True, exist_ok=True)
    chart_file.write_text(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 640 360\" role=\"img\" "
        "aria-label=\"Fixture K line chart\">"
        "<rect width=\"640\" height=\"360\" fill=\"#f8fafc\"/>"
        "<path d=\"M60 280 L160 235 L245 255 L330 170 L430 196 L560 105\" "
        "fill=\"none\" stroke=\"#0f766e\" stroke-width=\"8\" stroke-linecap=\"round\"/>"
        "<g fill=\"#0f766e\"><rect x=\"110\" y=\"210\" width=\"22\" height=\"82\" rx=\"4\"/>"
        "<rect x=\"276\" y=\"150\" width=\"22\" height=\"122\" rx=\"4\"/>"
        "<rect x=\"512\" y=\"92\" width=\"22\" height=\"178\" rx=\"4\"/></g>"
        "<text x=\"36\" y=\"48\" font-family=\"Arial\" font-size=\"26\" font-weight=\"700\" "
        "fill=\"#0f172a\">000001 b2 evidence</text>"
        "<text x=\"36\" y=\"82\" font-family=\"Arial\" font-size=\"16\" fill=\"#475569\">"
        "Product artifact fixture for UI smoke</text>"
        "</svg>",
        encoding="utf-8",
    )
    return {"active": active_path.as_posix(), "chart": chart_path.as_posix()}


def migration_report_payload() -> dict[str, Any]:
    issue = {
        "section": "history",
        "source_path": "var/ui-smoke/legacy-data/history/2026-05-27/bad-row.json",
        "reason": "missing_review_key",
        "message": "Fixture quarantine row for migration error-state inspection.",
        "record_key": "bad-row",
    }
    return {
        "migration_id": "run-ui-smoke-migration",
        "dry_run": True,
        "data_root": "var/ui-smoke/legacy-data",
        "sections": {
            "candidates": {
                "files_seen": 1,
                "files_valid": 1,
                "records_seen": 3,
                "records_valid": 3,
                "by_kind": {"candidate": 3},
            },
            "reviews": {
                "files_seen": 1,
                "files_valid": 1,
                "records_seen": 2,
                "records_valid": 2,
                "by_kind": {"review": 2},
            },
            "history": {
                "files_seen": 2,
                "files_valid": 1,
                "records_seen": 4,
                "records_valid": 3,
                "by_kind": {"archive": 3},
            },
        },
        "totals": {
            "files_seen": 4,
            "files_valid": 3,
            "records_seen": 9,
            "records_valid": 8,
            "warning_count": 1,
            "quarantine_count": 1,
        },
        "warnings": [issue],
        "quarantine": [issue],
        "import_summary": None,
    }


def seed(sqlite_path: Path, duckdb_path: Path, artifact_root: Path, *, force: bool) -> dict[str, Any]:
    prepare_targets(sqlite_path, duckdb_path, artifact_root, force=force)
    artifact_paths = write_artifacts(artifact_root)
    migrate_sqlite(sqlite_path)

    engine = create_sqlite_engine(sqlite_path)
    session_factory = create_session_factory(engine)
    analytics_writer = DuckDBAnalyticsWriter(duckdb_path)
    now = datetime.fromisoformat("2026-05-27T15:30:00")

    with session_factory() as session:
        active_run = Run(
            id="run-ui-smoke-active",
            kind="preselect",
            status="running",
            pick_date="2026-05-27",
            started_at=now,
            summary_json={"phase": "strategy dispatch", "candidate_count": 3},
        )
        failed_run = Run(
            id="run-ui-smoke-failed-review",
            kind="review",
            status="failed",
            pick_date="2026-05-27",
            started_at=now,
            finished_at=now,
            summary_json={"error": "missing chart artifact fixture"},
        )
        preselect_run = Run(
            id="run-ui-smoke-preselect",
            kind="preselect",
            status="succeeded",
            pick_date="2026-05-27",
            started_at=now,
            finished_at=now,
            summary_json={"candidate_count": 3},
        )
        chart_run = Run(
            id="run-ui-smoke-chart",
            kind="chart_export",
            status="succeeded",
            pick_date="2026-05-27",
            started_at=now,
            finished_at=now,
            summary_json={"exported_count": 1},
        )
        review_run = Run(
            id="run-ui-smoke-review",
            kind="review",
            status="succeeded",
            pick_date="2026-05-27",
            started_at=now,
            finished_at=now,
            summary_json={"total_reviewed": 2, "recommended": 1},
        )
        archive_run = Run(
            id="run-ui-smoke-archive",
            kind="archive",
            status="succeeded",
            pick_date="2026-05-27",
            started_at=now,
            finished_at=now,
            summary_json={"archive_rows": 3},
        )
        migration_run_parent = Run(
            id="run-ui-smoke-migration",
            kind="legacy_import",
            status="succeeded",
            started_at=now,
            finished_at=now,
            summary_json={"dry_run": True, "quarantine_count": 1},
        )
        session.add_all(
            [
                active_run,
                failed_run,
                preselect_run,
                chart_run,
                review_run,
                archive_run,
                migration_run_parent,
            ]
        )

        load_step = JobStep(run=active_run, name="load market data", status="succeeded", started_at=now, finished_at=now)
        dispatch_step = JobStep(run=active_run, name="strategy dispatch", status="running", started_at=now)
        failed_step = JobStep(
            run=failed_run,
            name="provider review",
            status="failed",
            started_at=now,
            finished_at=now,
            error_json={"detail": "missing chart artifact fixture"},
        )
        session.add_all([load_step, dispatch_step, failed_step])
        session.add_all(
            [
                JobEvent(run=active_run, step=load_step, level="info", message="Loaded 3 smoke candidates."),
                JobEvent(run=active_run, step=dispatch_step, level="info", message="Dispatching b2 and brick strategies."),
                JobEvent(run=failed_run, step=failed_step, level="error", message="Provider review failed: missing chart artifact fixture."),
            ]
        )

        active_artifact = Artifact(
            id="artifact-ui-smoke-active-summary",
            run=active_run,
            kind="log",
            path=artifact_paths["active"],
            content_type="text/plain",
            metadata_json={"source": "ui_smoke"},
        )
        chart_artifact = Artifact(
            id="artifact-ui-smoke-chart-000001-b2",
            run=chart_run,
            kind="chart",
            path=artifact_paths["chart"],
            content_type="image/svg+xml",
            metadata_json={
                "source": "product:ui_smoke",
                "artifact_scope": "strategy",
                "candidate_batch_id": "batch-ui-smoke",
                "pick_date": "2026-05-27",
                "code": "000001",
                "strategy": "b2",
                "review_key": "000001_b2",
            },
        )
        session.add_all([active_artifact, chart_artifact])

        batch = CandidateBatch(
            id="batch-ui-smoke",
            run=preselect_run,
            pick_date="2026-05-27",
            source="fixture:ui-smoke",
            strategy_counts_json={"b2": 2, "brick": 1},
        )
        candidates = [
            Candidate(
                batch=batch,
                code="000001",
                strategy="b2",
                pick_date="2026-05-27",
                close=10.1,
                turnover_n=1.2,
                extra_json={"reason": "breakout"},
            ),
            Candidate(
                batch=batch,
                code="000001",
                strategy="brick",
                pick_date="2026-05-27",
                close=10.1,
                brick_growth=0.05,
            ),
            Candidate(
                batch=batch,
                code="000002",
                strategy="brick",
                pick_date="2026-05-27",
                close=12.2,
                turnover_n=1.7,
                brick_growth=0.07,
            ),
        ]
        session.add_all(candidates)

        review_batch = ReviewRun(
            id="review-ui-smoke",
            run=review_run,
            candidate_batch=batch,
            pick_date="2026-05-27",
            provider="gemini-cli",
            status="succeeded",
            summary_json={"total_reviewed": 2, "recommended": 1},
        )
        reviews = [
            Review(
                review_run=review_batch,
                candidate=candidates[0],
                code="000001",
                strategy="b2",
                review_key="000001_b2",
                verdict="PASS",
                total_score=4.8,
                reviewer="gemini-cli",
                payload_json={"comment": "clean breakout"},
            ),
            Review(
                review_run=review_batch,
                candidate=candidates[2],
                code="000002",
                strategy="brick",
                review_key="000002_brick",
                verdict="WATCH",
                total_score=3.4,
                reviewer="gemini-cli",
                payload_json={"comment": "near miss"},
            ),
        ]
        session.add_all(reviews)
        session.flush()

        recommendation = Recommendation(
            review_run=review_batch,
            review=reviews[0],
            rank=1,
            code="000001",
            strategy="b2",
            review_key="000001_b2",
            verdict="PASS",
            total_score=4.8,
            payload_json={"reason": "score threshold"},
        )
        session.add(recommendation)
        session.flush()

        snapshot = ArchiveSnapshot(
            id="archive-ui-smoke-2026-05-27",
            run=archive_run,
            candidate_batch=batch,
            review_run=review_batch,
            pick_date="2026-05-27",
            candidate_run_date="2026-05-27",
            candidate_count=3,
            reviewed_count=2,
            recommended_count=1,
            strategy_counts_json={
                "b2": {"total": 1, "recommended": 1, "reviewed": 0, "unreviewed": 0},
                "brick": {"total": 2, "recommended": 0, "reviewed": 1, "unreviewed": 1},
            },
            executed_strategies_json=["b2", "brick"],
            min_score_threshold=4.5,
            source_json={"candidates": "fixture:ui-smoke"},
            summary_json={"date": "2026-05-27", "run_id": archive_run.id},
            archived_at=now,
        )
        session.add(snapshot)
        session.flush()

        archive_rows = [
            ArchiveRow(
                snapshot=snapshot,
                candidate=candidates[0],
                review=reviews[0],
                recommendation=recommendation,
                chart_artifact=chart_artifact,
                pick_date="2026-05-27",
                run_id=archive_run.id,
                code="000001",
                strategy="b2",
                review_key="000001_b2",
                status="recommended",
                rank=1,
                close=10.1,
                turnover_n=1.2,
                extra_json={"reason": "breakout"},
                review_payload_json={"comment": "clean breakout"},
                chart_path=artifact_paths["chart"],
            ),
            ArchiveRow(
                snapshot=snapshot,
                candidate=candidates[1],
                pick_date="2026-05-27",
                run_id=archive_run.id,
                code="000001",
                strategy="brick",
                review_key="000001_brick",
                status="unreviewed",
                close=10.1,
            ),
            ArchiveRow(
                snapshot=snapshot,
                candidate=candidates[2],
                review=reviews[1],
                pick_date="2026-05-27",
                run_id=archive_run.id,
                code="000002",
                strategy="brick",
                review_key="000002_brick",
                status="reviewed",
                close=12.2,
                turnover_n=1.7,
                brick_growth=0.07,
                review_payload_json={"comment": "near miss"},
            ),
        ]
        session.add_all(archive_rows)

        preferences = ProductPreferenceSettings(
            theme="light",
            table_density="compact",
            default_strategy_ids=["b2", "brick"],
            analytics_default_limit=100,
            candidate_page_size=50,
            review_page_size=50,
            archive_page_size=50,
            chart_export_enabled=True,
            auto_archive_after_review=False,
        )
        session.add(AppSetting(key=PRODUCT_PREFERENCES_KEY, value_json=preferences.model_dump(mode="json"), updated_at=now))

        report_payload = migration_report_payload()
        session.add(
            MigrationRun(
                id=migration_run_parent.id,
                source_root=report_payload["data_root"],
                status="succeeded",
                started_at=now,
                finished_at=now,
                report_json=report_payload,
            )
        )
        session.add(
            MigrationQuarantine(
                migration_run_id=migration_run_parent.id,
                source_path=report_payload["quarantine"][0]["source_path"],
                reason=report_payload["quarantine"][0]["reason"],
                payload_json=report_payload["quarantine"][0],
            )
        )

        session.flush()
        analytics_writer.record_candidate_import(run_id=preselect_run.id, batch=batch, candidates=candidates)
        analytics_writer.record_review_import(run_id=review_run.id, review_run=review_batch, reviews=reviews)
        analytics_writer.record_archive_import(run_id=archive_run.id, snapshot=snapshot, rows=archive_rows)
        session.commit()

    engine.dispose()
    return {
        "sqlite_path": sqlite_path.as_posix(),
        "duckdb_path": duckdb_path.as_posix(),
        "artifact_root": artifact_root.as_posix(),
        "routes": {
            "overview": "/",
            "run_center": "/runs?run_id=run-ui-smoke-active",
            "candidates": "/candidates?pick_date=2026-05-27",
            "reviews": "/reviews?pick_date=2026-05-27",
            "archive": "/archive?pick_date=2026-05-27",
            "analytics": "/analytics?pick_date=2026-05-27",
            "settings": "/settings",
            "migrations": "/migrations",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed local UI smoke data")
    parser.add_argument("--sqlite-path", default=str(DEFAULT_SMOKE_ROOT / "db" / "app.sqlite"))
    parser.add_argument("--duckdb-path", default=str(DEFAULT_SMOKE_ROOT / "db" / "analytics.duckdb"))
    parser.add_argument("--artifact-root", default=str(DEFAULT_SMOKE_ROOT / "artifacts"))
    parser.add_argument("--force", action="store_true", help="replace existing smoke databases and smoke artifact directories")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = seed(
        resolve_path(args.sqlite_path),
        resolve_path(args.duckdb_path),
        resolve_path(args.artifact_root),
        force=args.force,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
