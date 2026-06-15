from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import (
    get_archive_repository,
    get_analytics_writer,
    get_artifact_root,
    get_candidate_repository,
    get_job_runtime,
    get_market_data_service,
    get_preselect_service,
    get_review_repository,
    get_review_provider_executor,
    get_run_repository,
)
from ..jobs.runtime import JobRuntime
from ..routes.archive import archive_row_response, archive_snapshot_response
from ..routes.reviews import recommendation_response, review_response, review_run_response
from ..schemas.archive import ArchiveRunCreateRequest, ArchiveRunCreateResponse
from ..schemas.charts import ChartExportRunCreateRequest, ChartExportRunCreateResponse
from ..schemas.market_data import MarketDataRunRequest, MarketDataRunResponse
from ..schemas.preselect import (
    CandidateBatchResponse,
    CandidateResponse,
    PreselectRunRequest,
    PreselectRunResponse,
)
from ..schemas.reviews import ReviewProviderRunCreateRequest, ReviewRunCreateRequest, ReviewRunCreateResponse
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
from ..services.cancellation import WorkflowCancellationRequested
from ..storage.archive_repository import ArchiveRepository, ArchiveSourceNotFoundError
from ..storage.candidate_repository import CandidateBatchNotFoundError as CandidateSelectionBatchNotFoundError
from ..storage.candidate_repository import CandidateRepository
from ..storage.duckdb import DuckDBAnalyticsWriter
from ..storage.review_repository import CandidateBatchNotFoundError, ReviewRepository
from ..storage.run_repository import RunNotFoundError, RunRepository
from ..storage.sqlite_models import Artifact, Candidate, CandidateBatch, JobEvent, JobStep, Run

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


def candidate_response(candidate: Candidate) -> CandidateResponse:
    return CandidateResponse(
        id=candidate.id,
        batch_id=candidate.batch_id,
        code=candidate.code,
        date=candidate.pick_date,
        strategy=candidate.strategy,
        close=candidate.close,
        turnover_n=candidate.turnover_n,
        brick_growth=candidate.brick_growth,
        extra=candidate.extra_json or {},
    )


def candidate_batch_response(batch: CandidateBatch) -> CandidateBatchResponse:
    return CandidateBatchResponse(
        id=batch.id,
        run_id=batch.run_id,
        pick_date=batch.pick_date,
        source=batch.source,
        strategy_counts={str(key): int(value) for key, value in (batch.strategy_counts_json or {}).items()},
        total=len(batch.candidates),
        created_at=batch.created_at,
        candidates=[candidate_response(candidate) for candidate in batch.candidates],
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


@router.post("/runs/preselect", response_model=PreselectRunResponse)
def create_preselect_run(
    request: PreselectRunRequest,
    runtime: JobRuntime = Depends(get_job_runtime),
    service: Any = Depends(get_preselect_service),
    analytics_writer: DuckDBAnalyticsWriter | None = Depends(get_analytics_writer),
) -> PreselectRunResponse:
    from stocktrade.domain.selection import PreselectParameters

    parameters = PreselectParameters(
        config_path=request.config_path,
        data_dir=request.data_dir,
        pick_date=request.pick_date,
        end_date=request.end_date,
        strategy_ids=tuple(request.strategy_ids) if request.strategy_ids is not None else None,
    )
    try:
        run, batch, _result = runtime.run_preselect_job(
            parameters,
            service=service,
            analytics_writer=analytics_writer,
        )
    except WorkflowCancellationRequested as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PreselectRunResponse(run=run_summary(run), batch=candidate_batch_response(batch))


@router.post("/runs/market-data", response_model=MarketDataRunResponse)
def create_market_data_run(
    request: MarketDataRunRequest,
    runtime: JobRuntime = Depends(get_job_runtime),
    service: Any = Depends(get_market_data_service),
) -> MarketDataRunResponse:
    from ..services.market_data_runs import MarketDataDownloadError, MarketDataDownloadValidationError

    try:
        run, created = runtime.run_market_data_job(request, service=service)
    except WorkflowCancellationRequested as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MarketDataDownloadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MarketDataDownloadError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return MarketDataRunResponse(
        run=run_summary(run),
        summary=created.summary,
        artifacts=[artifact_response(artifact) for artifact in created.artifacts],
    )


@router.post("/runs/review", response_model=ReviewRunCreateResponse)
def create_review_run(
    request: ReviewRunCreateRequest,
    runtime: JobRuntime = Depends(get_job_runtime),
    review_repository: ReviewRepository = Depends(get_review_repository),
    analytics_writer: DuckDBAnalyticsWriter | None = Depends(get_analytics_writer),
) -> ReviewRunCreateResponse:
    from ..services.review_runs import ReviewRunService, ReviewRunValidationError

    service = ReviewRunService(review_repository, analytics_writer=analytics_writer)
    try:
        run, created = runtime.run_review_job(request, service=service)
    except WorkflowCancellationRequested as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CandidateBatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail="candidate batch not found") from exc
    except ReviewRunValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReviewRunCreateResponse(
        run=run_summary(run),
        review_run=review_run_response(created.review_run),
        reviews=[review_response(review) for review in created.reviews],
        recommendations=[
            recommendation_response(recommendation)
            for recommendation in created.recommendations
        ],
    )


