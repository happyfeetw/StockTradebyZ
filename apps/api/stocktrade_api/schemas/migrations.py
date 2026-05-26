from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

LegacyImportScope = Literal["all", "candidates", "reviews"]


class LegacyImportDryRunRequest(BaseModel):
    dry_run: bool = True
    data_root: str = "data"
    scope: LegacyImportScope = "all"
    pick_date: str | None = None


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


class LegacyCandidateImportRecord(BaseModel):
    code: str
    date: str
    strategy: str
    close: float
    turnover_n: float
    brick_growth: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class LegacyCandidateImportPlan(BaseModel):
    data_root: str
    source_path: str
    run_date: str
    pick_date: str
    strategy_counts: dict[str, int]
    candidates: list[LegacyCandidateImportRecord]


class LegacyReviewImportRecord(BaseModel):
    code: str
    strategy: str
    review_key: str
    verdict: str | None = None
    total_score: float | None = None
    reviewer: str | None = None
    payload: dict[str, Any]


class LegacyRecommendationImportRecord(BaseModel):
    rank: int
    code: str
    strategy: str
    review_key: str
    verdict: str | None = None
    total_score: float | None = None
    payload: dict[str, Any]


class LegacyReviewImportPlan(BaseModel):
    data_root: str
    source_path: str
    pick_date: str
    provider: str
    reviews: list[LegacyReviewImportRecord]
    recommendations: list[LegacyRecommendationImportRecord]


class LegacyImportSummary(BaseModel):
    run_id: str
    pick_date: str
    source_file: str
    strategy_counts: dict[str, int]
    batch_id: str | None = None
    review_run_id: str | None = None
    candidates_imported: int = 0
    reviews_imported: int = 0
    recommendations_imported: int = 0


class LegacyImportDryRunReport(BaseModel):
    migration_id: str | None = None
    dry_run: bool
    data_root: str
    sections: dict[str, LegacyImportSectionReport]
    totals: LegacyImportTotals
    warnings: list[LegacyImportIssue]
    quarantine: list[LegacyImportIssue]
    import_summary: LegacyImportSummary | None = None


class MigrationQuarantineRecord(BaseModel):
    id: int
    migration_run_id: str
    source_path: str
    reason: str
    payload: LegacyImportIssue
    created_at: datetime


class LegacyMigrationRunResponse(BaseModel):
    id: str
    source_root: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report: LegacyImportDryRunReport
    quarantine: list[MigrationQuarantineRecord]
    created_at: datetime
