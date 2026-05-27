from __future__ import annotations

from pydantic import BaseModel, Field

from .runs import ArtifactResponse, RunSummary


class ChartExportRunCreateRequest(BaseModel):
    candidate_batch_id: str = Field(min_length=1, max_length=64)
    raw_dir: str | None = Field(default=None, min_length=1)
    bars: int = Field(default=120, ge=1, le=1000)
    limit: int = Field(default=0, ge=0, le=500)


class ChartExportRunCreateResponse(BaseModel):
    run: RunSummary
    artifacts: list[ArtifactResponse]
