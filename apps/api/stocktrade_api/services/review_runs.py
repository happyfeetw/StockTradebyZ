from __future__ import annotations

from typing import Any

from stocktrade.domain.review import (
    candidate_review_key,
    generate_suggestion,
    normalize_scores,
    result_review_key,
)

from ..schemas.reviews import ReviewRunCreateRequest
from .cancellation import CancellationCheck, raise_if_cancelled
from ..storage.duckdb import DuckDBAnalyticsWriter
from ..storage.review_repository import CreatedReviewRun, ReviewRepository
from ..storage.sqlite_models import Candidate, CandidateBatch


class ReviewRunValidationError(ValueError):
    pass


class ReviewRunService:
    def __init__(
        self,
        repository: ReviewRepository,
        *,
        analytics_writer: DuckDBAnalyticsWriter | None = None,
    ) -> None:
        self.repository = repository
        self.analytics_writer = analytics_writer

    def run(
        self,
        *,
        run_id: str,
        request: ReviewRunCreateRequest,
        source: dict[str, Any] | None = None,
        should_cancel: CancellationCheck | None = None,
    ) -> CreatedReviewRun:
        batch = self.repository.get_candidate_batch(request.candidate_batch_id)
        raise_if_cancelled(should_cancel)
        candidates = [_candidate_payload(candidate) for candidate in batch.candidates]
        candidates_by_key = {candidate_review_key(candidate): candidate for candidate in candidates}
        normalized_results = self._normalize_results(
            request.results,
            candidates_by_key=candidates_by_key,
            classic_pattern_config=request.classic_pattern_config,
        )
        raise_if_cancelled(should_cancel)
        suggestion = generate_suggestion(
            pick_date=batch.pick_date,
            all_results=normalized_results,
            min_score=request.min_score,
            candidates=candidates,
        )
        raise_if_cancelled(should_cancel)
        summary = _summary_payload(
            batch=batch,
            provider=request.provider,
            reviewer=request.reviewer or request.provider,
            min_score=request.min_score,
            normalized_results=normalized_results,
            suggestion=suggestion,
            source=source,
        )
        created = self.repository.create_review_run(
            run_id=run_id,
            candidate_batch_id=batch.id,
            provider=request.provider,
            reviewer=request.reviewer or request.provider,
            summary=summary,
            results=normalized_results,
            recommendations=list(suggestion["recommendations"]),
        )
        raise_if_cancelled(should_cancel)
        if self.analytics_writer is not None:
            self.analytics_writer.record_review_import(
                run_id=run_id,
                review_run=created.review_run,
                reviews=created.reviews,
            )
        return created

    def _normalize_results(
        self,
        results: list[dict[str, Any]],
        *,
        candidates_by_key: dict[str, dict[str, Any]],
        classic_pattern_config: Any,
    ) -> list[dict[str, Any]]:
        normalized_results: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for raw_result in results:
            normalized = normalize_scores(raw_result, classic_pattern_config)
            review_key = result_review_key(normalized)
            if not review_key:
                raise ReviewRunValidationError("review result is missing code/strategy identity")
            if review_key in seen_keys:
                raise ReviewRunValidationError(f"duplicate review result for {review_key}")
            candidate = candidates_by_key.get(review_key)
            if candidate is None:
                raise ReviewRunValidationError(f"review result {review_key} is not in candidate batch")

            normalized["review_key"] = review_key
            normalized["code"] = candidate["code"]
            normalized["strategy"] = candidate["strategy"]
            normalized_results.append(normalized)
            seen_keys.add(review_key)
        return normalized_results


def _candidate_payload(candidate: Candidate) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": candidate.code,
        "strategy": candidate.strategy,
        "close": candidate.close,
        "turnover_n": candidate.turnover_n,
        "brick_growth": candidate.brick_growth,
    }
    if candidate.extra_json:
        payload.update(candidate.extra_json)
    return payload


def _summary_payload(
    *,
    batch: CandidateBatch,
    provider: str,
    reviewer: str,
    min_score: float,
    normalized_results: list[dict[str, Any]],
    suggestion: dict[str, Any],
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy_counts = suggestion.get("strategy_counts") or {}
    pending = sum(int(metrics.get("pending", 0)) for metrics in strategy_counts.values())
    source_payload = source or {}
    summary = {
        "mode": str(source_payload.get("mode") or "review_results"),
        "candidate_batch_id": batch.id,
        "pick_date": batch.pick_date,
        "provider": provider,
        "reviewer": reviewer,
        "min_score_threshold": min_score,
        "total_candidates": len(batch.candidates),
        "total_reviewed": len(normalized_results),
        "recommended": len(suggestion["recommendations"]),
        "excluded": len(suggestion["excluded"]),
        "pending": pending,
        "strategy_counts": strategy_counts,
        "suggestion": suggestion,
    }
    if source_payload:
        summary["source"] = source_payload
    return summary
