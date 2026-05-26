from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ..schemas.migrations import (
    LegacyCandidateImportPlan,
    LegacyImportSummary,
    LegacyImportDryRunReport,
    LegacyImportIssue,
    LegacyReviewImportPlan,
)
from .sqlite_models import Candidate, CandidateBatch, MigrationQuarantine, MigrationRun, Recommendation, Review, ReviewRun, Run


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
            self._add_quarantine_rows(session, run_id, report.quarantine)
            session.commit()
            migration_run = self._load_migration_run(session, run_id)
            if migration_run is None:
                raise MigrationRunNotFoundError(run_id)
            return migration_run

    def record_candidate_import(
        self,
        report: LegacyImportDryRunReport,
        plan: LegacyCandidateImportPlan,
        *,
        migration_id: str | None = None,
        batch_id: str | None = None,
    ) -> MigrationRun:
        run_id = migration_id or uuid4().hex
        candidate_batch_id = batch_id or uuid4().hex
        now = utc_now()
        import_summary = LegacyImportSummary(
            run_id=run_id,
            pick_date=plan.pick_date,
            source_file=plan.source_path,
            strategy_counts=plan.strategy_counts,
            batch_id=candidate_batch_id,
            candidates_imported=len(plan.candidates),
        )
        stored_report = report.model_copy(
            update={
                "migration_id": run_id,
                "dry_run": False,
                "import_summary": import_summary,
            }
        )
        report_payload = stored_report.model_dump(mode="json")
        summary = {
            "dry_run": False,
            "data_root": report.data_root,
            "totals": report_payload["totals"],
            "import_summary": report_payload["import_summary"],
        }

        with self.session_factory() as session:
            session.add(
                Run(
                    id=run_id,
                    kind="legacy_import",
                    status="succeeded",
                    pick_date=plan.pick_date,
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
            session.add(
                CandidateBatch(
                    id=candidate_batch_id,
                    run_id=run_id,
                    pick_date=plan.pick_date,
                    source="legacy:candidates",
                    strategy_counts_json=plan.strategy_counts,
                )
            )
            for candidate in plan.candidates:
                session.add(
                    Candidate(
                        batch_id=candidate_batch_id,
                        code=candidate.code,
                        strategy=candidate.strategy,
                        pick_date=candidate.date,
                        close=candidate.close,
                        turnover_n=candidate.turnover_n,
                        brick_growth=candidate.brick_growth,
                        extra_json=candidate.extra,
                    )
                )
            self._add_quarantine_rows(session, run_id, report.quarantine)
            session.commit()
            migration_run = self._load_migration_run(session, run_id)
            if migration_run is None:
                raise MigrationRunNotFoundError(run_id)
            return migration_run

    def record_review_import(
        self,
        report: LegacyImportDryRunReport,
        plan: LegacyReviewImportPlan,
        *,
        migration_id: str | None = None,
        review_run_id: str | None = None,
    ) -> MigrationRun:
        run_id = migration_id or uuid4().hex
        imported_review_run_id = review_run_id or uuid4().hex
        now = utc_now()
        strategy_counts = _strategy_counts_for_reviews(plan)
        import_summary = LegacyImportSummary(
            run_id=run_id,
            pick_date=plan.pick_date,
            source_file=plan.source_path,
            strategy_counts=strategy_counts,
            review_run_id=imported_review_run_id,
            reviews_imported=len(plan.reviews),
            recommendations_imported=len(plan.recommendations),
        )
        stored_report = report.model_copy(
            update={
                "migration_id": run_id,
                "dry_run": False,
                "import_summary": import_summary,
            }
        )
        report_payload = stored_report.model_dump(mode="json")
        summary = {
            "dry_run": False,
            "data_root": report.data_root,
            "totals": report_payload["totals"],
            "import_summary": report_payload["import_summary"],
        }

        with self.session_factory() as session:
            session.add(
                Run(
                    id=run_id,
                    kind="legacy_import",
                    status="succeeded",
                    pick_date=plan.pick_date,
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
            review_run = ReviewRun(
                id=imported_review_run_id,
                run_id=run_id,
                pick_date=plan.pick_date,
                provider=plan.provider,
                status="succeeded",
                summary_json={
                    "source": "legacy:reviews",
                    "reviews_imported": len(plan.reviews),
                    "recommendations_imported": len(plan.recommendations),
                    "strategy_counts": strategy_counts,
                },
            )
            session.add(review_run)
            session.flush()

            reviews_by_key: dict[str, Review] = {}
            for item in plan.reviews:
                review = Review(
                    review_run_id=imported_review_run_id,
                    code=item.code,
                    strategy=item.strategy,
                    review_key=item.review_key,
                    verdict=item.verdict,
                    total_score=item.total_score,
                    reviewer=item.reviewer or plan.provider,
                    payload_json=item.payload,
                )
                session.add(review)
                reviews_by_key[item.review_key] = review
            session.flush()

            for item in plan.recommendations:
                session.add(
                    Recommendation(
                        review_run_id=imported_review_run_id,
                        review=reviews_by_key.get(item.review_key),
                        rank=item.rank,
                        code=item.code,
                        strategy=item.strategy,
                        review_key=item.review_key,
                        verdict=item.verdict,
                        total_score=item.total_score,
                        payload_json=item.payload,
                    )
                )
            self._add_quarantine_rows(session, run_id, report.quarantine)
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

    def _add_quarantine_rows(
        self,
        session: Session,
        migration_id: str,
        issues: list[LegacyImportIssue],
    ) -> None:
        for issue in issues:
            session.add(
                MigrationQuarantine(
                    migration_run_id=migration_id,
                    source_path=issue.source_path,
                    reason=issue.reason,
                    payload_json=issue.model_dump(mode="json"),
                )
            )


def _strategy_counts_for_reviews(plan: LegacyReviewImportPlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for review in plan.reviews:
        counts[review.strategy] = counts.get(review.strategy, 0) + 1
    return dict(sorted(counts.items()))
