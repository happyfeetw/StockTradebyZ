from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.storage.duckdb import apply_migrations  # noqa: E402

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


class BackupApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_backup_api_copies_sqlite_duckdb_and_records_backup_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            sqlite_path = tmp / "app.sqlite"
            duckdb_path = tmp / "analytics.duckdb"
            backup_root = tmp / "backups"
            migrate_sqlite(sqlite_path)
            apply_migrations(duckdb_path)
            app = create_app(sqlite_path=sqlite_path, duckdb_path=duckdb_path, backup_root=backup_root)
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/api/backups")
                self.assertEqual(response.status_code, 200)
                payload = response.json()["backup"]
                backup_path = Path(payload["backup_path"])
                self.assertTrue(backup_path.is_dir())
                self.assertEqual(payload["backup_id"], payload["run_id"])
                self.assertEqual(payload["product_version"], "0.1.0")
                self.assertEqual(payload["missing_optional"], [])
                self.assertEqual(payload["files"]["sqlite"], "db/app.sqlite")
                self.assertEqual(payload["files"]["duckdb"], "db/analytics.duckdb")
                self.assertEqual(payload["files"]["manifest"], "manifest.json")
                self.assertTrue((backup_path / "db" / "app.sqlite").is_file())
                self.assertTrue((backup_path / "db" / "analytics.duckdb").is_file())
                self.assertTrue((backup_path / "artifacts_manifest.json").is_file())
                self.assertTrue((backup_path / "migration_versions.json").is_file())

                manifest = json.loads((backup_path / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["backup_id"], payload["backup_id"])
                self.assertEqual(manifest["sources"]["sqlite"], sqlite_path.as_posix())
                self.assertEqual(manifest["sources"]["duckdb"], duckdb_path.as_posix())

                with closing(sqlite3.connect(backup_path / "db" / "app.sqlite")) as connection:
                    count = connection.execute(
                        "SELECT count(*) FROM runs WHERE id = ? AND kind = 'backup'",
                        [payload["run_id"]],
                    ).fetchone()[0]
                    self.assertEqual(count, 1)

                runs = await client.get("/api/runs")
                self.assertEqual(runs.status_code, 200)
                backup_runs = [run for run in runs.json()["runs"] if run["id"] == payload["run_id"]]
                self.assertEqual(len(backup_runs), 1)
                self.assertEqual(backup_runs[0]["kind"], "backup")
                self.assertEqual(backup_runs[0]["status"], "succeeded")

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

    async def test_backup_api_records_missing_optional_duckdb(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            sqlite_path = tmp / "app.sqlite"
            missing_duckdb_path = tmp / "missing.duckdb"
            migrate_sqlite(sqlite_path)
            app = create_app(sqlite_path=sqlite_path, duckdb_path=missing_duckdb_path, backup_root=tmp / "backups")
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/api/backups")
                self.assertEqual(response.status_code, 200)
                payload = response.json()["backup"]
                self.assertEqual(payload["missing_optional"], ["duckdb"])
                self.assertNotIn("duckdb", payload["files"])
                self.assertEqual(payload["sources"]["duckdb"], missing_duckdb_path.as_posix())

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

    async def test_backup_api_rejects_in_memory_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(sqlite_path=":memory:", duckdb_path=None, backup_root=Path(tmpdir) / "backups")
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/api/backups")
                self.assertEqual(response.status_code, 409)
                self.assertIn("file-backed", response.json()["detail"])

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


class BackupImportHygieneTests(unittest.TestCase):
    def test_backup_imports_do_not_pull_heavy_legacy_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
import stocktrade_api.routes.backups
import stocktrade_api.storage.backup_service
print("pipeline.select_stock" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False"])


if __name__ == "__main__":
    unittest.main()
