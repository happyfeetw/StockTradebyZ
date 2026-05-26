from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from ..schemas.backups import BackupManifest
from .run_repository import RunRepository
from .sqlite import ROOT


DEFAULT_BACKUP_ROOT = ROOT / "var" / "backups"


class BackupError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(UTC)


def resolve_sqlite_file_path(path: str | Path) -> Path:
    text = str(path)
    if text == ":memory:" or text == "sqlite:///:memory:":
        raise BackupError("SQLite backup requires a file-backed database", status_code=409)
    if text.startswith("sqlite:///"):
        text = text.removeprefix("sqlite:///")
    elif text.startswith("sqlite:"):
        raise BackupError("SQLite backup only supports local sqlite:/// file paths", status_code=409)

    db_path = Path(text).expanduser()
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return db_path


def resolve_optional_file_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    if str(path) == ":memory:":
        return None
    file_path = Path(path).expanduser()
    if not file_path.is_absolute():
        file_path = ROOT / file_path
    return file_path


class BackupService:
    def __init__(
        self,
        run_repository: RunRepository,
        *,
        sqlite_path: str | Path,
        duckdb_path: str | Path | None = None,
        backup_root: str | Path = DEFAULT_BACKUP_ROOT,
        product_version: str,
    ):
        self.run_repository = run_repository
        self.sqlite_path = sqlite_path
        self.duckdb_path = duckdb_path
        self.backup_root = Path(backup_root).expanduser()
        if not self.backup_root.is_absolute():
            self.backup_root = ROOT / self.backup_root
        self.product_version = product_version

    def create_backup(self) -> BackupManifest:
        sqlite_path = resolve_sqlite_file_path(self.sqlite_path)
        if not sqlite_path.exists():
            raise BackupError(f"SQLite database does not exist: {sqlite_path}", status_code=404)

        created_at = utc_now()
        run = self.run_repository.create_run(
            kind="backup",
            status="running",
            summary={"backup_root": self.backup_root.as_posix()},
        )
        backup_dir = self.backup_root / f"{created_at.strftime('%Y%m%dT%H%M%SZ')}-{run.id[:8]}"
        db_dir = backup_dir / "db"
        files: dict[str, str] = {}
        missing_optional: list[str] = []
        sources: dict[str, str | None] = {
            "sqlite": sqlite_path.as_posix(),
            "duckdb": None,
        }

        try:
            db_dir.mkdir(parents=True, exist_ok=False)
            sqlite_target = db_dir / "app.sqlite"
            self._backup_sqlite(sqlite_path, sqlite_target)
            files["sqlite"] = _relative_to_backup(backup_dir, sqlite_target)

            duckdb_path = resolve_optional_file_path(self.duckdb_path)
            if duckdb_path is not None:
                sources["duckdb"] = duckdb_path.as_posix()
                if duckdb_path.exists():
                    duckdb_target = db_dir / "analytics.duckdb"
                    shutil.copy2(duckdb_path, duckdb_target)
                    files["duckdb"] = _relative_to_backup(backup_dir, duckdb_target)
                else:
                    missing_optional.append("duckdb")
            else:
                missing_optional.append("duckdb")

            artifacts_manifest = backup_dir / "artifacts_manifest.json"
            artifacts_manifest.write_text(json.dumps({"artifacts": []}, indent=2), encoding="utf-8")
            files["artifacts_manifest"] = _relative_to_backup(backup_dir, artifacts_manifest)

            migration_versions = backup_dir / "migration_versions.json"
            migration_versions.write_text(json.dumps({"versions": {}}, indent=2), encoding="utf-8")
            files["migration_versions"] = _relative_to_backup(backup_dir, migration_versions)

            manifest = BackupManifest(
                backup_id=run.id,
                run_id=run.id,
                created_at=created_at,
                backup_path=backup_dir.as_posix(),
                product_version=self.product_version,
                sources=sources,
                files=files,
                missing_optional=missing_optional,
            )
            manifest_path = backup_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            files["manifest"] = _relative_to_backup(backup_dir, manifest_path)
            manifest = manifest.model_copy(update={"files": dict(sorted(files.items()))})
            manifest_path.write_text(
                json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            self.run_repository.transition_run(run.id, status="succeeded", summary=manifest.model_dump(mode="json"))
            return manifest
        except Exception as exc:
            self.run_repository.transition_run(
                run.id,
                status="failed",
                summary={"backup_path": backup_dir.as_posix(), "error": str(exc)},
            )
            raise

    def _backup_sqlite(self, source: Path, target: Path) -> None:
        try:
            with closing(sqlite3.connect(source)) as source_connection:
                with closing(sqlite3.connect(target)) as target_connection:
                    source_connection.backup(target_connection)
        except sqlite3.Error as exc:
            raise BackupError(f"failed to backup SQLite database: {exc}", status_code=500) from exc


def _relative_to_backup(backup_dir: Path, path: Path) -> str:
    return path.relative_to(backup_dir).as_posix()
