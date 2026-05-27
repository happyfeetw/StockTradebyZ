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
from stocktrade_api.schemas.reviews import ReviewDetailResponse, ReviewListResponse  # noqa: E402
from stocktrade_api.storage.duckdb import apply_migrations as apply_duckdb_migrations  # noqa: E402
from stocktrade_api.storage.duckdb import connect_duckdb  # noqa: E402
from stocktrade_api.storage.review_repository import ReviewRepository  # noqa: E402
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import Candidate, CandidateBatch, Recommendation, Review, ReviewRun, Run  # noqa: E402

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


def seed_reviews(db_path: Path) -> None:
    engine = create_sqlite_engine(db_path)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        candidate_run_1 = Run(id="run-preselect-1", kind="preselect", status="succeeded", pick_date="2026-05-27")
        candidate_run_2 = Run(id="run-preselect-2", kind="preselect", status="succeeded", pick_date="2026-05-28")
        review_run_1 = Run(id="run-review-1", kind="review", status="succeeded", pick_date="2026-05-27")
        review_run_2 = Run(id="run-review-2", kind="review", status="succeeded", pick_date="2026-05-28")
        batch_1 = CandidateBatch(
            id="batch-1",
            run=candidate_run_1,
            pick_date="2026-05-27",
            source="fixture",
            strategy_counts_json={"b2": 1, "brick": 1},
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
        )
        candidate_2 = Candidate(
            batch=batch_1,
            code="000002",
            strategy="brick",
            pick_date="2026-05-27",
            close=12.2,
        )
        candidate_3 = Candidate(
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
            summary_json={"total_reviewed": 3, "recommended": 1},
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
            review_run=review_batch_1,
            code="000001",
            strategy="brick",
            review_key="000001_brick",
            verdict="FAIL",
            total_score=2.8,
            reviewer="gemini-cli",
            payload_json={"comment": "legacy strategy mismatch fixture"},
        )
        review_next_date = Review(
            review_run=review_batch_2,
            candidate=candidate_3,
            code="000003",
            strategy="b2",
            review_key="000003_b2",
            verdict="PASS",
            total_score=4.5,
            reviewer="gemini-api",
        )
        session.add_all([review_pass, review_watch, review_mismatch, review_next_date])
        session.flush()
        session.add_all(
            [
                Recommendation(
                    review_run=review_batch_1,
                    review=review_pass,
                    rank=1,
                    code="000001",
                    strategy="b2",
                    review_key="000001_b2",
                    verdict="PASS",
                    total_score=4.8,
                    payload_json={"reason": "score threshold"},
                ),
                Recommendation(
                    review_run=review_batch_2,
                    review=review_next_date,
                    rank=1,
                    code="000003",
                    strategy="b2",
                    review_key="000003_b2",
                    verdict="PASS",
                    total_score=4.5,
                ),
            ]
        )
        session.commit()
    engine.dispose()


def seed_historical_candidate_batch(db_path: Path) -> None:
    engine = create_sqlite_engine(db_path)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        candidate_run = Run(
            id="run-preselect-history",
            kind="preselect",
            status="succeeded",
            pick_date="2026-05-25",
        )
        batch = CandidateBatch(
            id="batch-history",
            run=candidate_run,
            pick_date="2026-05-25",
            source="fixture",
            strategy_counts_json={"b2": 1, "brick": 1},
        )
        session.add_all(
            [
                Candidate(
                    batch=batch,
                    code="000001",
                    strategy="b2",
                    pick_date="2026-05-25",
                    close=10.1,
                    turnover_n=2.3,
                ),
                Candidate(
                    batch=batch,
                    code="000002",
                    strategy="brick",
                    pick_date="2026-05-25",
                    close=12.2,
                    turnover_n=1.7,
                ),
            ]
        )
        session.commit()
    engine.dispose()


def review_result(
    code: str,
    strategy: str,
    *,
    trend_structure: float = 5.0,
    price_position: float = 5.0,
    volume_behavior: float = 5.0,
    previous_abnormal_move: float = 5.0,
) -> dict:
    return {
        "code": code,
        "strategy": strategy,
        "signal_type": "fixture",
        "comment": f"{code} {strategy} fixture",
        "scores": {
            "trend_structure": trend_structure,
            "price_position": price_position,
            "volume_behavior": volume_behavior,
            "previous_abnormal_move": previous_abnormal_move,
            "classic_pattern_match": 5,
        },
    }


