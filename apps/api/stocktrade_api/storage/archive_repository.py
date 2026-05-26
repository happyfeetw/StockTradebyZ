from __future__ import annotations

import re

from sqlalchemy import case, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .sqlite_models import ArchiveRow, ArchiveSnapshot

ARCHIVE_QUERY_STATUSES = {"all", "recommended", "reviewed", "unreviewed"}


class ArchiveRowNotFoundError(LookupError):
    pass


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
