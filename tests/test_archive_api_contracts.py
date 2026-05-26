from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.schemas.archive import ArchiveDetailResponse, ArchiveListResponse  # noqa: E402
from stocktrade_api.storage.archive_repository import ArchiveRepository, ArchiveSnapshotNotFoundError  # noqa: E402
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import (  # noqa: E402
    ArchiveRow,
    ArchiveSnapshot,
    Artifact,
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
    with session_factory() as session:
        candidate_run_1 = Run(id="run-preselect-1", kind="preselect", status="succeeded", pick_date="2026-05-27")
        candidate_run_2 = Run(id="run-preselect-2", kind="preselect", status="succeeded", pick_date="2026-05-28")
        review_run_1 = Run(id="run-review-1", kind="review", status="succeeded", pick_date="2026-05-27")
        review_run_2 = Run(id="run-review-2", kind="review", status="succeeded", pick_date="2026-05-28")
        archive_run_1 = Run(
            id="run-archive-1",
            kind="archive",
            status="succeeded",
            pick_date="2026-05-27",
            summary_json={"archive_snapshot_id": "archive-20260527"},
        )
        archive_run_2 = Run(
            id="run-archive-2",
            kind="archive",
            status="succeeded",
            pick_date="2026-05-28",
            summary_json={"archive_snapshot_id": "archive-20260528"},
        )
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
        candidate_pass = Candidate(
            batch=batch_1,
            code="000001",
            strategy="b2",
            pick_date="2026-05-27",
            close=10.1,
            turnover_n=2.2,
            extra_json={"source": "candidate-pass"},
        )
        candidate_watch = Candidate(
            batch=batch_1,
            code="000002",
            strategy="brick",
            pick_date="2026-05-27",
            close=12.2,
            brick_growth=0.18,
        )
        candidate_unreviewed = Candidate(
            batch=batch_1,
            code="000004",
            strategy="b2",
            pick_date="2026-05-27",
            close=8.9,
        )
        candidate_next = Candidate(
            batch=batch_2,
            code="000003",
            strategy="b2",
            pick_date="2026-05-28",
            close=9.8,
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
            provider="gemini-api",
            status="succeeded",
            summary_json={"total_reviewed": 1, "recommended": 1},
        )
        review_pass = Review(
            review_run=review_batch_1,
            candidate=candidate_pass,
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
            candidate=candidate_watch,
            code="000002",
            strategy="brick",
            review_key="000002_brick",
            verdict="WATCH",
            total_score=3.4,
            reviewer="gemini-cli",
            payload_json={"comment": "near miss"},
        )
        review_next = Review(
            review_run=review_batch_2,
            candidate=candidate_next,
            code="000003",
            strategy="b2",
            review_key="000003_b2",
            verdict="PASS",
            total_score=4.5,
            reviewer="gemini-api",
        )
        recommendation_pass = Recommendation(
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
        recommendation_next = Recommendation(
            review_run=review_batch_2,
            review=review_next,
            rank=1,
            code="000003",
            strategy="b2",
            review_key="000003_b2",
            verdict="PASS",
            total_score=4.5,
        )
        chart_artifact = Artifact(
            id="artifact-chart-1",
            run=archive_run_1,
            kind="chart",
            path="charts/2026-05-27/000001_day.png",
            content_type="image/png",
            metadata_json={"code": "000001", "strategy": "b2"},
        )
        snapshot_1 = ArchiveSnapshot(
            id="archive-20260527",
            run=archive_run_1,
            candidate_batch=batch_1,
            review_run=review_batch_1,
            pick_date="2026-05-27",
            source="fixture",
            summary_json={
                "candidate_count": 3,
                "reviewed_count": 2,
                "recommended_count": 1,
                "strategy_counts": {"b2": 2, "brick": 1},
                "executed_strategies": ["b2", "brick"],
                "min_score_threshold": 4.0,
                "source": "fixture",
            },
        )
        snapshot_2 = ArchiveSnapshot(
            id="archive-20260528",
            run=archive_run_2,
            candidate_batch=batch_2,
            review_run=review_batch_2,
            pick_date="2026-05-28",
            source="fixture",
            summary_json={
                "candidate_count": 1,
                "reviewed_count": 1,
                "recommended_count": 1,
                "strategy_counts": {"b2": 1},
                "executed_strategies": ["b2"],
                "min_score_threshold": 4.0,
                "source": "fixture",
            },
        )
        session.add_all(
            [
                ArchiveRow(
                    snapshot=snapshot_1,
                    candidate=candidate_pass,
                    review=review_pass,
                    recommendation=recommendation_pass,
                    chart_artifact=chart_artifact,
                    pick_date="2026-05-27",
                    run_id="run-archive-1",
                    code="000001",
                    strategy="b2",
                    review_key="000001_b2",
                    status="recommended",
                    rank=1,
                    close=10.1,
                    turnover_n=2.2,
                    extra_json={"source": "candidate-pass"},
                    review_json={"comment": "clean breakout"},
                    chart_path="charts/2026-05-27/000001_day.png",
                ),
                ArchiveRow(
                    snapshot=snapshot_1,
                    candidate=candidate_watch,
                    review=review_watch,
                    pick_date="2026-05-27",
                    run_id="run-archive-1",
                    code="000002",
                    strategy="brick",
                    review_key="000002_brick",
                    status="reviewed",
                    close=12.2,
                    brick_growth=0.18,
                    review_json={"comment": "near miss"},
                ),
                ArchiveRow(
                    snapshot=snapshot_1,
                    candidate=candidate_unreviewed,
                    pick_date="2026-05-27",
                    run_id="run-archive-1",
                    code="000004",
                    strategy="b2",
                    review_key="000004_b2",
                    status="unreviewed",
                    close=8.9,
                ),
                ArchiveRow(
                    snapshot=snapshot_2,
                    candidate=candidate_next,
                    review=review_next,
                    recommendation=recommendation_next,
                    pick_date="2026-05-28",
                    run_id="run-archive-2",
                    code="000003",
                    strategy="b2",
                    review_key="000003_b2",
                    status="recommended",
                    rank=1,
                    close=9.8,
                ),
            ]
        )
        session.commit()
    engine.dispose()


def archive_repository(db_path: Path) -> tuple[ArchiveRepository, object]:
    engine = create_sqlite_engine(db_path)
    return ArchiveRepository(create_session_factory(engine)), engine


class ArchiveRepositoryContractTests(unittest.TestCase):
    def test_repository_lists_snapshots_and_filters_rows_by_archive_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_archive(db_path)
            repository, engine = archive_repository(db_path)

            snapshots = repository.list_snapshots()
            self.assertEqual([snapshot.id for snapshot in snapshots], ["archive-20260528", "archive-20260527"])
            self.assertEqual(repository.list_snapshots(pick_date="2026-05-27")[0].summary_json["candidate_count"], 3)

            rows = repository.list_rows(pick_date="2026-05-27")
            self.assertEqual([row.review_key for row in rows], ["000001_b2", "000002_brick", "000004_b2"])
            self.assertEqual([row.status for row in rows], ["recommended", "reviewed", "unreviewed"])
            self.assertEqual(rows[0].snapshot.run_id, "run-archive-1")
            self.assertEqual(rows[0].chart_artifact_id, "artifact-chart-1")

            self.assertEqual([row.review_key for row in repository.list_rows(run_id="run-archive-2")], ["000003_b2"])
            self.assertEqual(
                [row.review_key for row in repository.list_rows(pick_date="2026-05-27", strategy="b2")],
                ["000001_b2", "000004_b2"],
            )
            self.assertEqual(
                [row.review_key for row in repository.list_rows(pick_date="2026-05-27", status="recommended")],
                ["000001_b2"],
            )
            self.assertEqual(
                [row.review_key for row in repository.list_rows(pick_date="2026-05-27", status="reviewed")],
                ["000002_brick"],
            )
            self.assertEqual(
                [row.review_key for row in repository.list_rows(pick_date="2026-05-27", status="unreviewed")],
                ["000004_b2"],
            )
            self.assertEqual(
                [row.review_key for row in repository.list_rows(pick_date="2026-05-27", code="000001")],
                ["000001_b2"],
            )
            engine.dispose()

    def test_repository_gets_latest_snapshot_or_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_archive(db_path)
            repository, engine = archive_repository(db_path)

            self.assertEqual(repository.get_snapshot(pick_date="2026-05-27").id, "archive-20260527")
            self.assertEqual(repository.get_snapshot(pick_date="2026-05-27", run_id="run-archive-1").review_run_id, "review-batch-1")
            with self.assertRaises(ArchiveSnapshotNotFoundError):
                repository.get_snapshot(pick_date="2026-05-29")
            with self.assertRaises(ValueError):
                repository.list_rows(status="hidden")
            engine.dispose()

    def test_archive_schemas_expose_summary_status_and_lineage(self) -> None:
        payload = {
            "snapshot": {
                "id": "archive-20260527",
                "pick_date": "2026-05-27",
                "run_id": "run-archive-1",
                "candidate_batch_id": "batch-1",
                "review_run_id": "review-batch-1",
                "source": "fixture",
                "summary": {"candidate_count": 3, "recommended_count": 1},
                "created_at": "2026-05-27T00:00:00",
            },
            "rows": [
                {
                    "id": 1,
                    "snapshot_id": "archive-20260527",
                    "candidate_id": 7,
                    "review_id": 8,
                    "recommendation_id": 9,
                    "chart_artifact_id": "artifact-chart-1",
                    "pick_date": "2026-05-27",
                    "run_id": "run-archive-1",
                    "code": "000001",
                    "strategy": "b2",
                    "review_key": "000001_b2",
                    "status": "recommended",
                    "rank": 1,
                    "close": 10.1,
                    "turnover_n": 2.2,
                    "brick_growth": None,
                    "extra": {"source": "candidate-pass"},
                    "review": {"comment": "clean breakout"},
                    "chart_path": "charts/2026-05-27/000001_day.png",
                    "created_at": "2026-05-27T00:00:00",
                    "snapshot": {
                        "id": "archive-20260527",
                        "pick_date": "2026-05-27",
                        "run_id": "run-archive-1",
                        "candidate_batch_id": "batch-1",
                        "review_run_id": "review-batch-1",
                        "source": "fixture",
                        "summary": {"candidate_count": 3, "recommended_count": 1},
                        "created_at": "2026-05-27T00:00:00",
                    },
                }
            ],
            "total": 1,
        }
        detail = ArchiveDetailResponse.model_validate(payload)
        self.assertEqual(detail.snapshot.summary["candidate_count"], 3)
        self.assertEqual(detail.rows[0].status, "recommended")
        self.assertEqual(detail.rows[0].chart_artifact_id, "artifact-chart-1")
        self.assertEqual(
            ArchiveListResponse.model_validate({"archives": [payload["snapshot"]], "total": 1}).archives[0].run_id,
            "run-archive-1",
        )

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
                archive_list = await client.get("/api/archive")
                self.assertEqual(archive_list.status_code, 200)
                archive_list_payload = archive_list.json()
                self.assertEqual(archive_list_payload["total"], 2)
                self.assertEqual([item["id"] for item in archive_list_payload["archives"]], ["archive-20260528", "archive-20260527"])

                filtered_list = await client.get("/api/archive", params={"pick_date": "2026-05-27"})
                self.assertEqual(filtered_list.status_code, 200)
                self.assertEqual(filtered_list.json()["archives"][0]["summary"]["candidate_count"], 3)

                detail = await client.get("/api/archive/2026-05-27")
                self.assertEqual(detail.status_code, 200)
                detail_payload = detail.json()
                self.assertEqual(detail_payload["total"], 3)
                self.assertEqual(detail_payload["snapshot"]["candidate_batch_id"], "batch-1")
                self.assertEqual(detail_payload["snapshot"]["review_run_id"], "review-batch-1")
                self.assertEqual(detail_payload["snapshot"]["summary"]["recommended_count"], 1)
                self.assertEqual(
                    [row["review_key"] for row in detail_payload["rows"]],
                    ["000001_b2", "000002_brick", "000004_b2"],
                )

                recommended = await client.get("/api/archive/2026-05-27", params={"status": "recommended"})
                self.assertEqual(recommended.status_code, 200)
                recommended_row = recommended.json()["rows"][0]
                self.assertEqual(recommended_row["review_key"], "000001_b2")
                self.assertEqual(recommended_row["rank"], 1)
                self.assertEqual(recommended_row["chart_artifact_id"], "artifact-chart-1")
                self.assertEqual(recommended_row["snapshot"]["run_id"], "run-archive-1")

                strategy_filtered = await client.get("/api/archive/2026-05-27", params={"strategy": "b2"})
                self.assertEqual(strategy_filtered.status_code, 200)
                self.assertEqual(
                    [row["review_key"] for row in strategy_filtered.json()["rows"]],
                    ["000001_b2", "000004_b2"],
                )

                run_filtered = await client.get("/api/archive/2026-05-28", params={"run_id": "run-archive-2"})
                self.assertEqual(run_filtered.status_code, 200)
                self.assertEqual([row["review_key"] for row in run_filtered.json()["rows"]], ["000003_b2"])

                missing = await client.get("/api/archive/2026-05-29")
                self.assertEqual(missing.status_code, 404)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
