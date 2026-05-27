from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import get_settings_repository
from ..schemas.settings import (
    ProductPreferenceSettings,
    ProductPreferenceState,
    ProductSettingsResponse,
    ProductSettingsUpdateRequest,
)
from ..services.settings_metadata import build_product_settings
from ..storage.settings_repository import (
    PRODUCT_PREFERENCES_KEY,
    SettingsRepository,
    SettingsStorageUnavailableError,
)

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=ProductSettingsResponse)
def get_settings(
    request: Request,
    repository: SettingsRepository = Depends(get_settings_repository),
) -> ProductSettingsResponse:
    return _settings_response(request, _load_preferences(repository))


@router.put("/settings", response_model=ProductSettingsResponse)
def put_settings(
    payload: ProductSettingsUpdateRequest,
    request: Request,
    repository: SettingsRepository = Depends(get_settings_repository),
) -> ProductSettingsResponse:
    try:
        setting = repository.upsert_setting(
            PRODUCT_PREFERENCES_KEY,
            payload.preferences.model_dump(mode="json"),
        )
    except SettingsStorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return _settings_response(
        request,
        ProductPreferenceState(
            source="sqlite",
            updated_at=setting.updated_at,
            preferences=ProductPreferenceSettings.model_validate(setting.value_json),
        ),
    )


def _settings_response(request: Request, product_preferences: ProductPreferenceState) -> ProductSettingsResponse:
    return ProductSettingsResponse(
        **build_product_settings(
            sqlite_path=request.app.state.sqlite_path,
            duckdb_path=request.app.state.duckdb_path,
            artifact_root=request.app.state.artifact_root,
            backup_root=request.app.state.backup_root,
            product_preferences=product_preferences.model_dump(mode="python"),
        )
    )


def _load_preferences(repository: SettingsRepository) -> ProductPreferenceState:
    try:
        setting = repository.get_setting(PRODUCT_PREFERENCES_KEY)
    except SettingsStorageUnavailableError:
        setting = None

    if setting is None:
        return ProductPreferenceState(
            source="defaults",
            updated_at=None,
            preferences=ProductPreferenceSettings(),
        )

    return ProductPreferenceState(
        source="sqlite",
        updated_at=setting.updated_at,
        preferences=ProductPreferenceSettings.model_validate(setting.value_json),
    )
