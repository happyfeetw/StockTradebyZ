from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BackupManifest(BaseModel):
    backup_id: str
    run_id: str
    created_at: datetime
    backup_path: str
    product_version: str
    sources: dict[str, str | None]
    files: dict[str, str] = Field(default_factory=dict)
    missing_optional: list[str] = Field(default_factory=list)


class BackupCreateResponse(BaseModel):
    backup: BackupManifest


class BackupRestoreRequest(BaseModel):
    backup_path: str


class BackupRestoreResult(BaseModel):
    restore_id: str
    run_id: str
    backup_id: str
    backup_path: str
    restored_at: datetime
    files_restored: dict[str, str] = Field(default_factory=dict)
    missing_optional: list[str] = Field(default_factory=list)


class BackupRestoreResponse(BaseModel):
    restore: BackupRestoreResult
