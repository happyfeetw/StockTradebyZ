from __future__ import annotations

from fastapi import Request

from .jobs.runtime import JobRuntime
from .storage.candidate_repository import CandidateRepository
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


def get_preselect_service():
    from stocktrade.domain.selection import PreselectService

    return PreselectService()
