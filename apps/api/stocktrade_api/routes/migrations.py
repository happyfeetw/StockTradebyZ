from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_migration_repository
from ..schemas.migrations import (
    LegacyImportDryRunReport,
    LegacyImportDryRunRequest,
    LegacyMigrationRunResponse,
    MigrationQuarantineRecord,
)
from ..services.legacy_import import (
    LegacyCandidateImportError,
    build_legacy_candidate_import_report,
    build_legacy_review_import_report,
    load_legacy_candidate_import_plan,
    load_legacy_review_import_plan,
    scan_legacy_import_dry_run,
)
from ..storage.migration_repository import MigrationRepository, MigrationRunNotFoundError
from ..storage.sqlite_models import MigrationQuarantine, MigrationRun

router = APIRouter(tags=["migrations"])


@router.post("/migrations/import-legacy", response_model=LegacyImportDryRunReport)
def import_legacy(
    request: LegacyImportDryRunRequest,
    repository: MigrationRepository = Depends(get_migration_repository),
) -> LegacyImportDryRunReport:
    if not request.dry_run and (request.scope not in {"candidates", "reviews"} or not request.pick_date):
        raise HTTPException(
            status_code=409,
            detail="legacy import writes require scope='candidates' or scope='reviews' and pick_date",
        )

    if request.dry_run:
        report = scan_legacy_import_dry_run(request.data_root)
        migration_run = repository.record_dry_run(report)
        return report.model_copy(update={"migration_id": migration_run.id})

    try:
        if request.scope == "candidates":
            import_plan = load_legacy_candidate_import_plan(request.data_root, request.pick_date)
            migration_run = repository.record_candidate_import(
                build_legacy_candidate_import_report(import_plan),
                import_plan,
            )
        else:
            review_plan = load_legacy_review_import_plan(request.data_root, request.pick_date)
            migration_run = repository.record_review_import(
                build_legacy_review_import_report(review_plan),
                review_plan,
            )
    except LegacyCandidateImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return LegacyImportDryRunReport.model_validate(migration_run.report_json or {})


def quarantine_record(row: MigrationQuarantine, repository: MigrationRepository) -> MigrationQuarantineRecord:
    return MigrationQuarantineRecord(
        id=row.id,
        migration_run_id=row.migration_run_id,
        source_path=row.source_path,
        reason=row.reason,
        payload=repository.quarantine_issue(row),
        created_at=row.created_at,
    )


def migration_response(migration_run: MigrationRun, repository: MigrationRepository) -> LegacyMigrationRunResponse:
    report = LegacyImportDryRunReport.model_validate(migration_run.report_json or {})
    return LegacyMigrationRunResponse(
        id=migration_run.id,
        source_root=migration_run.source_root,
        status=migration_run.status,
        started_at=migration_run.started_at,
        finished_at=migration_run.finished_at,
        report=report,
        quarantine=[
            quarantine_record(row, repository)
            for row in migration_run.quarantine_rows
        ],
        created_at=migration_run.created_at,
    )


@router.get("/migrations/{migration_id}", response_model=LegacyMigrationRunResponse)
def get_migration_run(
    migration_id: str,
    repository: MigrationRepository = Depends(get_migration_repository),
) -> LegacyMigrationRunResponse:
    try:
        return migration_response(repository.get_migration_run(migration_id), repository)
    except MigrationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="migration run not found") from exc
