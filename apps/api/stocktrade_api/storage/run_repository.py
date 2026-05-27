from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .sqlite_models import Artifact, Candidate, CandidateBatch, JobEvent, JobStep, Run

if TYPE_CHECKING:
    from stocktrade.domain.selection import PreselectResult

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "cancelling"}


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunNotFoundError(LookupError):
    pass


class ArtifactNotFoundError(LookupError):
    pass


class RunRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create_run(
        self,
        *,
        kind: str,
        status: str = "queued",
        pick_date: str | None = None,
        summary: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        with self.session_factory() as session:
            run = Run(
                id=run_id or uuid4().hex,
                kind=kind,
                status=status,
                pick_date=pick_date,
                summary_json=summary,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def list_runs(self, *, limit: int = 50) -> list[Run]:
        with self.session_factory() as session:
            statement = select(Run).order_by(Run.created_at.desc()).limit(limit)
            return list(session.execute(statement).scalars())

    def get_run(self, run_id: str) -> Run:
        with self.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RunNotFoundError(run_id)
            return run

    def get_run_detail(self, run_id: str) -> Run:
        with self.session_factory() as session:
            statement = (
                select(Run)
                .where(Run.id == run_id)
                .options(
                    selectinload(Run.steps),
                    selectinload(Run.events),
                    selectinload(Run.artifacts),
                )
            )
            run = session.execute(statement).scalar_one_or_none()
            if run is None:
                raise RunNotFoundError(run_id)
            return run

    def transition_run(
        self,
        run_id: str,
        *,
        status: str,
        pick_date: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> Run:
        with self.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RunNotFoundError(run_id)
            run.status = status
            if status == "running" and run.started_at is None:
                run.started_at = utc_now()
            if status in TERMINAL_STATUSES:
                run.finished_at = utc_now()
            if pick_date is not None:
                run.pick_date = pick_date
            if summary is not None:
                run.summary_json = summary
            session.commit()
            session.refresh(run)
            return run

    def add_step(self, run_id: str, *, name: str, status: str = "queued") -> JobStep:
        with self.session_factory() as session:
            step = JobStep(run_id=run_id, name=name, status=status)
            session.add(step)
            session.commit()
            session.refresh(step)
            return step

    def transition_step(
        self,
        step_id: int,
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> JobStep:
        with self.session_factory() as session:
            step = session.get(JobStep, step_id)
            if step is None:
                raise LookupError(step_id)
            step.status = status
            if status == "running" and step.started_at is None:
                step.started_at = utc_now()
            if status in TERMINAL_STATUSES:
                step.finished_at = utc_now()
            if error is not None:
                step.error_json = error
            session.commit()
            session.refresh(step)
            return step

    def append_event(
        self,
        run_id: str,
        *,
        message: str,
        level: str = "info",
        step_id: int | None = None,
    ) -> JobEvent:
        with self.session_factory() as session:
            event = JobEvent(run_id=run_id, step_id=step_id, level=level, message=message)
            session.add(event)
            session.commit()
            session.refresh(event)
            return event

    def list_events(self, run_id: str) -> list[JobEvent]:
        with self.session_factory() as session:
            statement = select(JobEvent).where(JobEvent.run_id == run_id).order_by(JobEvent.created_at, JobEvent.id)
            return list(session.execute(statement).scalars())

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        with self.session_factory() as session:
            statement = select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at, Artifact.id)
            return list(session.execute(statement).scalars())

    def create_artifacts(self, artifacts: list[dict[str, Any]]) -> list[Artifact]:
        if not artifacts:
            return []
        with self.session_factory() as session:
            created = [Artifact(**artifact) for artifact in artifacts]
            session.add_all(created)
            session.commit()
            artifact_ids = [artifact.id for artifact in created]
            statement = select(Artifact).where(Artifact.id.in_(artifact_ids)).order_by(Artifact.created_at, Artifact.id)
            return list(session.execute(statement).scalars())

    def get_artifact(self, artifact_id: str) -> Artifact:
        with self.session_factory() as session:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None:
                raise ArtifactNotFoundError(artifact_id)
            return artifact

    def request_cancellation(self, run_id: str) -> Run:
        with self.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RunNotFoundError(run_id)
            if run.status in TERMINAL_STATUSES:
                return run
            run.status = "cancelling"
            event = JobEvent(run_id=run_id, level="warning", message="Cancellation requested")
            session.add(event)
            session.commit()
            session.refresh(run)
            return run

    def create_candidate_batch(
        self,
        *,
        run_id: str,
        result: "PreselectResult",
        source: str = "preselect",
    ) -> CandidateBatch:
        batch_id = uuid4().hex
        with self.session_factory() as session:
            batch = CandidateBatch(
                id=batch_id,
                run_id=run_id,
                pick_date=result.pick_date,
                source=source,
                strategy_counts_json=result.strategy_counts,
            )
            session.add(batch)
            for candidate in result.candidates:
                session.add(
                    Candidate(
                        batch_id=batch_id,
                        code=candidate.code,
                        strategy=candidate.strategy,
                        pick_date=result.pick_date,
                        close=candidate.close,
                        turnover_n=candidate.turnover_n,
                        brick_growth=candidate.brick_growth,
                        extra_json=candidate.extra,
                    )
                )
            session.commit()
            return self._load_candidate_batch(session, batch_id)

    def get_candidate_batch_detail(self, batch_id: str) -> CandidateBatch:
        with self.session_factory() as session:
            batch = self._load_candidate_batch(session, batch_id)
            if batch is None:
                raise LookupError(batch_id)
            return batch

    def _load_candidate_batch(self, session: Session, batch_id: str) -> CandidateBatch:
        statement = (
            select(CandidateBatch)
            .where(CandidateBatch.id == batch_id)
            .options(selectinload(CandidateBatch.candidates))
        )
        batch = session.execute(statement).scalar_one()
        return batch
