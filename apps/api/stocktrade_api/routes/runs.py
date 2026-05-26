from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_job_runtime, get_run_repository
from ..jobs.runtime import JobRuntime
from ..schemas.runs import (
    ArtifactResponse,
    DiagnosticRunRequest,
    JobEventResponse,
    JobStepResponse,
    RunArtifactsResponse,
    RunDetail,
    RunEventsResponse,
    RunListResponse,
    RunSummary,
)
from ..storage.run_repository import RunNotFoundError, RunRepository
from ..storage.sqlite_models import Artifact, JobEvent, JobStep, Run

router = APIRouter(tags=["runs"])


def run_summary(run: Run) -> RunSummary:
    return RunSummary(
        id=run.id,
        kind=run.kind,
        status=run.status,
        pick_date=run.pick_date,
        started_at=run.started_at,
        finished_at=run.finished_at,
        summary=run.summary_json,
        created_at=run.created_at,
    )


def step_response(step: JobStep) -> JobStepResponse:
    return JobStepResponse(
        id=step.id,
        run_id=step.run_id,
        name=step.name,
        status=step.status,
        started_at=step.started_at,
        finished_at=step.finished_at,
        error=step.error_json,
        created_at=step.created_at,
    )


def event_response(event: JobEvent) -> JobEventResponse:
    return JobEventResponse(
        id=event.id,
        run_id=event.run_id,
        step_id=event.step_id,
        level=event.level,
        message=event.message,
        created_at=event.created_at,
    )


def artifact_response(artifact: Artifact) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.id,
        run_id=artifact.run_id,
        kind=artifact.kind,
        path=artifact.path,
        content_type=artifact.content_type,
        metadata=artifact.metadata_json,
        created_at=artifact.created_at,
    )


def run_detail(run: Run) -> RunDetail:
    summary = run_summary(run)
    return RunDetail(
        **summary.model_dump(),
        steps=[step_response(step) for step in run.steps],
        events=[event_response(event) for event in run.events],
        artifacts=[artifact_response(artifact) for artifact in run.artifacts],
    )


@router.post("/runs/diagnostic", response_model=RunDetail)
def create_diagnostic_run(
    request: DiagnosticRunRequest,
    runtime: JobRuntime = Depends(get_job_runtime),
    repository: RunRepository = Depends(get_run_repository),
) -> RunDetail:
    run = runtime.run_diagnostic_job(fail=request.fail)
    return run_detail(repository.get_run_detail(run.id))


@router.get("/runs", response_model=RunListResponse)
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    repository: RunRepository = Depends(get_run_repository),
) -> RunListResponse:
    return RunListResponse(runs=[run_summary(run) for run in repository.list_runs(limit=limit)])


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, repository: RunRepository = Depends(get_run_repository)) -> RunDetail:
    try:
        return run_detail(repository.get_run_detail(run_id))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/cancel", response_model=RunSummary)
def cancel_run(run_id: str, runtime: JobRuntime = Depends(get_job_runtime)) -> RunSummary:
    try:
        return run_summary(runtime.request_cancellation(run_id))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.get("/jobs/{run_id}/events", response_model=RunEventsResponse)
def list_run_events(run_id: str, repository: RunRepository = Depends(get_run_repository)) -> RunEventsResponse:
    try:
        repository.get_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return RunEventsResponse(events=[event_response(event) for event in repository.list_events(run_id)])


@router.get("/runs/{run_id}/artifacts", response_model=RunArtifactsResponse)
def list_run_artifacts(run_id: str, repository: RunRepository = Depends(get_run_repository)) -> RunArtifactsResponse:
    try:
        repository.get_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return RunArtifactsResponse(artifacts=[artifact_response(artifact) for artifact in repository.list_artifacts(run_id)])
