from __future__ import annotations

from fastapi import Request

from .jobs.runtime import JobRuntime
from .storage.run_repository import RunRepository


def get_run_repository(request: Request) -> RunRepository:
    return request.app.state.run_repository


def get_job_runtime(request: Request) -> JobRuntime:
    return request.app.state.job_runtime
