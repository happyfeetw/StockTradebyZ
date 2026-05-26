from __future__ import annotations

from fastapi import APIRouter

from stocktrade.domain.metadata import PRODUCT_STACK, SERVICE_NAME, VERSION
from ..schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=VERSION,
        stack=PRODUCT_STACK,
        simulated_trading_in_scope=False,
    )
