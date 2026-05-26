from __future__ import annotations

from typing import TYPE_CHECKING

from ..storage.run_repository import RunRepository
from ..storage.sqlite_models import CandidateBatch, Run

if TYPE_CHECKING:
    from stocktrade.domain.selection import PreselectParameters, PreselectResult, PreselectService

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

    def run_preselect_job(
        self,
        parameters: "PreselectParameters",
        *,
        service: "PreselectService",
    ) -> tuple[Run, CandidateBatch, "PreselectResult"]:
        run = self.repository.create_run(
            kind="preselect",
            pick_date=parameters.pick_date,
            summary={"mode": "preselect", "message": "queued"},
        )
        step = self.repository.add_step(run.id, name="preselect")
        self.repository.append_event(run.id, message="Preselect job queued")
        self.repository.transition_run(run.id, status="running")
        self.repository.transition_step(step.id, status="running")
        self.repository.append_event(run.id, step_id=step.id, message="Preselect job started")

        try:
            result = service.run(parameters)
            batch = self.repository.create_candidate_batch(run_id=run.id, result=result)
            self.repository.transition_step(step.id, status="succeeded")
            self.repository.append_event(
                run.id,
                step_id=step.id,
                message=f"Preselect job selected {len(result.candidates)} candidates",
            )
            final_run = self.repository.transition_run(
                run.id,
                status="succeeded",
                pick_date=result.pick_date,
                summary=result.meta,
            )
            return final_run, batch, result
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            self.repository.transition_step(step.id, status="failed", error=error)
            self.repository.append_event(run.id, step_id=step.id, level="error", message=error["message"])
            self.repository.transition_run(run.id, status="failed", summary=error)
            raise
