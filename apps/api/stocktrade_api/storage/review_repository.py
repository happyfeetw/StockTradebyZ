from __future__ import annotations

import re

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .sqlite_models import Recommendation, Review, ReviewRun

RECOMMENDATION_STATUSES = {"all", "recommended", "reviewed"}


class ReviewNotFoundError(LookupError):
    pass


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
