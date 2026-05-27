from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .health import ProductStack


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
    local_state: LocalStateSettings
    config_files: list[ConfigFileMetadata]
    external_integrations: list[ExternalIntegrationStatus]
