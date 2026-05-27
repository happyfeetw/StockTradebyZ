from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from stocktrade.domain.review import candidate_review_key, result_review_key

from ..schemas.reviews import ReviewProviderRunCreateRequest, ReviewRunCreateRequest
from ..storage.duckdb import DuckDBAnalyticsWriter
from ..storage.review_repository import CreatedReviewRun, ReviewRepository
from ..storage.sqlite_models import Artifact, Candidate, CandidateBatch
from .review_runs import ReviewRunService


class ReviewProviderValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewProviderItem:
    candidate_id: int
    code: str
    strategy: str
    review_key: str
    candidate: dict[str, Any]
    chart_artifact_id: str | None
    chart_path: str | None


@dataclass(frozen=True)
class ReviewProviderInput:
    candidate_batch_id: str
    pick_date: str
    provider: str
    reviewer: str
    items: list[ReviewProviderItem]
    provider_config: dict[str, Any]


class ReviewProviderExecutor(Protocol):
    def run(self, request: ReviewProviderInput) -> list[dict[str, Any]]:
        ...


class UnconfiguredReviewProviderExecutor:
    def run(self, request: ReviewProviderInput) -> list[dict[str, Any]]:
        raise ReviewProviderValidationError(f"review provider executor is not configured: {request.provider}")


class ReviewProviderRunService:
    def __init__(
        self,
        repository: ReviewRepository,
        *,
        executor: ReviewProviderExecutor,
        analytics_writer: DuckDBAnalyticsWriter | None = None,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self.review_service = ReviewRunService(repository, analytics_writer=analytics_writer)

    def run(self, *, run_id: str, request: ReviewProviderRunCreateRequest) -> CreatedReviewRun:
        sources = self.repository.get_review_provider_sources(request.candidate_batch_id)
        items = _provider_items(
            sources.candidate_batch,
            chart_artifacts_by_review_key=sources.chart_artifacts_by_review_key,
            chart_artifacts_by_code=sources.chart_artifacts_by_code,
            codes=request.codes,
            strategies=request.strategies,
            require_charts=request.require_charts,
        )
        if not items:
            raise ReviewProviderValidationError("candidate filters selected no provider review items")

        reviewer = request.reviewer or request.provider
        provider_input = ReviewProviderInput(
            candidate_batch_id=sources.candidate_batch.id,
            pick_date=sources.candidate_batch.pick_date,
            provider=request.provider,
            reviewer=reviewer,
            items=items,
            provider_config=request.provider_config,
        )
        raw_results = self.executor.run(provider_input)
        results = _results_with_lineage(raw_results, items=items, reviewer=reviewer)
        source = {
            "mode": "review_provider",
            "provider": request.provider,
            "candidate_batch_id": sources.candidate_batch.id,
            "pick_date": sources.candidate_batch.pick_date,
            "selected_candidates": len(items),
            "chart_artifact_count": sum(1 for item in items if item.chart_artifact_id),
            "require_charts": request.require_charts,
            "filters": {
                "codes": list(request.codes or []),
                "strategies": list(request.strategies or []),
            },
        }
        review_request = ReviewRunCreateRequest(
            candidate_batch_id=sources.candidate_batch.id,
            provider=request.provider,
            reviewer=reviewer,
            min_score=request.min_score,
            classic_pattern_config=request.classic_pattern_config,
            results=results,
        )
        return self.review_service.run(run_id=run_id, request=review_request, source=source)


def _provider_items(
    batch: CandidateBatch,
    *,
    chart_artifacts_by_review_key: dict[str, Artifact],
    chart_artifacts_by_code: dict[str, Artifact],
    codes: list[str] | None,
    strategies: list[str] | None,
    require_charts: bool,
) -> list[ReviewProviderItem]:
    code_filter = {str(code).strip() for code in (codes or []) if str(code).strip()}
    strategy_filter = {str(strategy).strip() for strategy in (strategies or []) if str(strategy).strip()}
    items: list[ReviewProviderItem] = []
    missing_charts: list[str] = []
    for candidate in batch.candidates:
        if code_filter and candidate.code not in code_filter:
            continue
        if strategy_filter and candidate.strategy not in strategy_filter:
            continue
        payload = _candidate_payload(candidate)
        review_key = candidate_review_key(payload)
        chart_artifact = chart_artifacts_by_review_key.get(review_key) or chart_artifacts_by_code.get(candidate.code)
        if require_charts and chart_artifact is None:
            missing_charts.append(review_key)
        items.append(
            ReviewProviderItem(
                candidate_id=candidate.id,
                code=candidate.code,
                strategy=candidate.strategy,
                review_key=review_key,
                candidate=payload,
                chart_artifact_id=chart_artifact.id if chart_artifact else None,
                chart_path=chart_artifact.path if chart_artifact else None,
            )
        )
    if missing_charts:
        raise ReviewProviderValidationError(
            "missing chart artifacts for provider review items: " + ", ".join(sorted(missing_charts))
        )
    return items


def _results_with_lineage(
    raw_results: list[dict[str, Any]],
    *,
    items: list[ReviewProviderItem],
    reviewer: str,
) -> list[dict[str, Any]]:
    expected = {item.review_key: item for item in items}
    seen: dict[str, dict[str, Any]] = {}
    for raw_result in raw_results:
        result = dict(raw_result)
        review_key = result_review_key(result)
        if not review_key:
            raise ReviewProviderValidationError("provider result is missing code/strategy identity")
        if review_key in seen:
            raise ReviewProviderValidationError(f"duplicate provider result for {review_key}")
        seen[review_key] = result

    missing = sorted(set(expected) - set(seen))
    extra = sorted(set(seen) - set(expected))
    if missing:
        raise ReviewProviderValidationError("provider result missing review items: " + ", ".join(missing))
    if extra:
        raise ReviewProviderValidationError("provider returned items outside selected batch: " + ", ".join(extra))

    results: list[dict[str, Any]] = []
    for item in items:
        result = seen[item.review_key]
        result["code"] = item.code
        result["strategy"] = item.strategy
        result["review_key"] = item.review_key
        result["reviewer"] = str(result.get("reviewer") or reviewer)
        result["chart_artifact_id"] = item.chart_artifact_id
        result["chart_path"] = item.chart_path
        result["provider_source"] = {
            "candidate_batch_id": item.candidate.get("batch_id"),
            "candidate_id": item.candidate_id,
            "chart_artifact_id": item.chart_artifact_id,
            "chart_path": item.chart_path,
        }
        results.append(result)
    return results


def _candidate_payload(candidate: Candidate) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "batch_id": candidate.batch_id,
        "candidate_id": candidate.id,
        "code": candidate.code,
        "strategy": candidate.strategy,
        "close": candidate.close,
        "turnover_n": candidate.turnover_n,
        "brick_growth": candidate.brick_growth,
    }
    if candidate.extra_json:
        payload.update(candidate.extra_json)
    return payload
