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
from stocktrade_api.schemas.candidates import (  # noqa: E402
    CandidateBatchDetailResponse,
    CandidateBatchListResponse,
    CandidateDetailResponse,
    CandidateListResponse,
)
from stocktrade_api.storage.candidate_repository import CandidateRepository  # noqa: E402
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import (  # noqa: E402
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


def seed_candidates(db_path: Path) -> None:
    engine = create_sqlite_engine(db_path)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        run_1 = Run(id="run-1", kind="preselect", status="succeeded", pick_date="2026-05-27")
        run_2 = Run(id="run-2", kind="preselect", status="succeeded", pick_date="2026-05-28")
        run_review_old = Run(id="run-review-old", kind="review", status="succeeded", pick_date="2026-05-27")
        run_review_new = Run(id="run-review-new", kind="review", status="succeeded", pick_date="2026-05-27")
        run_archive_1 = Run(id="run-archive-1", kind="archive", status="succeeded", pick_date="2026-05-27")
        batch_1 = CandidateBatch(
            id="batch-1",
            run=run_1,
            pick_date="2026-05-27",
            source="fixture",
            strategy_counts_json={"b2": 1, "brick": 1},
        )
        batch_2 = CandidateBatch(
            id="batch-2",
            run=run_2,
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
            turnover_n=100.0,
            extra_json={"signal": "breakout"},
        )
        candidate_2 = Candidate(
            batch=batch_1,
            code="000002",
            strategy="brick",
            pick_date="2026-05-27",
            close=12.2,
            turnover_n=130.0,
            brick_growth=1.4,
        )
        candidate_3 = Candidate(
            batch=batch_2,
            code="000001",
            strategy="b2",
            pick_date="2026-05-28",
            close=10.4,
            turnover_n=110.0,
        )
        review_batch_old = ReviewRun(
            id="review-batch-old",
            run=run_review_old,
            candidate_batch=batch_1,
            pick_date="2026-05-27",
            provider="fixture-reviewer",
            status="succeeded",
            summary_json={"total_reviewed": 1, "recommended": 0},
            created_at=datetime.fromisoformat("2026-05-27T09:00:00"),
        )
        review_batch_new = ReviewRun(
            id="review-batch-new",
            run=run_review_new,
            candidate_batch=batch_1,
            pick_date="2026-05-27",
            provider="fixture-reviewer",
            status="succeeded",
            summary_json={"total_reviewed": 2, "recommended": 1},
            created_at=datetime.fromisoformat("2026-05-27T10:00:00"),
        )
        old_review = Review(
            review_run=review_batch_old,
            candidate=candidate_1,
            code="000001",
            strategy="b2",
            review_key="000001_b2",
            verdict="WATCH",
            total_score=3.7,
            reviewer="fixture-reviewer",
        )
        new_review_pass = Review(
            review_run=review_batch_new,
            candidate=candidate_1,
            code="000001",
            strategy="b2",
            review_key="000001_b2",
            verdict="PASS",
            total_score=4.8,
            reviewer="fixture-reviewer",
        )
        new_review_watch = Review(
            review_run=review_batch_new,
            candidate=candidate_2,
            code="000002",
            strategy="brick",
            review_key="000002_brick",
            verdict="WATCH",
            total_score=3.4,
            reviewer="fixture-reviewer",
        )
        session.add_all(
            [
                batch_1,
                batch_2,
                candidate_1,
                candidate_2,
                candidate_3,
                old_review,
                new_review_pass,
                new_review_watch,
            ]
        )
        session.flush()
        session.add_all(
            [
                Recommendation(
                    review_run=review_batch_new,
                    review=new_review_pass,
                    rank=1,
                    code="000001",
                    strategy="b2",
                    review_key="000001_b2",
                    verdict="PASS",
                    total_score=4.8,
                ),
                ArchiveSnapshot(
                    id="archive-batch-1",
                    run=run_archive_1,
                    candidate_batch=batch_1,
                    review_run=review_batch_new,
                    pick_date="2026-05-27",
                    candidate_run_date="2026-05-27",
                    candidate_count=2,
                    reviewed_count=2,
                    recommended_count=1,
                    strategy_counts_json={"b2": {"recommended": 1}, "brick": {"reviewed": 1}},
                ),
            ]
        )
        session.commit()
    engine.dispose()


def candidate_repository(db_path: Path) -> tuple[CandidateRepository, object]:
    engine = create_sqlite_engine(db_path)
    return CandidateRepository(create_session_factory(engine)), engine


class CandidateRepositoryContractTests(unittest.TestCase):
    def test_repository_filters_candidates_by_date_run_strategy_and_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_candidates(db_path)
            repository, engine = candidate_repository(db_path)

            self.assertEqual([candidate.code for candidate in repository.list_candidates(pick_date="2026-05-27")], ["000001", "000002"])
            self.assertEqual([candidate.batch_id for candidate in repository.list_candidates(run_id="run-2")], ["batch-2"])
            self.assertEqual([candidate.strategy for candidate in repository.list_candidates(strategy="brick")], ["brick"])
            self.assertEqual(len(repository.list_candidates(code="000001")), 2)
            self.assertEqual([candidate.code for candidate in repository.list_candidates(batch_id="batch-1")], ["000001", "000002"])

            b2 = repository.list_candidates(pick_date="2026-05-27", strategy="b2")[0]
            self.assertEqual((b2.batch_id, b2.code, b2.strategy), ("batch-1", "000001", "b2"))
            self.assertEqual(b2.batch.run_id, "run-1")
            engine.dispose()

    def test_repository_lists_candidate_batch_overview_for_history_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_candidates(db_path)
            repository, engine = candidate_repository(db_path)

            summaries = repository.list_candidate_batches()
            self.assertEqual([summary.batch.id for summary in summaries], ["batch-2", "batch-1"])
            by_id = {summary.batch.id: summary for summary in summaries}
            self.assertEqual(by_id["batch-1"].candidate_count, 2)
            self.assertEqual(by_id["batch-1"].review_run_count, 2)
            self.assertEqual(by_id["batch-1"].latest_review_run_id, "review-batch-new")
            self.assertEqual(by_id["batch-1"].latest_reviewed_count, 2)
            self.assertEqual(by_id["batch-1"].latest_recommended_count, 1)
            self.assertEqual(by_id["batch-1"].archive_snapshot_count, 1)
            self.assertEqual(by_id["batch-2"].review_run_count, 0)

            detail = repository.get_candidate_batch("batch-1")
            self.assertEqual(detail.summary.latest_review_run_id, "review-batch-new")
            self.assertEqual([candidate.code for candidate in detail.candidates], ["000001", "000002"])
            with self.assertRaises(LookupError):
                repository.get_candidate_batch("missing-batch")
            engine.dispose()

    def test_candidate_schemas_expose_lineage_and_identity(self) -> None:
        payload = {
            "id": 1,
            "batch_id": "batch-1",
            "run_id": "run-1",
            "pick_date": "2026-05-27",
            "code": "000001",
            "strategy": "b2",
            "close": 10.1,
            "turnover_n": 100.0,
            "brick_growth": None,
            "extra": {"signal": "breakout"},
            "created_at": "2026-05-27T00:00:00",
            "batch": {
                "id": "batch-1",
                "run_id": "run-1",
                "pick_date": "2026-05-27",
                "source": "fixture",
                "strategy_counts": {"b2": 1},
                "created_at": "2026-05-27T00:00:00",
            },
        }
        self.assertEqual(CandidateDetailResponse.model_validate({"candidate": payload}).candidate.batch_id, "batch-1")
        self.assertEqual(CandidateListResponse.model_validate({"candidates": [payload], "total": 1}).total, 1)
        batch_payload = {
            **payload["batch"],
            "candidate_count": 2,
            "review_run_count": 1,
            "latest_review_run_id": "review-batch-1",
            "latest_reviewed_count": 2,
            "latest_recommended_count": 1,
            "archive_snapshot_count": 1,
        }
        self.assertEqual(CandidateBatchListResponse.model_validate({"batches": [batch_payload], "total": 1}).batches[0].candidate_count, 2)
        self.assertEqual(CandidateBatchDetailResponse.model_validate({"batch": batch_payload, "candidates": [payload], "total": 1}).batch.latest_review_run_id, "review-batch-1")

    def test_candidate_routes_do_not_pull_heavy_legacy_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
import stocktrade_api.routes.candidates
import stocktrade_api.storage.candidate_repository
print("pipeline.select_stock" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False"])


class CandidateApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_api_lists_filters_details_and_404s(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_candidates(db_path)
            app = create_app(sqlite_path=db_path)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                all_candidates = await client.get("/api/candidates")
                self.assertEqual(all_candidates.status_code, 200)
                self.assertEqual(all_candidates.json()["total"], 3)

                batches = await client.get("/api/candidate-batches")
                self.assertEqual(batches.status_code, 200)
                batches_payload = batches.json()
                self.assertEqual(batches_payload["total"], 2)
                batch_1 = next(item for item in batches_payload["batches"] if item["id"] == "batch-1")
                self.assertEqual(batch_1["candidate_count"], 2)
                self.assertEqual(batch_1["review_run_count"], 2)
                self.assertEqual(batch_1["latest_review_run_id"], "review-batch-new")
                self.assertEqual(batch_1["latest_reviewed_count"], 2)
                self.assertEqual(batch_1["latest_recommended_count"], 1)
                self.assertEqual(batch_1["archive_snapshot_count"], 1)

                batch_detail = await client.get("/api/candidate-batches/batch-1")
                self.assertEqual(batch_detail.status_code, 200)
                self.assertEqual(batch_detail.json()["total"], 2)
                self.assertEqual(
                    [(item["code"], item["strategy"]) for item in batch_detail.json()["candidates"]],
                    [("000001", "b2"), ("000002", "brick")],
                )

                batch_filtered = await client.get("/api/candidates", params={"batch_id": "batch-1"})
                self.assertEqual(batch_filtered.status_code, 200)
                self.assertEqual(batch_filtered.json()["total"], 2)

                filtered = await client.get("/api/candidates", params={"pick_date": "2026-05-27", "strategy": "b2"})
                self.assertEqual(filtered.status_code, 200)
                filtered_payload = filtered.json()
                self.assertEqual(filtered_payload["total"], 1)
                candidate = filtered_payload["candidates"][0]
                self.assertEqual((candidate["batch_id"], candidate["code"], candidate["strategy"]), ("batch-1", "000001", "b2"))
                self.assertEqual(candidate["run_id"], "run-1")
                self.assertEqual(candidate["batch"]["strategy_counts"], {"b2": 1, "brick": 1})

                detail = await client.get(f"/api/candidates/{candidate['id']}")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["candidate"]["extra"], {"signal": "breakout"})

                missing = await client.get("/api/candidates/99999")
                self.assertEqual(missing.status_code, 404)

                missing_batch = await client.get("/api/candidate-batches/missing-batch")
                self.assertEqual(missing_batch.status_code, 404)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
