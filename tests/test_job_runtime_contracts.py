from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
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
from stocktrade_api.storage.run_repository import (  # noqa: E402
    RunRepository,
    TerminalRunTransitionError,
    TerminalStepTransitionError,
)
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import Artifact, Run  # noqa: E402
from stocktrade.domain.selection import PreselectParameters, PreselectResult  # noqa: E402

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


def empty_preselect_result() -> PreselectResult:
    return PreselectResult(
        run_date="2026-05-27",
        pick_date="2026-05-27",
        candidates=[],
        meta={"strategy_candidate_counts": {}},
    )


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

    def test_runtime_does_not_cancel_terminal_run_after_late_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            repository, engine = repository_for(db_path)
            runtime = JobRuntime(repository)

            run = runtime.run_diagnostic_job()
            late_cancelled = runtime.mark_cancelled(run.id)
            detail = repository.get_run_detail(run.id)

            self.assertEqual(late_cancelled.status, "succeeded")
            self.assertEqual(detail.status, "succeeded")
            self.assertNotIn("Diagnostic job cancelled", [event.message for event in detail.events])
            engine.dispose()

    def test_app_startup_recovers_interrupted_active_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            repository, engine = repository_for(db_path)

            running = repository.create_run(kind="diagnostic", status="running", run_id="stale-running")
            repository.add_step(running.id, name="diagnostic", status="running")
            cancelling = repository.create_run(kind="preselect", status="cancelling", run_id="stale-cancelling")
            repository.add_step(cancelling.id, name="preselect", status="running")
            engine.dispose()

            app = create_app(sqlite_path=db_path, duckdb_path=None, recover_on_create=True)
            recovered_ids = {run.id for run in app.state.recovered_runs}
            self.assertEqual(recovered_ids, {"stale-running", "stale-cancelling"})

            recovered_running = app.state.run_repository.get_run_detail("stale-running")
            recovered_cancelling = app.state.run_repository.get_run_detail("stale-cancelling")

            self.assertEqual(recovered_running.status, "failed")
            self.assertEqual(recovered_running.steps[0].status, "failed")
            self.assertEqual(recovered_running.summary_json["type"], "RuntimeRecovery")
            self.assertIn("recovered interrupted diagnostic run", recovered_running.events[-1].message)

            self.assertEqual(recovered_cancelling.status, "cancelled")
            self.assertEqual(recovered_cancelling.steps[0].status, "cancelled")
            self.assertEqual(recovered_cancelling.summary_json["previous_status"], "cancelling")

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

    def test_product_workflow_jobs_are_serialized_in_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            repository, engine = repository_for(db_path)
            runtime = JobRuntime(repository)
            parameters = PreselectParameters(pick_date="2026-05-27")
            release_first = threading.Event()
            first_entered = threading.Event()
            second_entered = threading.Event()
            call_order: list[str] = []
            errors: list[BaseException] = []

            class BlockingPreselectService:
                def run(self, _parameters: PreselectParameters) -> PreselectResult:
                    call_order.append("first")
                    first_entered.set()
                    if not release_first.wait(timeout=5):
                        raise TimeoutError("first preselect service was not released")
                    return empty_preselect_result()

            class RecordingPreselectService:
                def run(self, _parameters: PreselectParameters) -> PreselectResult:
                    call_order.append("second")
                    second_entered.set()
                    return empty_preselect_result()

            def run_preselect(service: object) -> None:
                try:
                    runtime.run_preselect_job(parameters, service=service)  # type: ignore[arg-type]
                except BaseException as exc:  # pragma: no cover - reported below
                    errors.append(exc)

            first = threading.Thread(target=run_preselect, args=(BlockingPreselectService(),))
            second = threading.Thread(target=run_preselect, args=(RecordingPreselectService(),))

            first.start()
            self.assertTrue(first_entered.wait(timeout=2))
            second.start()
            time.sleep(0.1)
            self.assertFalse(second_entered.is_set())

            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(call_order, ["first", "second"])
            self.assertEqual([run.status for run in repository.list_runs(limit=10)], ["succeeded", "succeeded"])
            engine.dispose()

    def test_runtime_terminal_run_state_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            repository, engine = repository_for(db_path)

            run = JobRuntime(repository).run_diagnostic_job()
            with self.assertRaises(TerminalRunTransitionError):
                repository.transition_run(
                    run.id,
                    status="running",
                    summary={"mode": "diagnostic", "message": "late overwrite"},
                )

            detail = repository.get_run_detail(run.id)
            self.assertEqual(detail.status, "succeeded")
            self.assertEqual(detail.summary_json["message"], "completed without executing legacy business logic")
            engine.dispose()

    def test_runtime_terminal_step_state_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            migrate_sqlite(db_path)
            repository, engine = repository_for(db_path)

            run = repository.create_run(kind="diagnostic")
            step = repository.add_step(run.id, name="diagnostic")
            repository.transition_step(step.id, status="running")
            repository.transition_step(step.id, status="succeeded")

            with self.assertRaises(TerminalStepTransitionError):
                repository.transition_step(
                    step.id,
                    status="failed",
                    error={"type": "LateFailure", "message": "late overwrite"},
                )

            detail = repository.get_run_detail(run.id)
            self.assertEqual(detail.steps[0].status, "succeeded")
            self.assertIsNone(detail.steps[0].error_json)
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

    async def test_artifact_file_api_serves_only_product_owned_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "app.sqlite"
            artifact_root = tmp / "artifacts"
            product_file = artifact_root / "run-artifact" / "report.txt"
            product_file.parent.mkdir(parents=True)
            product_file.write_text("artifact body", encoding="utf-8")
            (tmp / "secret.txt").write_text("secret", encoding="utf-8")
            migrate_sqlite(db_path)
            app = create_app(sqlite_path=db_path, duckdb_path=None, artifact_root=artifact_root)

            with app.state.session_factory() as session:
                session.add(Run(id="run-artifact", kind="diagnostic", status="succeeded"))
                session.add_all(
                    [
                        Artifact(
                            id="artifact-ok",
                            run_id="run-artifact",
                            kind="log",
                            path="run-artifact/report.txt",
                            content_type="text/plain",
                            metadata_json={"label": "report"},
                        ),
                        Artifact(
                            id="artifact-legacy",
                            run_id="run-artifact",
                            kind="chart",
                            path="data/kline/2026-05-27/000001_day.png",
                            content_type="image/png",
                        ),
                        Artifact(
                            id="artifact-escape",
                            run_id="run-artifact",
                            kind="log",
                            path="../secret.txt",
                            content_type="text/plain",
                        ),
                        Artifact(
                            id="artifact-missing-file",
                            run_id="run-artifact",
                            kind="log",
                            path="run-artifact/missing.txt",
                        ),
                    ]
                )
                session.commit()

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                served = await client.get("/api/artifacts/artifact-ok")
                self.assertEqual(served.status_code, 200)
                self.assertTrue(served.headers["content-type"].startswith("text/plain"))
                self.assertEqual(served.text, "artifact body")

                artifacts = await client.get("/api/runs/run-artifact/artifacts")
                self.assertEqual(artifacts.status_code, 200)
                self.assertEqual(len(artifacts.json()["artifacts"]), 4)

                legacy = await client.get("/api/artifacts/artifact-legacy")
                self.assertEqual(legacy.status_code, 409)

                escaped = await client.get("/api/artifacts/artifact-escape")
                self.assertEqual(escaped.status_code, 403)

                missing_file = await client.get("/api/artifacts/artifact-missing-file")
                self.assertEqual(missing_file.status_code, 404)

                missing_artifact = await client.get("/api/artifacts/not-found")
                self.assertEqual(missing_artifact.status_code, 404)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