def review_repository(db_path: Path) -> tuple[ReviewRepository, object]:
    engine = create_sqlite_engine(db_path)
    return ReviewRepository(create_session_factory(engine)), engine


class ReviewRepositoryContractTests(unittest.TestCase):
    def test_repository_filters_reviews_and_orders_recommendations_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_reviews(db_path)
            repository, engine = review_repository(db_path)

            daily = repository.list_reviews(pick_date="2026-05-27")
            self.assertEqual([review.review_key for review in daily], ["000001_b2", "000002_brick", "000001_brick"])
            self.assertEqual(daily[0].recommendation.rank, 1)
            self.assertEqual(daily[0].review_run.run_id, "run-review-1")

            self.assertEqual([review.review_key for review in repository.list_reviews(run_id="run-review-2")], ["000003_b2"])
            self.assertEqual([review.review_key for review in repository.list_reviews(strategy="brick")], ["000002_brick", "000001_brick"])
            self.assertEqual(len(repository.list_reviews(code="000001")), 2)
            self.assertEqual([review.review_key for review in repository.list_reviews(reviewer="gemini-api")], ["000003_b2"])
            self.assertEqual([review.review_key for review in repository.list_reviews(recommendation_status="recommended")], ["000003_b2", "000001_b2"])
            self.assertEqual([review.review_key for review in repository.list_reviews(pick_date="2026-05-27", recommendation_status="reviewed")], ["000002_brick", "000001_brick"])
            engine.dispose()

    def test_repository_preserves_review_key_strategy_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_reviews(db_path)
            repository, engine = review_repository(db_path)

            exact = repository.list_reviews(code="000001", strategy="b2")
            self.assertEqual([review.review_key for review in exact], ["000001_b2"])
            self.assertEqual(repository.list_reviews(review_key="000001_b2")[0].strategy, "b2")
            self.assertEqual(repository.list_reviews(review_key="000001_brick")[0].strategy, "brick")
            engine.dispose()

    def test_review_schemas_expose_lineage_and_recommendation(self) -> None:
        payload = {
            "id": 1,
            "review_run_id": "review-batch-1",
            "run_id": "run-review-1",
            "candidate_batch_id": "batch-1",
            "candidate_id": 7,
            "pick_date": "2026-05-27",
            "code": "000001",
            "strategy": "b2",
            "review_key": "000001_b2",
            "verdict": "PASS",
            "total_score": 4.8,
            "reviewer": "gemini-cli",
            "payload": {"comment": "clean breakout"},
            "created_at": "2026-05-27T00:00:00",
            "review_run": {
                "id": "review-batch-1",
                "run_id": "run-review-1",
                "candidate_batch_id": "batch-1",
                "pick_date": "2026-05-27",
                "provider": "gemini-cli",
                "status": "succeeded",
                "summary": {"total_reviewed": 1},
                "created_at": "2026-05-27T00:00:00",
            },
            "recommendation": {
                "id": 3,
                "review_run_id": "review-batch-1",
                "review_id": 1,
                "rank": 1,
                "code": "000001",
                "strategy": "b2",
                "review_key": "000001_b2",
                "verdict": "PASS",
                "total_score": 4.8,
                "payload": {"reason": "score threshold"},
                "created_at": "2026-05-27T00:00:00",
            },
        }
        self.assertEqual(ReviewDetailResponse.model_validate({"review": payload}).review.review_key, "000001_b2")
        self.assertEqual(ReviewListResponse.model_validate({"reviews": [payload], "total": 1}).reviews[0].recommendation.rank, 1)

    def test_review_routes_do_not_pull_heavy_legacy_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
