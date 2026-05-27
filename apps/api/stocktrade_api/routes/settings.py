from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas.settings import ProductSettingsResponse
from ..services.settings_metadata import build_product_settings

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=ProductSettingsResponse)
def get_settings(request: Request) -> ProductSettingsResponse:
    return ProductSettingsResponse(
        **build_product_settings(
            sqlite_path=request.app.state.sqlite_path,
            duckdb_path=request.app.state.duckdb_path,
            artifact_root=request.app.state.artifact_root,
            backup_root=request.app.state.backup_root,
        )
    )
