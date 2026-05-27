from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


StrategyParityStatus = Literal["product_owned_with_legacy_adapter", "legacy_only", "not_applicable"]


class StrategyConfigProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    exists: bool
    section: str


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    enabled_by_default: bool
    candidate_identity: list[str]
    parity_status: StrategyParityStatus
    config_provenance: StrategyConfigProvenance
    parameters: dict[str, Any]


class StrategyMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_path: str
    config_exists: bool
    candidate_identity: list[str]
    strategies: list[StrategyDefinition]
