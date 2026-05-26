from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SQLITE_PATH = ROOT / "var" / "db" / "app.sqlite"


def sqlite_url(path: str | Path = DEFAULT_SQLITE_PATH) -> str:
    if isinstance(path, str) and path.startswith("sqlite:"):
        return path
    if str(path) == ":memory:":
        return "sqlite:///:memory:"

    db_path = Path(path).expanduser()
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return f"sqlite:///{db_path}"


def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_sqlite_engine(path: str | Path = DEFAULT_SQLITE_PATH, *, echo: bool = False) -> Engine:
    db_path = Path(path)
    if str(path) != ":memory:" and not str(path).startswith("sqlite:"):
        if not db_path.is_absolute():
            db_path = ROOT / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(sqlite_url(path), echo=echo, future=True)
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
