from __future__ import annotations

from pathlib import Path

from fastapi import Request

from .jobs.runtime import JobRuntime
from .storage.archive_repository import ArchiveRepository
from .storage.backup_service import BackupService
from .storage.candidate_repository import CandidateRepository
from .storage.duckdb import DuckDBAnalyticsReader, DuckDBAnalyticsWriter
from .storage.migration_repository import MigrationRepository
from .storage.review_repository import ReviewRepository
from .storage.run_repository import RunRepository


def get_run_repository(request: Request) -> RunRepository:
    return request.app.state.run_repository


def get_job_runtime(request: Request) -> JobRuntime:
    return request.app.state.job_runtime


def get_candidate_repository(request: Request) -> CandidateRepository:
    return request.app.state.candidate_repository


def get_review_repository(request: Request) -> ReviewRepository:
    return request.app.state.review_repository


def get_analytics_writer(request: Request) -> DuckDBAnalyticsWriter | None:
    return request.app.state.analytics_writer


def get_analytics_reader(request: Request) -> DuckDBAnalyticsReader | None:
    return request.app.state.analytics_reader


def get_archive_repository(request: Request) -> ArchiveRepository:
    return request.app.state.archive_repository


def get_backup_service(request: Request) -> BackupService:
    return request.app.state.backup_service


def get_migration_repository(request: Request) -> MigrationRepository:
    return request.app.state.migration_repository


def get_artifact_root(request: Request) -> Path:
    return request.app.state.artifact_root


def get_review_provider_executor(request: Request):
    return getattr(request.app.state, "review_provider_executor", None)


def get_preselect_service():
    from stocktrade.domain.selection import PreselectService

    return PreselectService()
