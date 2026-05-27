from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .sqlite_models import AppSetting

PRODUCT_PREFERENCES_KEY = "product_preferences"


def utc_now() -> datetime:
    return datetime.now(UTC)


class SettingsStorageUnavailableError(RuntimeError):
    pass


class SettingsRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def get_setting(self, key: str) -> AppSetting | None:
        try:
            with self.session_factory() as session:
                return session.get(AppSetting, key)
        except OperationalError as exc:
            raise SettingsStorageUnavailableError("settings storage is not migrated") from exc

    def upsert_setting(self, key: str, value: dict[str, Any]) -> AppSetting:
        try:
            with self.session_factory() as session:
                setting = session.get(AppSetting, key)
                if setting is None:
                    setting = AppSetting(key=key, value_json=value, updated_at=utc_now())
                    session.add(setting)
                else:
                    setting.value_json = value
                    setting.updated_at = utc_now()
                session.commit()
                session.refresh(setting)
                return setting
        except OperationalError as exc:
            raise SettingsStorageUnavailableError("settings storage is not migrated") from exc
