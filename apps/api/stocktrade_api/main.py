from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from .routes.candidates import router as candidates_router
from .routes.health import router as health_router
from .routes.migrations import router as migrations_router
from .routes.reviews import router as reviews_router
from .routes.runs import router as runs_router
from .jobs.runtime import JobRuntime
from .storage.candidate_repository import CandidateRepository
from .storage.review_repository import ReviewRepository
from .storage.run_repository import RunRepository
from .storage.sqlite import DEFAULT_SQLITE_PATH, create_session_factory, create_sqlite_engine

API_TITLE = "StockTradebyZ API"
API_VERSION = "0.1.0"


def create_app(
    *,
    sqlite_path: str | Path = DEFAULT_SQLITE_PATH,
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
    app.state.job_runtime = JobRuntime(run_repository)
    app.state.session_factory = session_factory
    app.state.sqlite_engine = sqlite_engine

    app.include_router(health_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(candidates_router, prefix="/api")
    app.include_router(reviews_router, prefix="/api")
    app.include_router(migrations_router, prefix="/api")

    @app.on_event("shutdown")
    def shutdown_storage() -> None:
        if app.state.sqlite_engine is not None:
            app.state.sqlite_engine.dispose()

    return app


app = create_app()
