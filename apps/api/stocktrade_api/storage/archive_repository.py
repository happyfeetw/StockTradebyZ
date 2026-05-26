from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .sqlite_models import ArchiveRow, ArchiveSnapshot

ARCHIVE_STATUS_FILTERS = {"all", "recommended", "reviewed", "unreviewed"}


class ArchiveSnapshotNotFoundError(LookupError):
    pass


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
                .order_by(ArchiveSnapshot.pick_date.desc(), ArchiveSnapshot.created_at.desc(), ArchiveSnapshot.id.desc())
                .limit(limit)
            )
            if pick_date:
                statement = statement.where(ArchiveSnapshot.pick_date == pick_date)
            if run_id:
                statement = statement.where(ArchiveSnapshot.run_id == run_id)
            return list(session.execute(statement).scalars())

    def get_snapshot(self, *, pick_date: str, run_id: str | None = None) -> ArchiveSnapshot:
        with self.session_factory() as session:
            statement = (
                select(ArchiveSnapshot)
                .where(ArchiveSnapshot.pick_date == pick_date)
                .order_by(ArchiveSnapshot.created_at.desc(), ArchiveSnapshot.id.desc())
                .limit(1)
            )
            if run_id:
                statement = statement.where(ArchiveSnapshot.run_id == run_id)
            snapshot = session.execute(statement).scalar_one_or_none()
            if snapshot is None:
                raise ArchiveSnapshotNotFoundError(pick_date)
            return snapshot

    def list_rows(
        self,
        *,
        pick_date: str | None = None,
        run_id: str | None = None,
        snapshot_id: str | None = None,
        strategy: str | None = None,
        status: str = "all",
        code: str | None = None,
        limit: int = 500,
    ) -> list[ArchiveRow]:
        if status not in ARCHIVE_STATUS_FILTERS:
            raise ValueError(f"Unsupported archive status: {status}")

        with self.session_factory() as session:
            status_order = case(
                (ArchiveRow.status == "recommended", 0),
                (ArchiveRow.status == "reviewed", 1),
                (ArchiveRow.status == "unreviewed", 2),
                else_=3,
            )
            statement = (
                select(ArchiveRow)
                .join(ArchiveSnapshot)
                .options(selectinload(ArchiveRow.snapshot))
                .order_by(
                    ArchiveRow.pick_date.desc(),
                    status_order,
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
                statement = statement.where(ArchiveSnapshot.run_id == run_id)
            if snapshot_id:
                statement = statement.where(ArchiveRow.snapshot_id == snapshot_id)
            if strategy:
                statement = statement.where(ArchiveRow.strategy == strategy)
            if status != "all":
                statement = statement.where(ArchiveRow.status == status)
            if code:
                statement = statement.where(ArchiveRow.code == code)
            return list(session.execute(statement).scalars())
