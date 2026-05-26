from __future__ import annotations

from ..storage.run_repository import RunRepository
from ..storage.sqlite_models import Run

__all__ = ["JobRuntime"]


class JobRuntime:
    def __init__(self, repository: RunRepository):
        self.repository = repository

    def queue_diagnostic_job(self) -> Run:
        run = self.repository.create_run(
            kind="diagnostic",
            summary={"mode": "diagnostic", "message": "queued without executing legacy business logic"},
        )
        self.repository.append_event(run.id, message="Diagnostic job queued")
        return run

    def run_diagnostic_job(self, *, fail: bool = False) -> Run:
        run = self.queue_diagnostic_job()
        step = self.repository.add_step(run.id, name="diagnostic")
        self.repository.transition_run(run.id, status="running")
        self.repository.transition_step(step.id, status="running")
        self.repository.append_event(run.id, step_id=step.id, message="Diagnostic job started")

        if fail:
            error = {"type": "DiagnosticFailure", "message": "diagnostic failure requested"}
            self.repository.transition_step(step.id, status="failed", error=error)
            self.repository.append_event(run.id, step_id=step.id, level="error", message=error["message"])
            return self.repository.transition_run(run.id, status="failed", summary=error)

        self.repository.transition_step(step.id, status="succeeded")
        self.repository.append_event(run.id, step_id=step.id, message="Diagnostic job succeeded")
        return self.repository.transition_run(
            run.id,
            status="succeeded",
            summary={"mode": "diagnostic", "message": "completed without executing legacy business logic"},
        )

    def request_cancellation(self, run_id: str) -> Run:
        return self.repository.request_cancellation(run_id)

    def mark_cancelled(self, run_id: str) -> Run:
        self.repository.append_event(run_id, level="warning", message="Diagnostic job cancelled")
        return self.repository.transition_run(
            run_id,
            status="cancelled",
            summary={"mode": "diagnostic", "message": "cancelled before execution"},
        )
