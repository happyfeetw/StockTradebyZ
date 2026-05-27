from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CandidateBatchResponse(BaseModel):
    id: str
    run_id: str
    pick_date: str
    source: str
    strategy_counts: dict[str, Any] | None = None
    created_at: datetime


class CandidateBatchSummaryResponse(CandidateBatchResponse):
    candidate_count: int
    review_run_count: int
    latest_review_run_id: str | None = None
    latest_reviewed_count: int
    latest_recommended_count: int
    archive_snapshot_count: int


class CandidateResponse(BaseModel):
    id: int
    batch_id: str
    run_id: str
    pick_date: str
    code: str
    strategy: str
    close: float | None = None
    turnover_n: float | None = None
    brick_growth: float | None = None
    extra: dict[str, Any] | None = None
    created_at: datetime
    batch: CandidateBatchResponse


class CandidateListResponse(BaseModel):
    candidates: list[CandidateResponse]
    total: int


class CandidateDetailResponse(BaseModel):
    candidate: CandidateResponse


class CandidateBatchListResponse(BaseModel):
    batches: list[CandidateBatchSummaryResponse]
    total: int


class CandidateBatchDetailResponse(BaseModel):
    batch: CandidateBatchSummaryResponse
    candidates: list[CandidateResponse]
    total: int
