from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from .runs import ArtifactResponse, RunSummary


class MarketDataRunRequest(BaseModel):
    config_path: str | None = None
    start: str | None = None
    end: str | None = None
    out_dir: str | None = None
    log_path: str | None = None
    workers: int | None = Field(default=None, ge=1, le=32)

    @field_validator("config_path", "start", "end", "out_dir", "log_path", mode="before")
    @classmethod
    def blank_string_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class MarketDataRunResponse(BaseModel):
    run: RunSummary
    summary: dict[str, Any]
    artifacts: list[ArtifactResponse] = Field(default_factory=list)
