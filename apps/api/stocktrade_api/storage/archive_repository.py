from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import case, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .sqlite_models import ArchiveRow, ArchiveSnapshot, Artifact, CandidateBatch, Review, ReviewRun

ARCHIVE_QUERY_STATUSES = {"all", "recommended", "reviewed", "unreviewed"}


class ArchiveRowNotFoundError(LookupError):
    pass


class ArchiveSourceNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ArchiveSources:
    candidate_batch: CandidateBatch
    review_run: ReviewRun
    chart_artifacts_by_code: dict[str, Artifact]


@dataclass(frozen=True)
class CreatedArchive:
    snapshot: ArchiveSnapshot
    rows: list[ArchiveRow]


def archive_review_key_for(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


class ArchiveRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def list_snapshots(
        self,
        *,
        pick_date: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[ArchiveSnapshot]:
        with self.session_factory() as session:
            statement = (
                select(ArchiveSnapshot)
                .options(
                    selectinload(ArchiveSnapshot.candidate_batch),
                    selectinload(ArchiveSnapshot.review_run),
                )
                .order_by(ArchiveSnapshot.pick_date.desc(), ArchiveSnapshot.created_at.desc(), ArchiveSnapshot.id)
                .limit(limit)
            )
            if pick_date:
                statement = statement.where(ArchiveSnapshot.pick_date == pick_date)
            if run_id:
                statement = statement.where(ArchiveSnapshot.run_id == run_id)
            return list(session.execute(statement).scalars())

    def list_rows(
        self,
        *,
        pick_date: str | None = None,
        run_id: str | None = None,
        strategy: str | None = None,
        code: str | None = None,
        review_key: str | None = None,
        status: str = "all",
        rank: int | None = None,
        limit: int = 100,
    ) -> list[ArchiveRow]:
        if status not in ARCHIVE_QUERY_STATUSES:
            raise ValueError(f"Unsupported archive status: {status}")

        with self.session_factory() as session:
            statement = (
                select(ArchiveRow)
                .join(ArchiveSnapshot)
                .options(selectinload(ArchiveRow.snapshot))
                .order_by(
                    ArchiveRow.pick_date.desc(),
                    ArchiveSnapshot.created_at.desc(),
                    case((ArchiveRow.rank.is_(None), 1), else_=0),
                    ArchiveRow.rank,
                    ArchiveRow.strategy,
                    ArchiveRow.code,
                    ArchiveRow.id,
                )
                .limit(limit)
            )
            if pick_date:
                statement = statement.where(ArchiveRow.pick_date == pick_date)
            if run_id:
                statement = statement.where(ArchiveRow.run_id == run_id)
            if strategy:
                statement = statement.where(ArchiveRow.strategy == strategy)
            if code:
                statement = statement.where(ArchiveRow.code == code)
            if review_key:
                statement = statement.where(ArchiveRow.review_key == review_key)
            if code and strategy:
                statement = statement.where(ArchiveRow.review_key == archive_review_key_for(code, strategy))
            if status != "all":
                statement = statement.where(ArchiveRow.status == status)
            if rank is not None:
                statement = statement.where(ArchiveRow.rank == rank)
            return list(session.execute(statement).scalars())

    def get_row(self, row_id: int) -> ArchiveRow:
        with self.session_factory() as session:
            statement = (
                select(ArchiveRow)
                .where(ArchiveRow.id == row_id)
                .options(selectinload(ArchiveRow.snapshot))
            )
            row = session.execute(statement).scalar_one_or_none()
            if row is None:
                raise ArchiveRowNotFoundError(row_id)
            return row

    def get_archive_sources(self, *, candidate_batch_id: str, review_run_id: str) -> ArchiveSources:
        with self.session_factory() as session:
            batch = session.execute(
                select(CandidateBatch)
                .where(CandidateBatch.id == candidate_batch_id)
                .options(selectinload(CandidateBatch.candidates))
            ).scalar_one_or_none()
            review_run = session.execute(
                select(ReviewRun)
                .where(ReviewRun.id == review_run_id)
                .options(
                    selectinload(ReviewRun.reviews).selectinload(Review.recommendation),
                    selectinload(ReviewRun.recommendations),
                )
            ).scalar_one_or_none()
            if batch is None or review_run is None:
                raise ArchiveSourceNotFoundError(candidate_batch_id, review_run_id)
            return ArchiveSources(
                candidate_batch=batch,
                review_run=review_run,
                chart_artifacts_by_code=self._chart_artifacts_by_code(session, candidate_batch_id),
            )

    def create_archive_snapshot(
        self,
        *,
        run_id: str,
        snapshot: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> CreatedArchive:
        snapshot_id = uuid4().hex
        with self.session_factory() as session:
            archive_snapshot = ArchiveSnapshot(
                id=snapshot_id,
                run_id=run_id,
                candidate_batch_id=snapshot["candidate_batch_id"],
                review_run_id=snapshot["review_run_id"],
                pick_date=snapshot["pick_date"],
                candidate_run_date=snapshot["candidate_run_date"],
                candidate_count=snapshot["candidate_count"],
                reviewed_count=snapshot["reviewed_count"],
                recommended_count=snapshot["recommended_count"],
                strategy_counts_json=snapshot["strategy_counts"],
                executed_strategies_json=snapshot["executed_strategies"],
                min_score_threshold=snapshot["min_score_threshold"],
                source_json=snapshot["source"],
                summary_json=snapshot["summary"],
                archived_at=datetime.now(UTC),
            )
            session.add(archive_snapshot)
            for row in rows:
                session.add(
                    ArchiveRow(
                        snapshot_id=snapshot_id,
                        candidate_id=row["candidate_id"],
                        review_id=row["review_id"],
                        recommendation_id=row["recommendation_id"],
                        chart_artifact_id=row["chart_artifact_id"],
                        pick_date=snapshot["pick_date"],
                        run_id=run_id,
                        code=row["code"],
                        strategy=row["strategy"],
                        review_key=row["review_key"],
                        status=row["status"],
                        rank=row["rank"],
                        close=row["close"],
                        turnover_n=row["turnover_n"],
                        brick_growth=row["brick_growth"],
                        extra_json=row["extra"],
                        review_payload_json=row["review_payload"],
                        chart_path=row["chart"],
                    )
                )
            session.commit()
            return self._load_created_archive(session, snapshot_id)

    def _load_created_archive(self, session: Session, snapshot_id: str) -> CreatedArchive:
        snapshot = session.execute(
            select(ArchiveSnapshot)
            .where(ArchiveSnapshot.id == snapshot_id)
            .options(
                selectinload(ArchiveSnapshot.candidate_batch),
                selectinload(ArchiveSnapshot.review_run),
            )
        ).scalar_one()
        rows = list(
            session.execute(
                select(ArchiveRow)
                .where(ArchiveRow.snapshot_id == snapshot_id)
                .options(selectinload(ArchiveRow.snapshot))
                .order_by(
                    case((ArchiveRow.rank.is_(None), 1), else_=0),
                    ArchiveRow.rank,
                    ArchiveRow.strategy,
                    ArchiveRow.code,
                    ArchiveRow.id,
                )
            ).scalars()
        )
        return CreatedArchive(snapshot=snapshot, rows=rows)

    def _chart_artifacts_by_code(self, session: Session, candidate_batch_id: str) -> dict[str, Artifact]:
        artifacts = session.execute(
            select(Artifact)
            .where(Artifact.kind == "chart")
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        ).scalars()
        by_code: dict[str, Artifact] = {}
        for artifact in artifacts:
            metadata = artifact.metadata_json or {}
            if metadata.get("source") != "product:chart_export":
                continue
            if metadata.get("candidate_batch_id") != candidate_batch_id:
                continue
            code = str(metadata.get("code") or "")
            if code and code not in by_code:
                by_code[code] = artifact
        return by_code
