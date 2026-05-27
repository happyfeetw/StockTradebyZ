from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .sqlite_models import ArchiveSnapshot, Candidate, CandidateBatch, Recommendation, Review, ReviewRun


class CandidateNotFoundError(LookupError):
    pass


class CandidateBatchNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CandidateBatchSummary:
    batch: CandidateBatch
    candidate_count: int
    review_run_count: int
    latest_review_run_id: str | None
    latest_reviewed_count: int
    latest_recommended_count: int
    archive_snapshot_count: int


@dataclass(frozen=True)
class CandidateBatchDetail:
    summary: CandidateBatchSummary
    candidates: list[Candidate]


class CandidateRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def list_candidates(
        self,
        *,
        batch_id: str | None = None,
        pick_date: str | None = None,
        run_id: str | None = None,
        strategy: str | None = None,
        code: str | None = None,
        limit: int = 100,
    ) -> list[Candidate]:
        with self.session_factory() as session:
            statement = (
                select(Candidate)
                .join(CandidateBatch)
                .options(selectinload(Candidate.batch))
                .order_by(Candidate.pick_date.desc(), Candidate.strategy, Candidate.code, Candidate.id)
                .limit(limit)
            )
            if batch_id:
                statement = statement.where(Candidate.batch_id == batch_id)
            if pick_date:
                statement = statement.where(Candidate.pick_date == pick_date)
            if run_id:
                statement = statement.where(CandidateBatch.run_id == run_id)
            if strategy:
                statement = statement.where(Candidate.strategy == strategy)
            if code:
                statement = statement.where(Candidate.code == code)
            return list(session.execute(statement).scalars())

    def get_candidate(self, candidate_id: int) -> Candidate:
        with self.session_factory() as session:
            statement = (
                select(Candidate)
                .where(Candidate.id == candidate_id)
                .options(selectinload(Candidate.batch))
            )
            candidate = session.execute(statement).scalar_one_or_none()
            if candidate is None:
                raise CandidateNotFoundError(candidate_id)
            return candidate

    def list_candidate_batches(
        self,
        *,
        pick_date: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[CandidateBatchSummary]:
        with self.session_factory() as session:
            statement = (
                select(CandidateBatch)
                .order_by(CandidateBatch.pick_date.desc(), CandidateBatch.created_at.desc(), CandidateBatch.id)
                .limit(limit)
            )
            if pick_date:
                statement = statement.where(CandidateBatch.pick_date == pick_date)
            if run_id:
                statement = statement.where(CandidateBatch.run_id == run_id)

            batches = list(session.execute(statement).scalars())
            return self._batch_summaries(session, batches)

    def get_candidate_batch(self, batch_id: str) -> CandidateBatchDetail:
        with self.session_factory() as session:
            statement = select(CandidateBatch).where(CandidateBatch.id == batch_id)
            batch = session.execute(statement).scalar_one_or_none()
            if batch is None:
                raise CandidateBatchNotFoundError(batch_id)
            candidates = list(
                session.execute(
                    select(Candidate)
                    .where(Candidate.batch_id == batch.id)
                    .options(selectinload(Candidate.batch))
                    .order_by(Candidate.id)
                ).scalars()
            )
            return CandidateBatchDetail(
                summary=self._batch_summaries(session, [batch])[0],
                candidates=candidates,
            )

    def _batch_summaries(self, session: Session, batches: list[CandidateBatch]) -> list[CandidateBatchSummary]:
        batch_ids = [batch.id for batch in batches]
        if not batch_ids:
            return []

        candidate_counts = self._group_counts(session, Candidate.batch_id, Candidate.batch_id.in_(batch_ids))
        review_run_counts = self._group_counts(
            session,
            ReviewRun.candidate_batch_id,
            ReviewRun.candidate_batch_id.in_(batch_ids),
        )
        archive_snapshot_counts = self._group_counts(
            session,
            ArchiveSnapshot.candidate_batch_id,
            ArchiveSnapshot.candidate_batch_id.in_(batch_ids),
        )

        latest_review_runs: dict[str, ReviewRun] = {}
        review_runs = session.execute(
            select(ReviewRun)
            .where(ReviewRun.candidate_batch_id.in_(batch_ids))
            .order_by(ReviewRun.candidate_batch_id, ReviewRun.created_at.desc(), ReviewRun.id.desc())
        ).scalars()
        for review_run in review_runs:
            if review_run.candidate_batch_id and review_run.candidate_batch_id not in latest_review_runs:
                latest_review_runs[review_run.candidate_batch_id] = review_run

        latest_review_run_ids = [review_run.id for review_run in latest_review_runs.values()]
        reviewed_counts: dict[str, int] = {}
        recommended_counts: dict[str, int] = {}
        if latest_review_run_ids:
            reviewed_counts = self._group_counts(session, Review.review_run_id, Review.review_run_id.in_(latest_review_run_ids))
            recommended_counts = self._group_counts(
                session,
                Recommendation.review_run_id,
                Recommendation.review_run_id.in_(latest_review_run_ids),
            )

        summaries: list[CandidateBatchSummary] = []
        for batch in batches:
            latest_review_run = latest_review_runs.get(batch.id)
            latest_review_run_id = latest_review_run.id if latest_review_run else None
            summaries.append(
                CandidateBatchSummary(
                    batch=batch,
                    candidate_count=candidate_counts.get(batch.id, 0),
                    review_run_count=review_run_counts.get(batch.id, 0),
                    latest_review_run_id=latest_review_run_id,
                    latest_reviewed_count=reviewed_counts.get(latest_review_run_id, 0),
                    latest_recommended_count=recommended_counts.get(latest_review_run_id, 0),
                    archive_snapshot_count=archive_snapshot_counts.get(batch.id, 0),
                )
            )
        return summaries

    def _group_counts(self, session: Session, key: object, *where_clauses: object) -> dict[str, int]:
        statement = select(key, func.count()).where(*where_clauses).group_by(key)
        return {str(group_key): int(count) for group_key, count in session.execute(statement) if group_key}