import stocktrade_api.routes.reviews
import stocktrade_api.storage.review_repository
print("pipeline.select_stock" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False"])


class ReviewApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_api_lists_filters_details_and_404s(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            seed_reviews(db_path)
            app = create_app(sqlite_path=db_path)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                all_reviews = await client.get("/api/reviews")
                self.assertEqual(all_reviews.status_code, 200)
                self.assertEqual(all_reviews.json()["total"], 4)

                filtered = await client.get(
                    "/api/reviews",
                    params={"pick_date": "2026-05-27", "strategy": "b2", "recommendation_status": "recommended"},
                )
                self.assertEqual(filtered.status_code, 200)
                filtered_payload = filtered.json()
                self.assertEqual(filtered_payload["total"], 1)
                review = filtered_payload["reviews"][0]
                self.assertEqual((review["review_key"], review["run_id"], review["candidate_batch_id"]), ("000001_b2", "run-review-1", "batch-1"))
                self.assertEqual(review["recommendation"]["rank"], 1)
                self.assertEqual(review["review_run"]["summary"], {"total_reviewed": 3, "recommended": 1})

                detail = await client.get(f"/api/reviews/{review['id']}")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["review"]["payload"], {"comment": "clean breakout"})

                mismatch = await client.get("/api/reviews", params={"code": "000001", "strategy": "b2"})
                self.assertEqual(mismatch.status_code, 200)
                self.assertEqual([item["review_key"] for item in mismatch.json()["reviews"]], ["000001_b2"])

                missing = await client.get("/api/reviews/99999")
                self.assertEqual(missing.status_code, 404)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

    async def test_review_run_api_records_historical_batch_reruns_and_duckdb_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "app.sqlite"
            duckdb_path = tmp / "analytics.duckdb"
            migrate_sqlite(db_path)
            apply_duckdb_migrations(duckdb_path)
            seed_historical_candidate_batch(db_path)
            app = create_app(sqlite_path=db_path, duckdb_path=duckdb_path)

            request_payload = {
                "candidate_batch_id": "batch-history",
                "provider": "fixture-reviewer",
                "min_score": 4.0,
                "classic_pattern_config": {"classic_pattern_enabled": True},
                "results": [
                    review_result("000001", "b2"),
                    review_result(
                        "000002",
                        "brick",
                        trend_structure=1,
                        price_position=1,
                        volume_behavior=1,
                        previous_abnormal_move=1,
                    ),
                ],
            }

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.post("/api/runs/review", json=request_payload)
                self.assertEqual(first.status_code, 200, first.text)
                first_payload = first.json()
                self.assertEqual(first_payload["run"]["kind"], "review")
                self.assertEqual(first_payload["run"]["status"], "succeeded")
                self.assertEqual(first_payload["run"]["pick_date"], "2026-05-25")
                self.assertEqual(first_payload["review_run"]["candidate_batch_id"], "batch-history")
                self.assertEqual(first_payload["review_run"]["provider"], "fixture-reviewer")
                self.assertEqual(first_payload["review_run"]["summary"]["total_candidates"], 2)
                self.assertEqual(first_payload["review_run"]["summary"]["total_reviewed"], 2)
                self.assertEqual(first_payload["review_run"]["summary"]["recommended"], 1)
                self.assertEqual(len(first_payload["reviews"]), 2)
                self.assertEqual([item["review_key"] for item in first_payload["recommendations"]], ["000001_b2"])

                second = await client.post("/api/runs/review", json=request_payload)
                self.assertEqual(second.status_code, 200, second.text)
                self.assertNotEqual(first_payload["review_run"]["id"], second.json()["review_run"]["id"])

                history = await client.get(
                    "/api/reviews",
                    params={"candidate_batch_id": "batch-history", "limit": 10},
                )
                self.assertEqual(history.status_code, 200)
                history_payload = history.json()
                self.assertEqual(history_payload["total"], 4)
                self.assertEqual(
                    {item["review_run_id"] for item in history_payload["reviews"]},
                    {first_payload["review_run"]["id"], second.json()["review_run"]["id"]},
                )

                missing = await client.post(
                    "/api/runs/review",
                    json={**request_payload, "candidate_batch_id": "not-found"},
                )
                self.assertEqual(missing.status_code, 404)

                mismatched = await client.post(
                    "/api/runs/review",
                    json={**request_payload, "results": [review_result("000003", "b2")]},
                )
                self.assertEqual(mismatched.status_code, 400)

            with connect_duckdb(duckdb_path, read_only=True) as connection:
                rows = connection.execute(
                    """
                    SELECT run_id, review_run_id, code, strategy, review_key, total_score
                    FROM review_facts
                    ORDER BY run_id, review_key
                    """
                ).fetchall()
            self.assertEqual(len(rows), 4)
            self.assertEqual({row[4] for row in rows}, {"000001_b2", "000002_brick"})
            self.assertEqual(len({row[1] for row in rows}), 2)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
