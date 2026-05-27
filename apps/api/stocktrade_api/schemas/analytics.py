from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrategySummaryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pick_date: str
    run_id: str
    strategy: str
    total: int
    reviewed: int
    recommended: int
    unreviewed: int
    reviewed_rate: float
    recommended_rate: float


class StrategySummaryTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    reviewed: int
    recommended: int
    unreviewed: int
    reviewed_rate: float
    recommended_rate: float
    strategies: list[str] = Field(default_factory=list)
    pick_dates: list[str] = Field(default_factory=list)


class StrategySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[StrategySummaryRow]
    totals: StrategySummaryTotals
    filters: dict[str, str | None]
