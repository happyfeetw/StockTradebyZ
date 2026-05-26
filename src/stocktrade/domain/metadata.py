from __future__ import annotations

from dataclasses import dataclass, asdict

SERVICE_NAME = "stocktrade-api"
VERSION = "0.1.0"


@dataclass(frozen=True)
class ProductStack:
    frontend: str = "React/Vite/TypeScript"
    backend: str = "FastAPI"
    domain_language: str = "Python"
    product_state_database: str = "SQLite"
    analytical_database: str = "DuckDB"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


PRODUCT_STACK = ProductStack().to_dict()
