from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .sqlite import DEFAULT_SQLITE_PATH, ROOT, sqlite_url

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def run_sqlite_migrations(path: str | Path = DEFAULT_SQLITE_PATH) -> None:
    if _is_memory_database(path):
        return

    _ensure_sqlite_parent(path)
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", sqlite_url(path))
    command.upgrade(config, "head")


def _is_memory_database(path: str | Path) -> bool:
    return str(path) in {":memory:", "sqlite:///:memory:"}


def _ensure_sqlite_parent(path: str | Path) -> None:
    if isinstance(path, str) and path.startswith("sqlite:"):
        return
    db_path = Path(path).expanduser()
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
