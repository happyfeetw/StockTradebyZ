from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.schemas.archive import ArchiveDateResponse, ArchiveRowDetailResponse  # noqa: E402
from stocktrade_api.storage.archive_repository import ArchiveRepository  # noqa: E402
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import (  # noqa: E402
    ArchiveRow,
    ArchiveSnapshot,
    Candidate,
    CandidateBatch,
    Recommendation,
    Review,
    ReviewRun,
    Run,
)

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


def seed_archive(db_path: Path) -> None:
    engine = create_sqlite_engine(db_path)
    session_factory = create_session_factory(engine)
    archived_at = datetime.fromisoformat("2026-05-27T15:30:00")
    with session_factory() as session:
        candidate_run_1 = Run(id="run-preselect-1", kind="preselect", status="succeeded", pick_date="2026-05-27")
        candidate_run_2 = Run(id="run-preselect-2", kind="preselect", status="succeeded", pick_date="2026-05-28")
        review_run_1 = Run(id="run-review-1", kind="review", status="succeeded", pick_date="2026-05-27")
        review_run_2 = Run(id="run-review-2", kind="review", status="succeeded", pick_date="2026-05-28")
        archive_run_1 = Run(id="run-archive-1", kind="archive", status="succeeded", pick_date="2026-05-27")
        archive_run_2 = Run(id="run-archive-2", kind="archive", status="succeeded", pick_date="2026-05-28")
        batch_1 = CandidateBatch(
            id="batch-1",
            run=candidate_run_1,
            pick_date="2026-05-27",
            source="fixture",
            strategy_counts_json={"b2": 2, "brick": 1},
        )
        batch_2 = CandidateBatch(
            id="batch-2",
            run=candidate_run_2,
            pick_date="2026-05-28",
            source="fixture",
            strategy_counts_json={"b2": 1},
        )
        candidate_1 = Candidate(
            batch=batch_1,
            code="000001",
            strategy="b2",
            pick_date="2026-05-27",
            close=10.1,
            turnover_n=1.2,
            extra_json={"reason": "breakout"},
        )
        candidate_2 = Candidate(
            batch=batch_1,
            code="000002",
            strategy="brick",
            pick_date="2026-05-27",
            close=12.2,
            brick_growth=0.07,
        )
        candidate_3 = Candidate(
            batch=batch_1,
            code="000001",
            strategy="brick",
            pick_date="2026-05-27",
            close=10.1,
        )
        candidate_4 = Candidate(
            batch=batch_2,
            code="000004",
            strategy="b2",
            pick_date="2026-05-28",
            close=8.8,
        )
        review_batch_1 = ReviewRun(
            id="review-batch-1",
            run=review_run_1,
            candidate_batch=batch_1,
            pick_date="2026-05-27",
            provider="gemini-cli",
            status="succeeded",
            summary_json={"total_reviewed": 2, "recommended": 1},
        )
        review_batch_2 = ReviewRun(
            id="review-batch-2",
            run=review_run_2,
            candidate_batch=batch_2,
            pick_date="2026-05-28",
            provider="gemini-cli",
            status="succeeded",
            summary_json={"total_reviewed": 1},
        )
        review_pass = Review(
            review_run=review_batch_1,
            candidate=candidate_1,
            code="000001",
            strategy="b2",
            review_key="000001_b2",
            verdict="PASS",
            total_score=4.8,
            reviewer="gemini-cli",
            payload_json={"comment": "clean breakout"},
        )
        review_watch = Review(
            review_run=review_batch_1,
            candidate=candidate_2,
            code="000002",
            strategy="brick",
            review_key="000002_brick",
            verdict="WATCH",
            total_score=3.4,
            reviewer="gemini-cli",
            payload_json={"comment": "near miss"},
        )
        review_mismatch = Review(
            review_run=review_batch_2,
            candidate=candidate_4,
            code="000004",
            strategy="b2",
            review_key="000004_brick",
            verdict="WATCH",
            total_score=3.1,
            reviewer="gemini-cli",
            payload_json={"comment": "legacy mismatch"},
        )
        session.add_all([review_pass, review_watch, review_mismatch])
        session.flush()
        recommendation = Recommendation(
            review_run=review_batch_1,
            review=review_pass,
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
        snapshot_1 = ArchiveSnapshot(
            id="archive-2026-05-27-run-archive-1",
            run=archive_run_1,
            candidate_batch=batch_1,
            review_run=review_batch_1,
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
            source_json={"candidates": "data/candidates/candidates_latest.json"},
            summary_json={"date": "2026-05-27", "run_id": "run-archive-1"},
            archived_at=archived_at,
        )
        snapshot_2 = ArchiveSnapshot(
            id="archive-2026-05-28-run-archive-2",
            run=archive_run_2,
            candidate_batch=batch_2,
            review_run=review_batch_2,
            pick_date="2026-05-28",
            candidate_run_date="2026-05-28",
            candidate_count=1,
            reviewed_count=1,
            recommended_count=0,
            strategy_counts_json={"b2": {"total": 1, "recommended": 0, "reviewed": 1, "unreviewed": 0}},
            executed_strategies_json=["b2"],
            summary_json={"date": "2026-05-28", "run_id": "run-archive-2"},
            archived_at=archived_at,
        )
        session.add_all([snapshot_1, snapshot_2])
        session.flush()
        session.add_all(
            [
                ArchiveRow(
                    snapshot=snapshot_1,
                    candidate=candidate_1,
                    review=review_pass,
                    recommendation=recommendation,
                    pick_date="2026-05-27",
                    run_id="run-archive-1",
                    code="000001",
                    strategy="b2",
                    review_key="000001_b2",
                    status="recommended",
                    rank=1,
                    close=10.1,
                    turnover_n=1.2,
                    extra_json={"reason": "breakout"},
                    review_payload_json={"comment": "clean breakout"},
                    chart_path="data/kline/2026-05-27/000001_day.png",
                ),
                ArchiveRow(
                    snapshot=snapshot_1,
                    candidate=candidate_2,
                    review=review_watch,
                    pick_date="2026-05-27",
                    run_id="run-archive-1",
                    code="000002",
                    strategy="brick",
                    review_key="000002_brick",
                    status="reviewed",
                    close=12.2,
                    brick_growth=0.07,
                    review_payload_json={"comment": "near miss"},
                ),
                ArchiveRow(
                    snapshot=snapshot_1,
                    candidate=candidate_3,
                    pick_date="2026-05-27",
                    run_id="run-archive-1",
                    code="000001",
                    strategy="brick",
                    review_key="000001_brick",
                    status="unreviewed",
                    close=10.1,
                ),
                ArchiveRow(
                    snapshot=snapshot_2,
                    candidate=candidate_4,
                    review=review_mismatch,
                    pick_date="2026-05-28",
                    run_id="run-archive-2",
                    code="000004",
                    strategy="b2",
                    review_key="000004_brick",
                    status="reviewed",
                    close=8.8,
                    review_payload_json={"comment": "legacy mismatch"},
                ),
            ]
        )
        session.commit()
    engine.dispose()


def archive_repository(db_path: Path) -> tuple[ArchiveRepository, object]:
    engine = create_sqlite_engine(db_path)
    return ArchiveRepository(create_session_factory(engine)), engine


class ArchiveRepositoryContractTests(unittest.TestCase):
    def test_repository_lists_snapshots_and_orders_rows_by_archive_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_archive(db_path)
            repository, engine = archive_repository(db_path)

            snapshots = repository.list_snapshots()
            self.assertEqual([snapshot.pick_date for snapshot in snapshots], ["2026-05-28", "2026-05-27"])

            rows = repository.list_rows(pick_date="2026-05-27")
            self.assertEqual([row.review_key for row in rows], ["000001_b2", "000001_brick", "000002_brick"])
            self.assertEqual(rows[0].rank, 1)
            self.assertEqual(rows[0].snapshot.candidate_batch_id, "batch-1")
            self.assertEqual([row.review_key for row in repository.list_rows(status="recommended")], ["000001_b2"])
            self.assertEqual([row.review_key for row in repository.list_rows(status="unreviewed")], ["000001_brick"])
            engine.dispose()

    def test_repository_preserves_review_key_strategy_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_archive(db_path)
            repository, engine = archive_repository(db_path)

            exact = repository.list_rows(code="000001", strategy="b2")
            self.assertEqual([row.review_key for row in exact], ["000001_b2"])
            self.assertEqual(repository.list_rows(review_key="000001_brick")[0].strategy, "brick")
            self.assertEqual(repository.list_rows(code="000004", strategy="b2"), [])
            self.assertEqual(repository.list_rows(review_key="000004_brick")[0].code, "000004")
            engine.dispose()

    def test_archive_schemas_expose_summary_row_payload_and_lineage(self) -> None:
        payload = {
            "id": 1,
            "snapshot_id": "archive-2026-05-27-run-archive-1",
            "pick_date": "2026-05-27",
            "run_id": "run-archive-1",
            "candidate_batch_id": "batch-1",
            "review_run_id": "review-batch-1",
            "candidate_id": 7,
            "review_id": 8,
            "recommendation_id": 9,
            "code": "000001",
            "strategy": "b2",
            "review_key": "000001_b2",
            "status": "recommended",
            "rank": 1,
            "close": 10.1,
            "turnover_n": 1.2,
            "brick_growth": None,
            "extra": {"reason": "breakout"},
            "review_payload": {"comment": "clean breakout"},
            "chart": "data/kline/2026-05-27/000001_day.png",
            "created_at": "2026-05-27T15:30:00",
            "snapshot": {
                "id": "archive-2026-05-27-run-archive-1",
                "pick_date": "2026-05-27",
                "run_id": "run-archive-1",
                "candidate_batch_id": "batch-1",
                "review_run_id": "review-batch-1",
                "candidate_run_date": "2026-05-27",
                "candidate_count": 3,
                "reviewed_count": 2,
                "recommended_count": 1,
                "strategy_counts": {"b2": {"recommended": 1}},
                "executed_strategies": ["b2", "brick"],
                "min_score_threshold": 4.5,
                "source": {"candidates": "data/candidates/candidates_latest.json"},
                "summary": {"date": "2026-05-27"},
                "archived_at": "2026-05-27T15:30:00",
                "created_at": "2026-05-27T15:30:00",
            },
        }
        detail = ArchiveRowDetailResponse.model_validate({"row": payload})
        self.assertEqual(detail.row.review_key, "000001_b2")
        self.assertEqual(detail.row.snapshot.recommended_count, 1)
        self.assertEqual(ArchiveDateResponse.model_validate({"snapshots": [payload["snapshot"]], "rows": [payload], "total": 1}).total, 1)

    def test_archive_routes_do_not_pull_heavy_legacy_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
import stocktrade_api.routes.archive
import stocktrade_api.storage.archive_repository
print("pipeline.select_stock" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False"])


class ArchiveApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_api_lists_filters_details_and_404s(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_archive(db_path)
            app = create_app(sqlite_path=db_path)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                snapshots = await client.get("/api/archive")
                self.assertEqual(snapshots.status_code, 200)
                self.assertEqual(snapshots.json()["total"], 2)
                self.assertEqual(snapshots.json()["snapshots"][0]["pick_date"], "2026-05-28")

                filtered = await client.get(
                    "/api/archive/2026-05-27",
                    params={"strategy": "b2", "status": "recommended"},
                )
                self.assertEqual(filtered.status_code, 200)
                filtered_payload = filtered.json()
                self.assertEqual(filtered_payload["total"], 1)
                row = filtered_payload["rows"][0]
                self.assertEqual((row["review_key"], row["run_id"], row["candidate_batch_id"]), ("000001_b2", "run-archive-1", "batch-1"))
                self.assertEqual(row["snapshot"]["review_run_id"], "review-batch-1")
                self.assertEqual(row["review_payload"], {"comment": "clean breakout"})

                detail = await client.get(f"/api/archive/rows/{row['id']}")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["row"]["chart"], "data/kline/2026-05-27/000001_day.png")

                mismatch = await client.get("/api/archive/2026-05-28", params={"code": "000004", "strategy": "b2"})
                self.assertEqual(mismatch.status_code, 200)
                self.assertEqual(mismatch.json()["rows"], [])

                missing_date = await client.get("/api/archive/2026-05-29")
                self.assertEqual(missing_date.status_code, 404)

                missing_row = await client.get("/api/archive/rows/99999")
                self.assertEqual(missing_row.status_code, 404)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
