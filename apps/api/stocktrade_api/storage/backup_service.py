from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from pydantic import ValidationError

from ..schemas.backups import BackupManifest, BackupRestoreResult
from .artifact_service import DEFAULT_ARTIFACT_ROOT
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
        artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT,
        backup_root: str | Path = DEFAULT_BACKUP_ROOT,
        product_version: str,
        dispose_sqlite: Callable[[], None] | None = None,
    ):
        self.run_repository = run_repository
        self.sqlite_path = sqlite_path
        self.duckdb_path = duckdb_path
        self.artifact_root = Path(artifact_root).expanduser()
        if not self.artifact_root.is_absolute():
            self.artifact_root = ROOT / self.artifact_root
        self.backup_root = Path(backup_root).expanduser()
        if not self.backup_root.is_absolute():
            self.backup_root = ROOT / self.backup_root
        self.product_version = product_version
        self.dispose_sqlite = dispose_sqlite

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
            "artifacts": self.artifact_root.as_posix(),
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

            artifacts_dir = backup_dir / "artifacts"
            artifact_entries = self._backup_artifacts(artifacts_dir, backup_dir=backup_dir)
            if artifact_entries:
                files["artifacts"] = _relative_to_backup(backup_dir, artifacts_dir)
            artifacts_manifest = backup_dir / "artifacts_manifest.json"
            artifacts_manifest.write_text(
                json.dumps(
                    {
                        "artifact_root": self.artifact_root.as_posix(),
                        "artifacts": artifact_entries,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
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

    def restore_backup(self, backup_path: str | Path) -> BackupRestoreResult:
        sqlite_path = resolve_sqlite_file_path(self.sqlite_path)
        backup_dir = self._resolve_backup_dir(backup_path)
        manifest = self._load_manifest(backup_dir)
        sqlite_source = self._required_backup_file(backup_dir, manifest, "sqlite")
        duckdb_source = self._optional_backup_file(backup_dir, manifest, "duckdb")
        artifacts_manifest_path = self._optional_backup_file(backup_dir, manifest, "artifacts_manifest")
        artifacts_source = None
        if artifacts_manifest_path is not None:
            artifacts_source = self._optional_backup_dir(backup_dir, manifest, "artifacts")
        self._optional_backup_file(backup_dir, manifest, "migration_versions")
        duckdb_target = resolve_optional_file_path(self.duckdb_path)
        restored_at = utc_now()
        files_restored: dict[str, str] = {}
        missing_optional = list(manifest.missing_optional)

        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        if duckdb_target is not None:
            duckdb_target.parent.mkdir(parents=True, exist_ok=True)

        if self.dispose_sqlite is not None:
            self.dispose_sqlite()

        try:
            shutil.copy2(sqlite_source, sqlite_path)
            files_restored["sqlite"] = sqlite_path.as_posix()

            if duckdb_target is not None and duckdb_source is not None:
                shutil.copy2(duckdb_source, duckdb_target)
                files_restored["duckdb"] = duckdb_target.as_posix()
            elif duckdb_target is not None:
                if duckdb_target.exists():
                    duckdb_target.unlink()
                if "duckdb" not in missing_optional:
                    missing_optional.append("duckdb")

            if artifacts_manifest_path is not None:
                self._restore_artifacts(artifacts_source)
                files_restored["artifacts"] = self.artifact_root.as_posix()

            restore_run = self.run_repository.create_run(
                kind="restore",
                status="succeeded",
                summary={
                    "backup_id": manifest.backup_id,
                    "backup_path": backup_dir.as_posix(),
                    "files_restored": files_restored,
                    "missing_optional": sorted(missing_optional),
                },
            )
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(f"failed to restore backup: {exc}", status_code=500) from exc

        return BackupRestoreResult(
            restore_id=restore_run.id,
            run_id=restore_run.id,
            backup_id=manifest.backup_id,
            backup_path=backup_dir.as_posix(),
            restored_at=restored_at,
            files_restored=files_restored,
            missing_optional=sorted(missing_optional),
        )

    def _backup_sqlite(self, source: Path, target: Path) -> None:
        try:
            with closing(sqlite3.connect(source)) as source_connection:
                with closing(sqlite3.connect(target)) as target_connection:
                    source_connection.backup(target_connection)
        except sqlite3.Error as exc:
            raise BackupError(f"failed to backup SQLite database: {exc}", status_code=500) from exc

    def _backup_artifacts(self, target_root: Path, *, backup_dir: Path) -> list[dict[str, object]]:
        artifact_root = self.artifact_root.resolve(strict=False)
        if not artifact_root.exists():
            return []
        if not artifact_root.is_dir():
            raise BackupError(f"artifact root is not a directory: {artifact_root}", status_code=422)
        if _is_relative_to(backup_dir.resolve(strict=False), artifact_root):
            raise BackupError("backup directory must not be inside artifact root", status_code=422)

        entries: list[dict[str, object]] = []
        for source in sorted(artifact_root.rglob("*")):
            if source.is_symlink():
                raise BackupError("artifact backup does not support symlink files", status_code=422)
            if not source.is_file():
                continue
            relative = source.relative_to(artifact_root)
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append(
                {
                    "path": relative.as_posix(),
                    "backup_path": _relative_to_backup(backup_dir, target),
                    "size_bytes": source.stat().st_size,
                }
            )
        return entries

    def _restore_artifacts(self, backup_artifacts: Path | None) -> None:
        artifact_root = self.artifact_root.resolve(strict=False)
        _ensure_safe_artifact_root(artifact_root)

        if backup_artifacts is not None and not backup_artifacts.is_dir():
            raise BackupError("backup artifacts directory is not a directory", status_code=422)

        artifact_root.parent.mkdir(parents=True, exist_ok=True)
        staging_root = artifact_root.parent / f".{artifact_root.name}.restore-{uuid4().hex}"
        previous_root = artifact_root.parent / f".{artifact_root.name}.previous-{uuid4().hex}"
        staging_root.mkdir(parents=True, exist_ok=False)

        try:
            if backup_artifacts is not None:
                for source in sorted(backup_artifacts.rglob("*")):
                    if source.is_symlink():
                        raise BackupError("artifact restore does not support symlink files", status_code=422)
                    if not source.is_file():
                        continue
                    relative = source.relative_to(backup_artifacts)
                    target = staging_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

            if artifact_root.exists():
                if not artifact_root.is_dir():
                    raise BackupError(f"artifact root is not a directory: {artifact_root}", status_code=422)
                artifact_root.rename(previous_root)
            staging_root.rename(artifact_root)
        except Exception:
            if staging_root.exists():
                shutil.rmtree(staging_root)
            if previous_root.exists() and not artifact_root.exists():
                previous_root.rename(artifact_root)
            raise
        finally:
            if previous_root.exists():
                shutil.rmtree(previous_root)

    def _resolve_backup_dir(self, backup_path: str | Path) -> Path:
        if not str(backup_path).strip():
            raise BackupError("backup_path is required", status_code=422)
        backup_dir = Path(backup_path).expanduser()
        if not backup_dir.is_absolute():
            backup_dir = self.backup_root / backup_dir
        if not backup_dir.is_dir():
            raise BackupError(f"backup directory does not exist: {backup_dir}", status_code=404)
        return backup_dir

    def _load_manifest(self, backup_dir: Path) -> BackupManifest:
        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.is_file():
            raise BackupError("backup manifest.json does not exist", status_code=422)
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            return BackupManifest.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise BackupError(f"backup manifest.json is invalid: {exc}", status_code=422) from exc

    def _required_backup_file(self, backup_dir: Path, manifest: BackupManifest, key: str) -> Path:
        relative = manifest.files.get(key)
        if not relative:
            raise BackupError(f"backup manifest is missing required file: {key}", status_code=422)
        path = backup_dir / relative
        if not path.is_file():
            raise BackupError(f"backup file does not exist: {relative}", status_code=422)
        return path

    def _optional_backup_file(self, backup_dir: Path, manifest: BackupManifest, key: str) -> Path | None:
        relative = manifest.files.get(key)
        if not relative:
            return None
        path = backup_dir / relative
        if not path.is_file():
            raise BackupError(f"backup manifest references missing optional file: {relative}", status_code=422)
        return path

    def _optional_backup_dir(self, backup_dir: Path, manifest: BackupManifest, key: str) -> Path | None:
        relative = manifest.files.get(key)
        if not relative:
            return None
        path = backup_dir / relative
        if not path.exists():
            raise BackupError(f"backup manifest references missing optional directory: {relative}", status_code=422)
        if not path.is_dir():
            raise BackupError(f"backup manifest references non-directory optional path: {relative}", status_code=422)
        return path


def _relative_to_backup(backup_dir: Path, path: Path) -> str:
    return path.relative_to(backup_dir).as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _ensure_safe_artifact_root(path: Path) -> None:
    resolved = path.resolve(strict=False)
    forbidden = {
        Path(resolved.anchor).resolve(strict=False),
        ROOT.resolve(strict=False),
        ROOT.parent.resolve(strict=False),
        Path.home().resolve(strict=False),
    }
    if resolved in forbidden:
        raise BackupError(f"refusing to replace unsafe artifact root: {resolved}", status_code=422)
