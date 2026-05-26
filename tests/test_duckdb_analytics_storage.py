from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from stocktrade_api.storage.duckdb import (  # noqa: E402
    DuckDBMigrationError,
    apply_migrations,
    connect_duckdb,
)


class DuckDBAnalyticsStorageTests(unittest.TestCase):
    def test_storage_imports_do_not_pull_heavy_legacy_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
import stocktrade_api.storage.duckdb
print("pipeline.select_stock" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False"])

    def test_migration_runner_applies_ordered_sql_files_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            migrations_dir = tmp / "migrations"
            migrations_dir.mkdir()
            (migrations_dir / "0001_first.sql").write_text("CREATE TABLE first_table (id INTEGER);", encoding="utf-8")
            (migrations_dir / "0002_second.sql").write_text("CREATE TABLE second_table (id INTEGER);", encoding="utf-8")
            db_path = tmp / "analytics.duckdb"

            self.assertEqual(
                apply_migrations(db_path, migrations_dir=migrations_dir),
                ["0001_first", "0002_second"],
            )
            self.assertEqual(apply_migrations(db_path, migrations_dir=migrations_dir), [])

            with connect_duckdb(db_path, read_only=True) as connection:
                versions = connection.execute(
                    "SELECT version FROM duckdb_schema_versions ORDER BY version"
                ).fetchall()
                self.assertEqual(versions, [("0001_first",), ("0002_second",)])

    def test_initial_schema_can_be_created_against_temp_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "analytics.duckdb"

            self.assertEqual(apply_migrations(db_path), ["0001_initial_analytics"])

            with connect_duckdb(db_path, read_only=True) as connection:
                tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
                self.assertTrue(
                    {
                        "duckdb_schema_versions",
                        "market_daily_bars",
                        "candidate_facts",
                        "review_facts",
                        "archive_facts",
                        "strategy_run_metrics",
                        "analytics_snapshots",
                    }.issubset(tables)
                )

                candidate_columns = {row[0] for row in connection.execute("DESCRIBE candidate_facts").fetchall()}
                self.assertTrue(
                    {
                        "candidate_id",
                        "pick_date",
                        "run_id",
                        "batch_id",
                        "code",
                        "strategy",
                        "close",
                        "turnover_n",
                        "extra_json",
                    }.issubset(candidate_columns)
                )

                review_columns = {row[0] for row in connection.execute("DESCRIBE review_facts").fetchall()}
                self.assertTrue(
                    {"review_id", "review_run_id", "pick_date", "run_id", "code", "strategy", "review_key"}.issubset(
                        review_columns
                    )
                )

    def test_failed_migration_is_not_marked_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            migrations_dir = tmp / "migrations"
            migrations_dir.mkdir()
            (migrations_dir / "0001_bad.sql").write_text("CREATE TABLE broken (", encoding="utf-8")
            db_path = tmp / "analytics.duckdb"

            with self.assertRaises(DuckDBMigrationError):
                apply_migrations(db_path, migrations_dir=migrations_dir)

            with connect_duckdb(db_path, read_only=True) as connection:
                count = connection.execute("SELECT count(*) FROM duckdb_schema_versions").fetchone()[0]
                self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
