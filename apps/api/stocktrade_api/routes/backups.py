from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_backup_service
from ..schemas.backups import BackupCreateResponse, BackupRestoreRequest, BackupRestoreResponse
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


@router.post("/restore", response_model=BackupRestoreResponse)
def restore_backup(
    request: BackupRestoreRequest,
    service: BackupService = Depends(get_backup_service),
) -> BackupRestoreResponse:
    try:
        return BackupRestoreResponse(restore=service.restore_backup(request.backup_path))
    except BackupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
