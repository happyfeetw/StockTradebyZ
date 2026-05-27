from __future__ import annotations

from typing import TYPE_CHECKING

from ..storage.run_repository import RunRepository
from ..storage.sqlite_models import CandidateBatch, Run

if TYPE_CHECKING:
    from stocktrade.domain.selection import PreselectParameters, PreselectResult, PreselectService

    from ..schemas.archive import ArchiveRunCreateRequest
    from ..schemas.charts import ChartExportRunCreateRequest
    from ..schemas.reviews import ReviewRunCreateRequest
    from ..services.archive_runs import ArchiveRunService
    from ..services.chart_runs import ChartExportRunService, CreatedChartExport
    from ..services.review_runs import ReviewRunService
    from ..storage.archive_repository import CreatedArchive
    from ..storage.duckdb import DuckDBAnalyticsWriter
    from ..storage.review_repository import CreatedReviewRun

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
        analytics_writer: "DuckDBAnalyticsWriter | None" = None,
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
            if analytics_writer is not None:
                analytics_writer.record_candidate_import(
                    run_id=run.id,
                    batch=batch,
                    candidates=batch.candidates,
                )
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

    def run_review_job(
        self,
        request: "ReviewRunCreateRequest",
        *,
        service: "ReviewRunService",
    ) -> tuple[Run, "CreatedReviewRun"]:
        run = self.repository.create_run(
            kind="review",
            summary={
                "mode": "review",
                "candidate_batch_id": request.candidate_batch_id,
                "message": "queued",
            },
        )
        step = self.repository.add_step(run.id, name="review")
        self.repository.append_event(run.id, message="Review job queued")
        self.repository.transition_run(run.id, status="running")
        self.repository.transition_step(step.id, status="running")
        self.repository.append_event(run.id, step_id=step.id, message="Review job started")

        try:
            created = service.run(run_id=run.id, request=request)
            self.repository.transition_step(step.id, status="succeeded")
            self.repository.append_event(
                run.id,
                step_id=step.id,
                message=f"Review job recorded {len(created.reviews)} reviews",
            )
            final_run = self.repository.transition_run(
                run.id,
                status="succeeded",
                pick_date=created.review_run.pick_date,
                summary=created.review_run.summary_json,
            )
            return final_run, created
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            self.repository.transition_step(step.id, status="failed", error=error)
            self.repository.append_event(run.id, step_id=step.id, level="error", message=error["message"])
            self.repository.transition_run(run.id, status="failed", summary=error)
            raise

    def run_archive_job(
        self,
        request: "ArchiveRunCreateRequest",
        *,
        service: "ArchiveRunService",
    ) -> tuple[Run, "CreatedArchive"]:
        run = self.repository.create_run(
            kind="archive",
            summary={
                "mode": "archive",
                "candidate_batch_id": request.candidate_batch_id,
                "review_run_id": request.review_run_id,
                "message": "queued",
            },
        )
        step = self.repository.add_step(run.id, name="archive")
        self.repository.append_event(run.id, message="Archive job queued")
        self.repository.transition_run(run.id, status="running")
        self.repository.transition_step(step.id, status="running")
        self.repository.append_event(run.id, step_id=step.id, message="Archive job started")

        try:
            created = service.run(run_id=run.id, request=request)
            self.repository.transition_step(step.id, status="succeeded")
            self.repository.append_event(
                run.id,
                step_id=step.id,
                message=f"Archive job recorded {len(created.rows)} rows",
            )
            final_run = self.repository.transition_run(
                run.id,
                status="succeeded",
                pick_date=created.snapshot.pick_date,
                summary=created.snapshot.summary_json,
            )
            return final_run, created
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            self.repository.transition_step(step.id, status="failed", error=error)
            self.repository.append_event(run.id, step_id=step.id, level="error", message=error["message"])
            self.repository.transition_run(run.id, status="failed", summary=error)
            raise

    def run_chart_export_job(
        self,
        request: "ChartExportRunCreateRequest",
        *,
        service: "ChartExportRunService",
    ) -> tuple[Run, "CreatedChartExport"]:
        run = self.repository.create_run(
            kind="chart_export",
            summary={
                "mode": "chart_export",
                "candidate_batch_id": request.candidate_batch_id,
                "message": "queued",
            },
        )
        step = self.repository.add_step(run.id, name="chart_export")
        self.repository.append_event(run.id, message="Chart export job queued")
        self.repository.transition_run(run.id, status="running")
        self.repository.transition_step(step.id, status="running")
        self.repository.append_event(run.id, step_id=step.id, message="Chart export job started")

        try:
            created = service.run(run_id=run.id, request=request)
            self.repository.transition_step(step.id, status="succeeded")
            self.repository.append_event(
                run.id,
                step_id=step.id,
                message=f"Chart export job generated {len(created.artifacts)} artifacts",
            )
            final_run = self.repository.transition_run(
                run.id,
                status="succeeded",
                pick_date=str(created.summary.get("pick_date") or ""),
                summary=created.summary,
            )
            return final_run, created
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            self.repository.transition_step(step.id, status="failed", error=error)
            self.repository.append_event(run.id, step_id=step.id, level="error", message=error["message"])
            self.repository.transition_run(run.id, status="failed", summary=error)
            raise
