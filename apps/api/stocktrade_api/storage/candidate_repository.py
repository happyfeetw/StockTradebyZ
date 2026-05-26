from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .sqlite_models import Candidate, CandidateBatch


class CandidateNotFoundError(LookupError):
    pass


class CandidateRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def list_candidates(
        self,
        *,
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
