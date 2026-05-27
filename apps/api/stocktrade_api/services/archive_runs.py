from __future__ import annotations

from typing import Any

from ..schemas.archive import ArchiveRunCreateRequest
from ..storage.archive_repository import ArchiveRepository, CreatedArchive, archive_review_key_for
from ..storage.duckdb import DuckDBAnalyticsWriter
from ..storage.sqlite_models import Artifact, Candidate, CandidateBatch, Recommendation, Review, ReviewRun


class ArchiveRunValidationError(ValueError):
    pass


class ArchiveRunService:
    def __init__(
        self,
        repository: ArchiveRepository,
        *,
        analytics_writer: DuckDBAnalyticsWriter | None = None,
    ) -> None:
        self.repository = repository
        self.analytics_writer = analytics_writer

    def run(self, *, run_id: str, request: ArchiveRunCreateRequest) -> CreatedArchive:
        sources = self.repository.get_archive_sources(
            candidate_batch_id=request.candidate_batch_id,
            review_run_id=request.review_run_id,
        )
        batch = sources.candidate_batch
        review_run = sources.review_run
        if review_run.candidate_batch_id != batch.id:
            raise ArchiveRunValidationError("review run does not belong to candidate batch")
        if review_run.pick_date != batch.pick_date:
            raise ArchiveRunValidationError("review run date does not match candidate batch date")

        snapshot, rows = _build_archive_payload(
            batch=batch,
            review_run=review_run,
            chart_artifacts_by_code=sources.chart_artifacts_by_code,
        )
        created = self.repository.create_archive_snapshot(
            run_id=run_id,
            snapshot=snapshot,
            rows=rows,
        )
        if self.analytics_writer is not None:
            self.analytics_writer.record_archive_import(
                run_id=run_id,
                snapshot=created.snapshot,
                rows=created.rows,
            )
        return created


def _build_archive_payload(
    *,
    batch: CandidateBatch,
    review_run: ReviewRun,
    chart_artifacts_by_code: dict[str, Artifact] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reviews_by_key = {review.review_key: review for review in review_run.reviews}
    recommendations_by_key = {
        recommendation.review_key: recommendation for recommendation in review_run.recommendations
    }
    strategy_counts: dict[str, dict[str, int]] = {}
    rows: list[dict[str, Any]] = []

    for candidate in batch.candidates:
        review_key = archive_review_key_for(candidate.code, candidate.strategy)
        review = reviews_by_key.get(review_key)
        recommendation = recommendations_by_key.get(review_key)
        status = _archive_status(review, recommendation)
        counts = strategy_counts.setdefault(
            candidate.strategy,
            {"total": 0, "recommended": 0, "reviewed": 0, "unreviewed": 0},
        )
        counts["total"] += 1
        counts[status] += 1
        chart_artifact = (chart_artifacts_by_code or {}).get(candidate.code)
        rows.append(_row_payload(candidate, review_key, status, review, recommendation, chart_artifact))

    reviewed_count = sum(1 for row in rows if row["status"] != "unreviewed")
    recommended_count = sum(1 for row in rows if row["status"] == "recommended")
    min_score_threshold = _optional_float((review_run.summary_json or {}).get("min_score_threshold"))
    summary = {
        "mode": "archive",
        "candidate_batch_id": batch.id,
        "review_run_id": review_run.id,
        "pick_date": batch.pick_date,
        "candidate_count": len(rows),
        "reviewed_count": reviewed_count,
        "recommended_count": recommended_count,
        "chart_artifact_count": sum(1 for row in rows if row["chart_artifact_id"]),
        "strategy_counts": strategy_counts,
    }
    snapshot = {
        "candidate_batch_id": batch.id,
        "review_run_id": review_run.id,
        "pick_date": batch.pick_date,
        "candidate_run_date": batch.pick_date,
        "candidate_count": len(rows),
        "reviewed_count": reviewed_count,
        "recommended_count": recommended_count,
        "strategy_counts": strategy_counts,
        "executed_strategies": sorted(strategy_counts),
        "min_score_threshold": min_score_threshold,
        "source": {
            "kind": "product_review_batch",
            "candidate_batch_id": batch.id,
            "candidate_run_id": batch.run_id,
            "review_run_id": review_run.id,
            "review_workflow_run_id": review_run.run_id,
            "review_provider": review_run.provider,
        },
        "summary": summary,
    }
    return snapshot, rows


def _row_payload(
    candidate: Candidate,
    review_key: str,
    status: str,
    review: Review | None,
    recommendation: Recommendation | None,
    chart_artifact: Artifact | None,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate.id,
        "review_id": review.id if review else None,
        "recommendation_id": recommendation.id if recommendation else None,
        "chart_artifact_id": chart_artifact.id if chart_artifact else None,
        "code": candidate.code,
        "strategy": candidate.strategy,
        "review_key": review_key,
        "status": status,
        "rank": recommendation.rank if recommendation else None,
        "close": candidate.close,
        "turnover_n": candidate.turnover_n,
        "brick_growth": candidate.brick_growth,
        "extra": candidate.extra_json,
        "review_payload": review.payload_json if review else None,
        "chart": chart_artifact.path if chart_artifact else None,
    }


def _archive_status(review: Review | None, recommendation: Recommendation | None) -> str:
    if recommendation is not None:
        return "recommended"
    if review is not None:
        return "reviewed"
    return "unreviewed"


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
