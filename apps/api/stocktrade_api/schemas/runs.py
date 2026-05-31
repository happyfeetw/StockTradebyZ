from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelling", "cancelled"]
RunKind = Literal[
    "preselect",
    "market_data",
    "review",
    "archive",
    "chart_export",
    "legacy_import",
    "backup",
    "restore",
    "diagnostic",
]


class DiagnosticRunRequest(BaseModel):
    fail: bool = False


class RunSummary(BaseModel):
    id: str
    kind: RunKind
    status: RunStatus
    pick_date: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: dict[str, Any] | None = None
    created_at: datetime


class JobStepResponse(BaseModel):
    id: int
    run_id: str
    name: str
    status: RunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: dict[str, Any] | None = None
    created_at: datetime


class JobEventResponse(BaseModel):
    id: int
    run_id: str
    step_id: int | None = None
    level: str
    message: str
    created_at: datetime


class ArtifactResponse(BaseModel):
    id: str
    run_id: str
    kind: str
    path: str
    content_type: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class RunDetail(RunSummary):
    steps: list[JobStepResponse] = Field(default_factory=list)
    events: list[JobEventResponse] = Field(default_factory=list)
    artifacts: list[ArtifactResponse] = Field(default_factory=list)


class RunListResponse(BaseModel):
    runs: list[RunSummary]


class RunEventsResponse(BaseModel):
    events: list[JobEventResponse]


class RunArtifactsResponse(BaseModel):
    artifacts: list[ArtifactResponse]
