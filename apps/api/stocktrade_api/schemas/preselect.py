from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .runs import RunSummary


class PreselectRunRequest(BaseModel):
    config_path: str | None = None
    data_dir: str | None = None
    pick_date: str | None = None
    end_date: str | None = None


class CandidateResponse(BaseModel):
    id: int | None = None
    batch_id: str | None = None
    code: str
    date: str
    strategy: str
    close: float | None = None
    turnover_n: float | None = None
    brick_growth: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class CandidateBatchResponse(BaseModel):
    id: str
    run_id: str
    pick_date: str
    source: str
    strategy_counts: dict[str, int]
    total: int
    created_at: datetime
    candidates: list[CandidateResponse] = Field(default_factory=list)


class PreselectRunResponse(BaseModel):
    run: RunSummary
    batch: CandidateBatchResponse
