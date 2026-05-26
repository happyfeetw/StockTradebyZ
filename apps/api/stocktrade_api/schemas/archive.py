from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

ArchiveStatus = Literal["recommended", "reviewed", "unreviewed"]
ArchiveStatusFilter = Literal["all", "recommended", "reviewed", "unreviewed"]


class ArchiveSnapshotResponse(BaseModel):
    id: str
    pick_date: str
    run_id: str
    candidate_batch_id: str | None = None
    review_run_id: str | None = None
    source: str
    summary: dict[str, Any] | None = None
    created_at: datetime


class ArchiveRowResponse(BaseModel):
    id: int
    snapshot_id: str
    candidate_id: int | None = None
    review_id: int | None = None
    recommendation_id: int | None = None
    chart_artifact_id: str | None = None
    pick_date: str
    run_id: str
    code: str
    strategy: str
    review_key: str
    status: ArchiveStatus
    rank: int | None = None
    close: float | None = None
    turnover_n: float | None = None
    brick_growth: float | None = None
    extra: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    chart_path: str | None = None
    created_at: datetime
    snapshot: ArchiveSnapshotResponse


class ArchiveListResponse(BaseModel):
    archives: list[ArchiveSnapshotResponse]
    total: int


class ArchiveDetailResponse(BaseModel):
    snapshot: ArchiveSnapshotResponse
    rows: list[ArchiveRowResponse]
    total: int
