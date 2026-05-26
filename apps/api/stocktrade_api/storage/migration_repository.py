from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ..schemas.migrations import LegacyImportDryRunReport, LegacyImportIssue
from .sqlite_models import MigrationQuarantine, MigrationRun, Run


def utc_now() -> datetime:
    return datetime.now(UTC)


class MigrationRunNotFoundError(LookupError):
    pass


class MigrationRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def record_dry_run(self, report: LegacyImportDryRunReport, *, migration_id: str | None = None) -> MigrationRun:
        run_id = migration_id or uuid4().hex
        now = utc_now()
        report_payload = report.model_dump(mode="json")
        report_payload["migration_id"] = run_id
        summary = {
            "dry_run": True,
            "data_root": report.data_root,
            "totals": report_payload["totals"],
        }

        with self.session_factory() as session:
            session.add(
                Run(
                    id=run_id,
                    kind="legacy_import",
                    status="succeeded",
                    started_at=now,
                    finished_at=now,
                    summary_json=summary,
                )
            )
            session.add(
                MigrationRun(
                    id=run_id,
                    source_root=report.data_root,
                    status="succeeded",
                    started_at=now,
                    finished_at=now,
                    report_json=report_payload,
                )
            )
            for issue in report.quarantine:
                session.add(
                    MigrationQuarantine(
                        migration_run_id=run_id,
                        source_path=issue.source_path,
                        reason=issue.reason,
                        payload_json=issue.model_dump(mode="json"),
                    )
                )
            session.commit()
            migration_run = self._load_migration_run(session, run_id)
            if migration_run is None:
                raise MigrationRunNotFoundError(run_id)
            return migration_run

    def get_migration_run(self, migration_id: str) -> MigrationRun:
        with self.session_factory() as session:
            migration_run = self._load_migration_run(session, migration_id)
            if migration_run is None:
                raise MigrationRunNotFoundError(migration_id)
            return migration_run

    def quarantine_issue(self, row: MigrationQuarantine) -> LegacyImportIssue:
        payload: dict[str, Any] = dict(row.payload_json or {})
        payload.setdefault("source_path", row.source_path)
        payload.setdefault("reason", row.reason)
        return LegacyImportIssue.model_validate(payload)

    def _load_migration_run(self, session: Session, migration_id: str) -> MigrationRun | None:
        statement = (
            select(MigrationRun)
            .where(MigrationRun.id == migration_id)
            .options(selectinload(MigrationRun.quarantine_rows))
        )
        return session.execute(statement).scalar_one_or_none()
