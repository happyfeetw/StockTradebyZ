from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .sqlite_models import Artifact, CandidateBatch, Recommendation, Review, ReviewRun

RECOMMENDATION_STATUSES = {"all", "recommended", "reviewed"}


class ReviewNotFoundError(LookupError):
    pass


class CandidateBatchNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CreatedReviewRun:
    review_run: ReviewRun
    reviews: list[Review]
    recommendations: list[Recommendation]


@dataclass(frozen=True)
class ReviewProviderSources:
    candidate_batch: CandidateBatch
    chart_artifacts_by_review_key: dict[str, Artifact]
    chart_artifacts_by_code: dict[str, Artifact]


def review_key_for(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


class ReviewRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def list_reviews(
        self,
        *,
        pick_date: str | None = None,
        run_id: str | None = None,
        review_run_id: str | None = None,
        candidate_batch_id: str | None = None,
        strategy: str | None = None,
        code: str | None = None,
        review_key: str | None = None,
        reviewer: str | None = None,
        recommendation_status: str = "all",
        limit: int = 100,
    ) -> list[Review]:
        if recommendation_status not in RECOMMENDATION_STATUSES:
            raise ValueError(f"Unsupported recommendation_status: {recommendation_status}")

        with self.session_factory() as session:
            statement = (
                select(Review)
                .join(ReviewRun)
                .outerjoin(Recommendation, Recommendation.review_id == Review.id)
                .options(
                    selectinload(Review.review_run),
                    selectinload(Review.recommendation),
                )
                .order_by(
                    ReviewRun.pick_date.desc(),
                    case((Recommendation.rank.is_(None), 1), else_=0),
                    Recommendation.rank,
                    Review.total_score.desc(),
                    Review.code,
                    Review.strategy,
                    Review.id,
                )
                .limit(limit)
            )
            if pick_date:
                statement = statement.where(ReviewRun.pick_date == pick_date)
            if run_id:
                statement = statement.where(ReviewRun.run_id == run_id)
            if review_run_id:
                statement = statement.where(Review.review_run_id == review_run_id)
            if candidate_batch_id:
                statement = statement.where(ReviewRun.candidate_batch_id == candidate_batch_id)
            if strategy:
                statement = statement.where(Review.strategy == strategy)
            if code:
                statement = statement.where(Review.code == code)
            if review_key:
                statement = statement.where(Review.review_key == review_key)
            if code and strategy:
                statement = statement.where(Review.review_key == review_key_for(code, strategy))
            if reviewer:
                statement = statement.where(or_(Review.reviewer == reviewer, ReviewRun.provider == reviewer))
            if recommendation_status == "recommended":
                statement = statement.where(Recommendation.id.is_not(None))
            elif recommendation_status == "reviewed":
                statement = statement.where(Recommendation.id.is_(None))

            return list(session.execute(statement).scalars())

    def get_review(self, review_id: int) -> Review:
        with self.session_factory() as session:
            statement = (
                select(Review)
                .where(Review.id == review_id)
                .options(
                    selectinload(Review.review_run),
                    selectinload(Review.recommendation),
                )
            )
            review = session.execute(statement).scalar_one_or_none()
            if review is None:
                raise ReviewNotFoundError(review_id)
            return review

    def get_candidate_batch(self, batch_id: str) -> CandidateBatch:
        with self.session_factory() as session:
            batch = self._load_candidate_batch(session, batch_id)
            if batch is None:
                raise CandidateBatchNotFoundError(batch_id)
            return batch

    def get_review_provider_sources(self, batch_id: str) -> ReviewProviderSources:
        with self.session_factory() as session:
            batch = self._load_candidate_batch(session, batch_id)
            if batch is None:
                raise CandidateBatchNotFoundError(batch_id)
            chart_artifacts_by_review_key, chart_artifacts_by_code = self._chart_artifacts(session, batch_id)
            return ReviewProviderSources(
                candidate_batch=batch,
                chart_artifacts_by_review_key=chart_artifacts_by_review_key,
                chart_artifacts_by_code=chart_artifacts_by_code,
            )

    def create_review_run(
        self,
        *,
        run_id: str,
        candidate_batch_id: str,
        provider: str,
        reviewer: str,
        summary: dict[str, Any],
        results: list[dict[str, Any]],
        recommendations: list[dict[str, Any]],
    ) -> CreatedReviewRun:
        review_run_id = uuid4().hex
        with self.session_factory() as session:
            batch = self._load_candidate_batch(session, candidate_batch_id)
            if batch is None:
                raise CandidateBatchNotFoundError(candidate_batch_id)

            review_run = ReviewRun(
                id=review_run_id,
                run_id=run_id,
                candidate_batch_id=batch.id,
                pick_date=batch.pick_date,
                provider=provider,
                status="succeeded",
                summary_json=summary,
            )
            session.add(review_run)
            candidates_by_key = {
                review_key_for(candidate.code, candidate.strategy): candidate
                for candidate in batch.candidates
            }
            reviews_by_key: dict[str, Review] = {}
            for result in results:
                review_key = str(result["review_key"])
                candidate = candidates_by_key.get(review_key)
                review = Review(
                    review_run_id=review_run_id,
                    candidate_id=candidate.id if candidate else None,
                    code=str(result["code"]),
                    strategy=str(result.get("strategy") or ""),
                    review_key=review_key,
                    verdict=result.get("verdict"),
                    total_score=_float_or_none(result.get("total_score")),
                    reviewer=str(result.get("reviewer") or reviewer),
                    payload_json=result,
                )
                session.add(review)
                reviews_by_key[review_key] = review

            session.flush()
            for item in recommendations:
                review_key = str(item["review_key"])
                review = reviews_by_key.get(review_key)
                session.add(
                    Recommendation(
                        review_run_id=review_run_id,
                        review_id=review.id if review else None,
                        rank=int(item["rank"]),
                        code=str(item["code"]),
                        strategy=str(item.get("strategy") or ""),
                        review_key=review_key,
                        verdict=item.get("verdict"),
                        total_score=_float_or_none(item.get("total_score")),
                        payload_json=item,
                    )
                )

            session.commit()
            return self._load_created_review_run(session, review_run_id)

    def _load_candidate_batch(self, session: Session, batch_id: str) -> CandidateBatch | None:
        statement = (
            select(CandidateBatch)
            .where(CandidateBatch.id == batch_id)
            .options(selectinload(CandidateBatch.candidates))
        )
        return session.execute(statement).scalar_one_or_none()

    def _load_created_review_run(self, session: Session, review_run_id: str) -> CreatedReviewRun:
        review_run = session.execute(
            select(ReviewRun)
            .where(ReviewRun.id == review_run_id)
            .options(selectinload(ReviewRun.recommendations))
        ).scalar_one()
        reviews = list(
            session.execute(
                select(Review)
                .where(Review.review_run_id == review_run_id)
                .options(
                    selectinload(Review.review_run),
                    selectinload(Review.recommendation),
                )
                .order_by(Review.id)
            ).scalars()
        )
        recommendations = list(
            session.execute(
                select(Recommendation)
                .where(Recommendation.review_run_id == review_run_id)
                .order_by(Recommendation.rank, Recommendation.id)
            ).scalars()
        )
        return CreatedReviewRun(review_run=review_run, reviews=reviews, recommendations=recommendations)

    def _chart_artifacts(
        self,
        session: Session,
        candidate_batch_id: str,
    ) -> tuple[dict[str, Artifact], dict[str, Artifact]]:
        artifacts = session.execute(
            select(Artifact)
            .where(Artifact.kind == "chart")
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        ).scalars()
        by_review_key: dict[str, Artifact] = {}
        by_code: dict[str, Artifact] = {}
        for artifact in artifacts:
            metadata = artifact.metadata_json or {}
            if metadata.get("source") != "product:chart_export":
                continue
            if metadata.get("candidate_batch_id") != candidate_batch_id:
                continue
            review_key = str(metadata.get("review_key") or "")
            if review_key and review_key not in by_review_key:
                by_review_key[review_key] = artifact
            if metadata.get("artifact_scope") == "strategy":
                continue
            code = str(metadata.get("code") or "")
            if code and code not in by_code:
                by_code[code] = artifact
        return by_review_key, by_code


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
