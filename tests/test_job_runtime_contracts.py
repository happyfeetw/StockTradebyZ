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

from stocktrade_api.jobs.runtime import JobRuntime  # noqa: E402
from stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.schemas.runs import RunDetail, RunEventsResponse, RunListResponse, RunSummary  # noqa: E402
from stocktrade_api.storage.run_repository import RunRepository  # noqa: E402
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


def repository_for(db_path: Path) -> tuple[RunRepository, object]:
    engine = create_sqlite_engine(db_path)
    return RunRepository(create_session_factory(engine)), engine


class JobRuntimeContractTests(unittest.TestCase):
    def test_run_schemas_accept_runtime_payloads(self) -> None:
        payload = {
            "id": "run-1",
            "kind": "diagnostic",
            "status": "succeeded",
            "pick_date": None,
            "started_at": None,
            "finished_at": None,
            "summary": {"mode": "diagnostic"},
            "created_at": "2026-05-27T00:00:00",
        }
        self.assertEqual(RunSummary.model_validate(payload).kind, "diagnostic")
        self.assertEqual(RunListResponse.model_validate({"runs": [payload]}).runs[0].id, "run-1")
        self.assertEqual(RunEventsResponse.model_validate({"events": []}).events, [])
        self.assertEqual(RunDetail.model_validate({**payload, "steps": [], "events": [], "artifacts": []}).status, "succeeded")

    def test_runtime_success_records_steps_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            repository, engine = repository_for(db_path)

            run = JobRuntime(repository).run_diagnostic_job()
            detail = repository.get_run_detail(run.id)

            self.assertEqual(detail.status, "succeeded")
            self.assertEqual([step.status for step in detail.steps], ["succeeded"])
            self.assertIn("Diagnostic job succeeded", [event.message for event in detail.events])
            engine.dispose()

    def test_runtime_failure_records_failed_state_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            repository, engine = repository_for(db_path)

            run = JobRuntime(repository).run_diagnostic_job(fail=True)
            detail = repository.get_run_detail(run.id)

            self.assertEqual(detail.status, "failed")
            self.assertEqual(detail.steps[0].status, "failed")
            self.assertEqual(detail.steps[0].error_json["type"], "DiagnosticFailure")
            engine.dispose()

    def test_runtime_cancellation_transitions_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            repository, engine = repository_for(db_path)
            runtime = JobRuntime(repository)

            run = runtime.queue_diagnostic_job()
            cancelling = runtime.request_cancellation(run.id)
            cancelled = runtime.mark_cancelled(run.id)

            self.assertEqual(cancelling.status, "cancelling")
            self.assertEqual(cancelled.status, "cancelled")
            self.assertIn("Cancellation requested", [event.message for event in repository.list_events(run.id)])
            engine.dispose()

    def test_runtime_imports_do_not_pull_heavy_legacy_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
import stocktrade_api.jobs.runtime
import stocktrade_api.routes.runs
print("pipeline.select_stock" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False"])


class JobRuntimeApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_api_contracts_cover_create_list_detail_events_and_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            app = create_app(sqlite_path=db_path)
            repository: RunRepository = app.state.run_repository
            cancellable = repository.create_run(kind="diagnostic", status="running")

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = await client.post("/api/runs/diagnostic", json={"fail": False})
                self.assertEqual(created.status_code, 200)
                created_payload = created.json()
                self.assertEqual(created_payload["kind"], "diagnostic")
                self.assertEqual(created_payload["status"], "succeeded")
                self.assertEqual(created_payload["steps"][0]["status"], "succeeded")

                listed = await client.get("/api/runs")
                self.assertEqual(listed.status_code, 200)
                self.assertGreaterEqual(len(listed.json()["runs"]), 2)

                detail = await client.get(f"/api/runs/{created_payload['id']}")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["events"][-1]["message"], "Diagnostic job succeeded")

                events = await client.get(f"/api/jobs/{created_payload['id']}/events")
                self.assertEqual(events.status_code, 200)
                self.assertGreaterEqual(len(events.json()["events"]), 3)

                artifacts = await client.get(f"/api/runs/{created_payload['id']}/artifacts")
                self.assertEqual(artifacts.status_code, 200)
                self.assertEqual(artifacts.json(), {"artifacts": []})

                cancelled = await client.post(f"/api/runs/{cancellable.id}/cancel")
                self.assertEqual(cancelled.status_code, 200)
                self.assertEqual(cancelled.json()["status"], "cancelling")

                missing = await client.get("/api/runs/not-found")
                self.assertEqual(missing.status_code, 404)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
