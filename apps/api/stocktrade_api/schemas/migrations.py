from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

LegacyImportScope = Literal["all", "candidates", "reviews", "history"]
LegacyImportVerifyScope = Literal["candidates", "reviews", "history"]
LegacyArchiveStatus = Literal["recommended", "reviewed", "unreviewed"]


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


class LegacyArchiveImportRecord(BaseModel):
    code: str
    strategy: str
    review_key: str
    status: LegacyArchiveStatus
    rank: int | None = None
    close: float | None = None
    turnover_n: float | None = None
    brick_growth: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    review_payload: dict[str, Any] | None = None
    chart: str | None = None


class LegacyArchiveImportPlan(BaseModel):
    data_root: str
    source_path: str
    pick_date: str
    legacy_run_id: str
    candidate_run_date: str | None = None
    candidate_count: int
    reviewed_count: int
    recommended_count: int
    strategy_counts: dict[str, Any] = Field(default_factory=dict)
    executed_strategies: list[str] = Field(default_factory=list)
    min_score_threshold: float | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any]
    archived_at: datetime | None = None
    rows: list[LegacyArchiveImportRecord]


class LegacyImportSummary(BaseModel):
    run_id: str
    pick_date: str
    source_file: str
    strategy_counts: dict[str, Any]
    batch_id: str | None = None
    review_run_id: str | None = None
    archive_snapshot_id: str | None = None
    pre_import_backup_id: str | None = None
    pre_import_backup_path: str | None = None
    candidates_imported: int = 0
    reviews_imported: int = 0
    recommendations_imported: int = 0
    archive_rows_imported: int = 0
    archive_reviewed_count: int = 0
    archive_recommended_count: int = 0


class LegacyImportDryRunReport(BaseModel):
    migration_id: str | None = None
    dry_run: bool
    data_root: str
    sections: dict[str, LegacyImportSectionReport]
    totals: LegacyImportTotals
    warnings: list[LegacyImportIssue]
    quarantine: list[LegacyImportIssue]
    import_summary: LegacyImportSummary | None = None


class LegacyImportVerifyRequest(BaseModel):
    data_root: str = "data"
    scope: LegacyImportVerifyScope
    pick_date: str
    run_id: str | None = None


class LegacyImportVerifyCounts(BaseModel):
    legacy: int
    sqlite: int
    duckdb: int | None = None


class LegacyImportVerifyMismatches(BaseModel):
    missing_in_sqlite: list[str] = Field(default_factory=list)
    extra_in_sqlite: list[str] = Field(default_factory=list)
    missing_in_duckdb: list[str] = Field(default_factory=list)
    extra_in_duckdb: list[str] = Field(default_factory=list)


class LegacyImportVerifyReport(BaseModel):
    passed: bool
    data_root: str
    scope: LegacyImportVerifyScope
    pick_date: str
    run_id: str | None = None
    source_path: str
    duckdb_checked: bool
    counts: LegacyImportVerifyCounts
    mismatches: LegacyImportVerifyMismatches


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
