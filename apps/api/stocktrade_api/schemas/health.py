from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProductStack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontend: str
    backend: str
    domain_language: str
    product_state_database: str
    analytical_database: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    service: str
    version: str
    stack: ProductStack
    simulated_trading_in_scope: bool
