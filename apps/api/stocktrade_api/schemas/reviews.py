from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .runs import RunSummary


class ReviewRunResponse(BaseModel):
    id: str
    run_id: str
    candidate_batch_id: str | None = None
    pick_date: str
    provider: str
    status: str
    summary: dict[str, Any] | None = None
    created_at: datetime


class RecommendationResponse(BaseModel):
    id: int
    review_run_id: str
    review_id: int | None = None
    rank: int
    code: str
    strategy: str
    review_key: str
    verdict: str | None = None
    total_score: float | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime


class ReviewResponse(BaseModel):
    id: int
    review_run_id: str
    run_id: str
    candidate_batch_id: str | None = None
    candidate_id: int | None = None
    pick_date: str
    code: str
    strategy: str
    review_key: str
    verdict: str | None = None
    total_score: float | None = None
    reviewer: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime
    review_run: ReviewRunResponse
    recommendation: RecommendationResponse | None = None


class ReviewListResponse(BaseModel):
    reviews: list[ReviewResponse]
    total: int


class ReviewDetailResponse(BaseModel):
    review: ReviewResponse


class ReviewRunCreateRequest(BaseModel):
    candidate_batch_id: str = Field(min_length=1, max_length=64)
    provider: str = Field(default="manual", min_length=1, max_length=80)
    reviewer: str | None = Field(default=None, min_length=1, max_length=80)
    min_score: float = Field(default=4.0, ge=0, le=5)
    classic_pattern_config: Any = None
    results: list[dict[str, Any]] = Field(min_length=1)


class ReviewRunCreateResponse(BaseModel):
    run: RunSummary
    review_run: ReviewRunResponse
    reviews: list[ReviewResponse]
    recommendations: list[RecommendationResponse]
