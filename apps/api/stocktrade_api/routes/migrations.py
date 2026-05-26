from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas.migrations import LegacyImportDryRunReport, LegacyImportDryRunRequest
from ..services.legacy_import import scan_legacy_import_dry_run

router = APIRouter(tags=["migrations"])


@router.post("/migrations/import-legacy", response_model=LegacyImportDryRunReport)
def import_legacy(request: LegacyImportDryRunRequest) -> LegacyImportDryRunReport:
    if not request.dry_run:
        raise HTTPException(status_code=409, detail="legacy import writes are not enabled yet")
    return scan_legacy_import_dry_run(request.data_root)
