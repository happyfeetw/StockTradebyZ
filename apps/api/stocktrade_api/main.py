from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from .routes.archive import router as archive_router
from .routes.artifacts import router as artifacts_router
from .routes.backups import router as backups_router
from .routes.candidates import router as candidates_router
from .routes.health import router as health_router
from .routes.migrations import router as migrations_router
from .routes.reviews import router as reviews_router
from .routes.runs import router as runs_router
from .jobs.runtime import JobRuntime
from .storage.artifact_service import DEFAULT_ARTIFACT_ROOT
from .storage.archive_repository import ArchiveRepository
from .storage.backup_service import DEFAULT_BACKUP_ROOT, BackupService
from .storage.candidate_repository import CandidateRepository
from .storage.duckdb import DEFAULT_DUCKDB_PATH, DuckDBAnalyticsWriter
from .storage.migration_repository import MigrationRepository
from .storage.review_repository import ReviewRepository
from .storage.run_repository import RunRepository
from .storage.sqlite import DEFAULT_SQLITE_PATH, create_session_factory, create_sqlite_engine

API_TITLE = "StockTradebyZ API"
API_VERSION = "0.1.0"


def create_app(
    *,
    sqlite_path: str | Path = DEFAULT_SQLITE_PATH,
    duckdb_path: str | Path | None = DEFAULT_DUCKDB_PATH,
    backup_root: str | Path = DEFAULT_BACKUP_ROOT,
    artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    app = FastAPI(title=API_TITLE, version=API_VERSION)
    sqlite_engine: Engine | None = None

    if session_factory is None:
        sqlite_engine = create_sqlite_engine(sqlite_path)
        session_factory = create_session_factory(sqlite_engine)

    run_repository = RunRepository(session_factory)
    app.state.run_repository = run_repository
    app.state.candidate_repository = CandidateRepository(session_factory)
    app.state.review_repository = ReviewRepository(session_factory)
    app.state.archive_repository = ArchiveRepository(session_factory)
    analytics_writer = DuckDBAnalyticsWriter(duckdb_path) if duckdb_path is not None else None
    app.state.analytics_writer = analytics_writer
    app.state.migration_repository = MigrationRepository(
        session_factory,
        analytics_writer=analytics_writer,
        artifact_root=artifact_root,
    )

    def dispose_sqlite() -> None:
        if sqlite_engine is not None:
            sqlite_engine.dispose()

    app.state.backup_service = BackupService(
        run_repository,
        sqlite_path=sqlite_path,
        duckdb_path=duckdb_path,
        backup_root=backup_root,
        product_version=API_VERSION,
        dispose_sqlite=dispose_sqlite,
    )
    app.state.job_runtime = JobRuntime(run_repository)
    app.state.review_provider_executor = None
    app.state.session_factory = session_factory
    app.state.sqlite_path = sqlite_path
    app.state.duckdb_path = duckdb_path
    app.state.backup_root = backup_root
    app.state.artifact_root = artifact_root
    app.state.sqlite_engine = sqlite_engine

    app.include_router(health_router, prefix="/api")
    app.include_router(artifacts_router, prefix="/api")
    app.include_router(backups_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(candidates_router, prefix="/api")
    app.include_router(reviews_router, prefix="/api")
    app.include_router(archive_router, prefix="/api")
    app.include_router(migrations_router, prefix="/api")

    @app.on_event("shutdown")
    def shutdown_storage() -> None:
        dispose_sqlite()

    return app


app = create_app()
