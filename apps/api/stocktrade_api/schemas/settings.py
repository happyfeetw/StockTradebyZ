from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict
from pydantic import Field, field_validator

from .health import ProductStack

StrategyPreferenceId = Literal["b1", "b2", "brick"]


class ProductPreferenceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Shanghai"
    theme: Literal["system", "light", "dark"] = "system"
    table_density: Literal["comfortable", "compact"] = "comfortable"
    default_strategy_ids: list[StrategyPreferenceId] = Field(default_factory=lambda: ["b1"], min_length=1, max_length=3)
    analytics_default_limit: int = Field(default=100, ge=1, le=500)
    candidate_page_size: int = Field(default=50, ge=10, le=500)
    review_page_size: int = Field(default=50, ge=10, le=500)
    archive_page_size: int = Field(default=50, ge=10, le=500)
    chart_export_enabled: bool = True
    auto_archive_after_review: bool = False

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("default_strategy_ids")
    @classmethod
    def validate_strategy_ids(cls, value: list[StrategyPreferenceId]) -> list[StrategyPreferenceId]:
        if len(set(value)) != len(value):
            raise ValueError("default_strategy_ids must be unique")
        return value


class ProductPreferenceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["defaults", "sqlite"]
    updated_at: datetime | None
    preferences: ProductPreferenceSettings


class ProductSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferences: ProductPreferenceSettings


class ConfigFileMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    path: str
    exists: bool
    sections: list[str]
    writable: bool = False
    exposed: bool = True


class ExternalIntegrationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    configured: bool
    source: str
    secret_exposed: bool = False


class LocalStateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sqlite_path: str
    duckdb_path: str | None = None
    artifact_root: str
    backup_root: str


class ProductSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    version: str
    stack: ProductStack
    simulated_trading_in_scope: bool
    product_preferences: ProductPreferenceState
    local_state: LocalStateSettings
    config_files: list[ConfigFileMetadata]
    external_integrations: list[ExternalIntegrationStatus]
