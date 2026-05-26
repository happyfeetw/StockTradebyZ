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