@router.post("/runs/review/provider", response_model=ReviewRunCreateResponse)
def create_review_provider_run(
    request: ReviewProviderRunCreateRequest,
    runtime: JobRuntime = Depends(get_job_runtime),
    run_repository: RunRepository = Depends(get_run_repository),
    review_repository: ReviewRepository = Depends(get_review_repository),
    analytics_writer: DuckDBAnalyticsWriter | None = Depends(get_analytics_writer),
    artifact_root: Path = Depends(get_artifact_root),
    executor: Any | None = Depends(get_review_provider_executor),
) -> ReviewRunCreateResponse:
    from ..services.review_provider_runs import ReviewProviderRunService, ReviewProviderValidationError, UnconfiguredReviewProviderExecutor
    from ..services.review_runs import ReviewRunValidationError

    if executor is None:
        if request.provider == "gemini-cli":
            from ..services.gemini_cli_provider import GeminiCliReviewProviderExecutor

            executor = GeminiCliReviewProviderExecutor(artifact_root=artifact_root)
        else:
            executor = UnconfiguredReviewProviderExecutor()

    service = ReviewProviderRunService(
        review_repository,
        executor=executor,
        analytics_writer=analytics_writer,
        run_repository=run_repository,
        artifact_root=artifact_root,
    )
    try:
        run, created = runtime.run_review_job(request, service=service)
    except WorkflowCancellationRequested as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CandidateBatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail="candidate batch not found") from exc
    except (ReviewProviderValidationError, ReviewRunValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReviewRunCreateResponse(
        run=run_summary(run),
        review_run=review_run_response(created.review_run),
        reviews=[review_response(review) for review in created.reviews],
        recommendations=[
            recommendation_response(recommendation)
            for recommendation in created.recommendations
        ],
    )


@router.post("/runs/archive", response_model=ArchiveRunCreateResponse)
def create_archive_run(
    request: ArchiveRunCreateRequest,
    runtime: JobRuntime = Depends(get_job_runtime),
    archive_repository: ArchiveRepository = Depends(get_archive_repository),
    analytics_writer: DuckDBAnalyticsWriter | None = Depends(get_analytics_writer),
) -> ArchiveRunCreateResponse:
    from ..services.archive_runs import ArchiveRunService, ArchiveRunValidationError

    service = ArchiveRunService(archive_repository, analytics_writer=analytics_writer)
    try:
        run, created = runtime.run_archive_job(request, service=service)
    except WorkflowCancellationRequested as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ArchiveSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="archive source not found") from exc
    except ArchiveRunValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ArchiveRunCreateResponse(
        run=run_summary(run),
        snapshot=archive_snapshot_response(created.snapshot),
        rows=[archive_row_response(row) for row in created.rows],
    )


@router.post("/runs/chart-export", response_model=ChartExportRunCreateResponse)
def create_chart_export_run(
    request: ChartExportRunCreateRequest,
    runtime: JobRuntime = Depends(get_job_runtime),
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
    run_repository: RunRepository = Depends(get_run_repository),
    artifact_root: Path = Depends(get_artifact_root),
) -> ChartExportRunCreateResponse:
    from ..services.chart_runs import ChartExportRunService, ChartExportValidationError

    service = ChartExportRunService(
        candidate_repository,
        run_repository,
        artifact_root=artifact_root,
    )
    try:
        run, created = runtime.run_chart_export_job(request, service=service)
    except WorkflowCancellationRequested as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CandidateSelectionBatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail="candidate batch not found") from exc
    except ChartExportValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChartExportRunCreateResponse(
        run=run_summary(run),
        artifacts=[artifact_response(artifact) for artifact in created.artifacts],
    )


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
