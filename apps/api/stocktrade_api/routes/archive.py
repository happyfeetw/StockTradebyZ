from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_archive_repository
from ..schemas.archive import (
    ArchiveDateResponse,
    ArchiveRowDetailResponse,
    ArchiveRowResponse,
    ArchiveSnapshotListResponse,
    ArchiveSnapshotResponse,
)
from ..storage.archive_repository import ArchiveRepository, ArchiveRowNotFoundError
from ..storage.sqlite_models import ArchiveRow, ArchiveSnapshot

router = APIRouter(tags=["archive"])


def archive_snapshot_response(snapshot: ArchiveSnapshot) -> ArchiveSnapshotResponse:
    return ArchiveSnapshotResponse(
        id=snapshot.id,
        pick_date=snapshot.pick_date,
        run_id=snapshot.run_id,
        candidate_batch_id=snapshot.candidate_batch_id,
        review_run_id=snapshot.review_run_id,
        candidate_run_date=snapshot.candidate_run_date,
        candidate_count=snapshot.candidate_count,
        reviewed_count=snapshot.reviewed_count,
        recommended_count=snapshot.recommended_count,
        strategy_counts=snapshot.strategy_counts_json,
        executed_strategies=snapshot.executed_strategies_json,
        min_score_threshold=snapshot.min_score_threshold,
        source=snapshot.source_json,
        summary=snapshot.summary_json,
        archived_at=snapshot.archived_at,
        created_at=snapshot.created_at,
    )


def archive_row_response(row: ArchiveRow) -> ArchiveRowResponse:
    snapshot = archive_snapshot_response(row.snapshot)
    recommendation_score = row.recommendation.total_score if row.recommendation else None
    review_score = row.review.total_score if row.review else None
    return ArchiveRowResponse(
        id=row.id,
        snapshot_id=row.snapshot_id,
        pick_date=row.pick_date,
        run_id=row.run_id,
        candidate_batch_id=snapshot.candidate_batch_id,
        review_run_id=snapshot.review_run_id,
        candidate_id=row.candidate_id,
        review_id=row.review_id,
        recommendation_id=row.recommendation_id,
        chart_artifact_id=row.chart_artifact_id,
        code=row.code,
        strategy=row.strategy,
        review_key=row.review_key,
        status=row.status,
        rank=row.rank,
        total_score=recommendation_score if recommendation_score is not None else review_score,
        close=row.close,
        turnover_n=row.turnover_n,
        brick_growth=row.brick_growth,
        extra=row.extra_json,
        review_payload=row.review_payload_json,
        chart=row.chart_path,
        created_at=row.created_at,
        snapshot=snapshot,
    )


@router.get("/archive", response_model=ArchiveSnapshotListResponse)
def list_archive_snapshots(
    pick_date: str | None = Query(default=None, min_length=10, max_length=10),
    run_id: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    repository: ArchiveRepository = Depends(get_archive_repository),
) -> ArchiveSnapshotListResponse:
    snapshots = repository.list_snapshots(pick_date=pick_date, run_id=run_id, limit=limit)
    return ArchiveSnapshotListResponse(
        snapshots=[archive_snapshot_response(snapshot) for snapshot in snapshots],
        total=len(snapshots),
    )


@router.get("/archive/rows/{row_id}", response_model=ArchiveRowDetailResponse)
def get_archive_row(
    row_id: int,
    repository: ArchiveRepository = Depends(get_archive_repository),
) -> ArchiveRowDetailResponse:
    try:
        return ArchiveRowDetailResponse(row=archive_row_response(repository.get_row(row_id)))
    except ArchiveRowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="archive row not found") from exc


@router.get("/archive/{pick_date}", response_model=ArchiveDateResponse)
def list_archive_rows_for_date(
    pick_date: str,
    run_id: str | None = Query(default=None, min_length=1, max_length=64),
    strategy: str | None = Query(default=None, min_length=1, max_length=80),
    code: str | None = Query(default=None, min_length=1, max_length=16),
    review_key: str | None = Query(default=None, min_length=1, max_length=120),
    status: Literal["all", "recommended", "reviewed", "unreviewed"] = Query(default="all"),
    rank: int | None = Query(default=None, ge=1),
    min_score: float | None = Query(default=None, ge=0),
    max_score: float | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    repository: ArchiveRepository = Depends(get_archive_repository),
) -> ArchiveDateResponse:
    snapshots = repository.list_snapshots(pick_date=pick_date, run_id=run_id, limit=limit)
    if not snapshots:
        raise HTTPException(status_code=404, detail="archive date not found")

    rows = repository.list_rows(
        pick_date=pick_date,
        run_id=run_id,
        strategy=strategy,
        code=code,
        review_key=review_key,
        status=status,
        rank=rank,
        min_score=min_score,
        max_score=max_score,
        limit=limit,
    )
    return ArchiveDateResponse(
        snapshots=[archive_snapshot_response(snapshot) for snapshot in snapshots],
        rows=[archive_row_response(row) for row in rows],
        total=len(rows),
    )
