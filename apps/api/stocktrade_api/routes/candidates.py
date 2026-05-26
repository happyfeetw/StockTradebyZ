from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_candidate_repository
from ..schemas.candidates import (
    CandidateBatchResponse,
    CandidateDetailResponse,
    CandidateListResponse,
    CandidateResponse,
)
from ..storage.candidate_repository import CandidateNotFoundError, CandidateRepository
from ..storage.sqlite_models import Candidate, CandidateBatch

router = APIRouter(tags=["candidates"])


def batch_response(batch: CandidateBatch) -> CandidateBatchResponse:
    return CandidateBatchResponse(
        id=batch.id,
        run_id=batch.run_id,
        pick_date=batch.pick_date,
        source=batch.source,
        strategy_counts=batch.strategy_counts_json,
        created_at=batch.created_at,
    )


def candidate_response(candidate: Candidate) -> CandidateResponse:
    return CandidateResponse(
        id=candidate.id,
        batch_id=candidate.batch_id,
        run_id=candidate.batch.run_id,
        pick_date=candidate.pick_date,
        code=candidate.code,
        strategy=candidate.strategy,
        close=candidate.close,
        turnover_n=candidate.turnover_n,
        brick_growth=candidate.brick_growth,
        extra=candidate.extra_json,
        created_at=candidate.created_at,
        batch=batch_response(candidate.batch),
    )


@router.get("/candidates", response_model=CandidateListResponse)
def list_candidates(
    pick_date: str | None = Query(default=None, min_length=10, max_length=10),
    run_id: str | None = Query(default=None, min_length=1, max_length=64),
    strategy: str | None = Query(default=None, min_length=1, max_length=80),
    code: str | None = Query(default=None, min_length=1, max_length=16),
    limit: int = Query(default=100, ge=1, le=500),
    repository: CandidateRepository = Depends(get_candidate_repository),
) -> CandidateListResponse:
    candidates = repository.list_candidates(
        pick_date=pick_date,
        run_id=run_id,
        strategy=strategy,
        code=code,
        limit=limit,
    )
    return CandidateListResponse(
        candidates=[candidate_response(candidate) for candidate in candidates],
        total=len(candidates),
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateDetailResponse)
def get_candidate(
    candidate_id: int,
    repository: CandidateRepository = Depends(get_candidate_repository),
) -> CandidateDetailResponse:
    try:
        return CandidateDetailResponse(candidate=candidate_response(repository.get_candidate(candidate_id)))
    except CandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="candidate not found") from exc
