from __future__ import annotations

from pydantic import BaseModel, Field


class LegacyImportDryRunRequest(BaseModel):
    dry_run: bool = True
    data_root: str = "data"


class LegacyImportIssue(BaseModel):
    section: str
    source_path: str
    reason: str
    message: str
    record_key: str | None = None


class LegacyImportSectionReport(BaseModel):
    files_seen: int = 0
    files_valid: int = 0
    records_seen: int = 0
    records_valid: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)


class LegacyImportTotals(BaseModel):
    files_seen: int
    files_valid: int
    records_seen: int
    records_valid: int
    warning_count: int
    quarantine_count: int


class LegacyImportDryRunReport(BaseModel):
    dry_run: bool
    data_root: str
    sections: dict[str, LegacyImportSectionReport]
    totals: LegacyImportTotals
    warnings: list[LegacyImportIssue]
    quarantine: list[LegacyImportIssue]
