from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_archive_repository
from ..schemas.archive import (
    ArchiveDetailResponse,
    ArchiveListResponse,
    ArchiveRowResponse,
    ArchiveSnapshotResponse,
    ArchiveStatusFilter,
)
from ..storage.archive_repository import ArchiveRepository, ArchiveSnapshotNotFoundError
from ..storage.sqlite_models import ArchiveRow, ArchiveSnapshot

router = APIRouter(tags=["archive"])


def archive_snapshot_response(snapshot: ArchiveSnapshot) -> ArchiveSnapshotResponse:
    return ArchiveSnapshotResponse(
        id=snapshot.id,
        pick_date=snapshot.pick_date,
        run_id=snapshot.run_id,
        candidate_batch_id=snapshot.candidate_batch_id,
        review_run_id=snapshot.review_run_id,
        source=snapshot.source,
        summary=snapshot.summary_json,
        created_at=snapshot.created_at,
    )


def archive_row_response(row: ArchiveRow) -> ArchiveRowResponse:
    return ArchiveRowResponse(
        id=row.id,
        snapshot_id=row.snapshot_id,
        candidate_id=row.candidate_id,
        review_id=row.review_id,
        recommendation_id=row.recommendation_id,
        chart_artifact_id=row.chart_artifact_id,
        pick_date=row.pick_date,
        run_id=row.run_id,
        code=row.code,
        strategy=row.strategy,
        review_key=row.review_key,
        status=row.status,
        rank=row.rank,
        close=row.close,
        turnover_n=row.turnover_n,
        brick_growth=row.brick_growth,
        extra=row.extra_json,
        review=row.review_json,
        chart_path=row.chart_path,
        created_at=row.created_at,
        snapshot=archive_snapshot_response(row.snapshot),
    )


@router.get("/archive", response_model=ArchiveListResponse)
def list_archives(
    pick_date: str | None = Query(default=None, min_length=10, max_length=10),
    run_id: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    repository: ArchiveRepository = Depends(get_archive_repository),
) -> ArchiveListResponse:
    archives = repository.list_snapshots(pick_date=pick_date, run_id=run_id, limit=limit)
    return ArchiveListResponse(archives=[archive_snapshot_response(snapshot) for snapshot in archives], total=len(archives))


@router.get("/archive/{pick_date}", response_model=ArchiveDetailResponse)
def get_archive(
    pick_date: str,
    run_id: str | None = Query(default=None, min_length=1, max_length=64),
    strategy: str | None = Query(default=None, min_length=1, max_length=80),
    status: ArchiveStatusFilter = Query(default="all"),
    code: str | None = Query(default=None, min_length=1, max_length=16),
    limit: int = Query(default=500, ge=1, le=1000),
    repository: ArchiveRepository = Depends(get_archive_repository),
) -> ArchiveDetailResponse:
    try:
        snapshot = repository.get_snapshot(pick_date=pick_date, run_id=run_id)
    except ArchiveSnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail="archive snapshot not found") from exc
    rows = repository.list_rows(
        pick_date=pick_date,
        run_id=run_id,
        snapshot_id=snapshot.id,
        strategy=strategy,
        status=status,
        code=code,
        limit=limit,
    )
    return ArchiveDetailResponse(
        snapshot=archive_snapshot_response(snapshot),
        rows=[archive_row_response(row) for row in rows],
        total=len(rows),
    )
