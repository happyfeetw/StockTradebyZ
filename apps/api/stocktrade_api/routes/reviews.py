from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_review_repository
from ..schemas.reviews import (
    RecommendationResponse,
    ReviewDetailResponse,
    ReviewListResponse,
    ReviewResponse,
    ReviewRunResponse,
)
from ..storage.review_repository import ReviewNotFoundError, ReviewRepository
from ..storage.sqlite_models import Recommendation, Review, ReviewRun

router = APIRouter(tags=["reviews"])


def review_run_response(review_run: ReviewRun) -> ReviewRunResponse:
    return ReviewRunResponse(
        id=review_run.id,
        run_id=review_run.run_id,
        candidate_batch_id=review_run.candidate_batch_id,
        pick_date=review_run.pick_date,
        provider=review_run.provider,
        status=review_run.status,
        summary=review_run.summary_json,
        created_at=review_run.created_at,
    )


def recommendation_response(recommendation: Recommendation) -> RecommendationResponse:
    return RecommendationResponse(
        id=recommendation.id,
        review_run_id=recommendation.review_run_id,
        review_id=recommendation.review_id,
        rank=recommendation.rank,
        code=recommendation.code,
        strategy=recommendation.strategy,
        review_key=recommendation.review_key,
        verdict=recommendation.verdict,
        total_score=recommendation.total_score,
        payload=recommendation.payload_json,
        created_at=recommendation.created_at,
    )


def review_response(review: Review) -> ReviewResponse:
    return ReviewResponse(
        id=review.id,
        review_run_id=review.review_run_id,
        run_id=review.review_run.run_id,
        candidate_batch_id=review.review_run.candidate_batch_id,
        candidate_id=review.candidate_id,
        pick_date=review.review_run.pick_date,
        code=review.code,
        strategy=review.strategy,
        review_key=review.review_key,
        verdict=review.verdict,
        total_score=review.total_score,
        reviewer=review.reviewer,
        payload=review.payload_json,
        created_at=review.created_at,
        review_run=review_run_response(review.review_run),
        recommendation=recommendation_response(review.recommendation) if review.recommendation else None,
    )


@router.get("/reviews", response_model=ReviewListResponse)
def list_reviews(
    pick_date: str | None = Query(default=None, min_length=10, max_length=10),
    run_id: str | None = Query(default=None, min_length=1, max_length=64),
    review_run_id: str | None = Query(default=None, min_length=1, max_length=64),
    candidate_batch_id: str | None = Query(default=None, min_length=1, max_length=64),
    strategy: str | None = Query(default=None, min_length=1, max_length=80),
    code: str | None = Query(default=None, min_length=1, max_length=16),
    review_key: str | None = Query(default=None, min_length=1, max_length=120),
    reviewer: str | None = Query(default=None, min_length=1, max_length=80),
    recommendation_status: Literal["all", "recommended", "reviewed"] = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=500),
    repository: ReviewRepository = Depends(get_review_repository),
) -> ReviewListResponse:
    reviews = repository.list_reviews(
        pick_date=pick_date,
        run_id=run_id,
        review_run_id=review_run_id,
        candidate_batch_id=candidate_batch_id,
        strategy=strategy,
        code=code,
        review_key=review_key,
        reviewer=reviewer,
        recommendation_status=recommendation_status,
        limit=limit,
    )
    return ReviewListResponse(reviews=[review_response(review) for review in reviews], total=len(reviews))


@router.get("/reviews/{review_id}", response_model=ReviewDetailResponse)
def get_review(
    review_id: int,
    repository: ReviewRepository = Depends(get_review_repository),
) -> ReviewDetailResponse:
    try:
        return ReviewDetailResponse(review=review_response(repository.get_review(review_id)))
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="review not found") from exc
