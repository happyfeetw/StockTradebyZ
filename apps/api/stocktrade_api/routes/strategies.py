from __future__ import annotations

from fastapi import APIRouter

from ..schemas.strategies import StrategyMetadataResponse
from ..services.settings_metadata import build_strategy_metadata

router = APIRouter(tags=["strategies"])


@router.get("/strategies", response_model=StrategyMetadataResponse)
def get_strategies() -> StrategyMetadataResponse:
    return StrategyMetadataResponse(**build_strategy_metadata())
