from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_backup_service
from ..schemas.backups import BackupCreateResponse
from ..storage.backup_service import BackupError, BackupService

router = APIRouter(tags=["backups"])


@router.post("/backups", response_model=BackupCreateResponse)
def create_backup(
    service: BackupService = Depends(get_backup_service),
) -> BackupCreateResponse:
    try:
        return BackupCreateResponse(backup=service.create_backup())
    except BackupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
