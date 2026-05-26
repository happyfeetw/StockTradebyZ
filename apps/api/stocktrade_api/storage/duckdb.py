from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DUCKDB_PATH = ROOT / "var" / "db" / "analytics.duckdb"
DEFAULT_MIGRATIONS_DIR = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "duckdb" / "versions"


class DuckDBMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DuckDBMigration:
    version: str
    path: Path


def resolve_duckdb_path(path: str | Path = DEFAULT_DUCKDB_PATH) -> Path | str:
    if str(path) == ":memory:":
        return ":memory:"

    db_path = Path(path).expanduser()
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return db_path


def connect_duckdb(path: str | Path = DEFAULT_DUCKDB_PATH, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    resolved = resolve_duckdb_path(path)
    if resolved != ":memory:":
        assert isinstance(resolved, Path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(resolved), read_only=read_only)
    return duckdb.connect(resolved, read_only=read_only)


def list_migration_files(migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR) -> list[DuckDBMigration]:
    migration_root = Path(migrations_dir)
    return [
        DuckDBMigration(version=path.stem, path=path)
        for path in sorted(migration_root.glob("*.sql"))
        if path.is_file()
    ]


def ensure_schema_version_table(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS duckdb_schema_versions (
            version VARCHAR PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT current_timestamp
        )
        """
    )


def applied_versions(connection: duckdb.DuckDBPyConnection) -> set[str]:
    ensure_schema_version_table(connection)
    rows = connection.execute("SELECT version FROM duckdb_schema_versions").fetchall()
    return {str(row[0]) for row in rows}


def apply_migrations(
    path: str | Path = DEFAULT_DUCKDB_PATH,
    *,
    migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR,
) -> list[str]:
    applied: list[str] = []
    with connect_duckdb(path) as connection:
        ensure_schema_version_table(connection)
        existing_versions = applied_versions(connection)
        for migration in list_migration_files(migrations_dir):
            if migration.version in existing_versions:
                continue

            sql = migration.path.read_text(encoding="utf-8")
            try:
                connection.execute("BEGIN TRANSACTION")
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO duckdb_schema_versions (version) VALUES (?)",
                    [migration.version],
                )
                connection.execute("COMMIT")
            except Exception as exc:
                connection.execute("ROLLBACK")
                raise DuckDBMigrationError(f"failed to apply DuckDB migration {migration.version}") from exc

            existing_versions.add(migration.version)
            applied.append(migration.version)
    return applied
