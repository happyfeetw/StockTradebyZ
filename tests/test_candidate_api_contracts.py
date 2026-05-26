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
from stocktrade_api.schemas.candidates import CandidateDetailResponse, CandidateListResponse  # noqa: E402
from stocktrade_api.storage.candidate_repository import CandidateRepository  # noqa: E402
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import Candidate, CandidateBatch, Run  # noqa: E402

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
        session.add_all(
            [
                batch_1,
                batch_2,
                Candidate(
                    batch=batch_1,
                    code="000001",
                    strategy="b2",
                    pick_date="2026-05-27",
                    close=10.1,
                    turnover_n=100.0,
                    extra_json={"signal": "breakout"},
                ),
                Candidate(
                    batch=batch_1,
                    code="000002",
                    strategy="brick",
                    pick_date="2026-05-27",
                    close=12.2,
                    turnover_n=130.0,
                    brick_growth=1.4,
                ),
                Candidate(
                    batch=batch_2,
                    code="000001",
                    strategy="b2",
                    pick_date="2026-05-28",
                    close=10.4,
                    turnover_n=110.0,
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

            b2 = repository.list_candidates(pick_date="2026-05-27", strategy="b2")[0]
            self.assertEqual((b2.batch_id, b2.code, b2.strategy), ("batch-1", "000001", "b2"))
            self.assertEqual(b2.batch.run_id, "run-1")
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

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
