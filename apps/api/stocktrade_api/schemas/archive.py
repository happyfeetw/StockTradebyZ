from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .runs import RunSummary


class ArchiveSnapshotResponse(BaseModel):
    id: str
    pick_date: str
    run_id: str
    candidate_batch_id: str | None = None
    review_run_id: str | None = None
    candidate_run_date: str | None = None
    candidate_count: int
    reviewed_count: int
    recommended_count: int
    strategy_counts: dict[str, Any] | None = None
    executed_strategies: list[str] | None = None
    min_score_threshold: float | None = None
    source: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    archived_at: datetime | None = None
    created_at: datetime


class ArchiveRowResponse(BaseModel):
    id: int
    snapshot_id: str
    pick_date: str
    run_id: str
    candidate_batch_id: str | None = None
    review_run_id: str | None = None
    candidate_id: int | None = None
    review_id: int | None = None
    recommendation_id: int | None = None
    chart_artifact_id: str | None = None
    code: str
    strategy: str
    review_key: str
    status: str
    rank: int | None = None
    total_score: float | None = None
    close: float | None = None
    turnover_n: float | None = None
    brick_growth: float | None = None
    extra: dict[str, Any] | None = None
    review_payload: dict[str, Any] | None = None
    chart: str | None = None
    created_at: datetime
    snapshot: ArchiveSnapshotResponse


class ArchiveSnapshotListResponse(BaseModel):
    snapshots: list[ArchiveSnapshotResponse]
    total: int


class ArchiveDateResponse(BaseModel):
    snapshots: list[ArchiveSnapshotResponse]
    rows: list[ArchiveRowResponse]
    total: int


class ArchiveRowDetailResponse(BaseModel):
    row: ArchiveRowResponse


class ArchiveRunCreateRequest(BaseModel):
    candidate_batch_id: str = Field(min_length=1, max_length=64)
    review_run_id: str = Field(min_length=1, max_length=64)


class ArchiveRunCreateResponse(BaseModel):
    run: RunSummary
    snapshot: ArchiveSnapshotResponse
    rows: list[ArchiveRowResponse]
