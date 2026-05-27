from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from stocktrade_api.storage.duckdb import (  # noqa: E402
    DuckDBAnalyticsWriter,
    DuckDBMigrationError,
    apply_migrations,
    connect_duckdb,
)
from stocktrade_api.storage.sqlite_models import (  # noqa: E402
    ArchiveRow,
    ArchiveSnapshot,
    Candidate,
    CandidateBatch,
    Review,
    ReviewRun,
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

    def test_analytics_writer_materializes_and_replaces_import_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "analytics.duckdb"
            writer = DuckDBAnalyticsWriter(db_path)
            batch = CandidateBatch(id="batch-1", run_id="run-1", pick_date="2026-05-26", source="legacy:candidates")
            writer.record_candidate_import(
                run_id="run-1",
                batch=batch,
                candidates=[
                    Candidate(
                        id=1,
                        batch_id="batch-1",
                        code="000010",
                        strategy="b2",
                        pick_date="2026-05-26",
                        close=10.5,
                        turnover_n=105.0,
                        extra_json={"signal": "breakout"},
                    )
                ],
            )
            writer.record_candidate_import(
                run_id="run-1",
                batch=batch,
                candidates=[
                    Candidate(
                        id=1,
                        batch_id="batch-1",
                        code="000010",
                        strategy="b2",
                        pick_date="2026-05-26",
                        close=10.8,
                        turnover_n=108.0,
                        extra_json={"signal": "updated"},
                    )
                ],
            )
            review_run = ReviewRun(id="review-run-1", run_id="run-2", pick_date="2026-05-26", provider="gemini-cli")
            writer.record_review_import(
                run_id="run-2",
                review_run=review_run,
                reviews=[
                    Review(
                        id=2,
                        review_run_id="review-run-1",
                        code="000010",
                        strategy="b2",
                        review_key="000010_b2",
                        verdict="PASS",
                        total_score=4.7,
                        payload_json={"comment": "clean breakout"},
                    )
                ],
            )
            writer.record_review_import(
                run_id="run-2",
                review_run=review_run,
                reviews=[
                    Review(
                        id=2,
                        review_run_id="review-run-1",
                        code="000010",
                        strategy="b2",
                        review_key="000010_b2",
                        verdict="PASS",
                        total_score=4.9,
                        payload_json={"comment": "updated review"},
                    )
                ],
            )
            snapshot = ArchiveSnapshot(id="snapshot-1", run_id="run-3", pick_date="2026-05-26")
            writer.record_archive_import(
                run_id="run-3",
                snapshot=snapshot,
                rows=[
                    ArchiveRow(
                        id=3,
                        snapshot_id="snapshot-1",
                        pick_date="2026-05-26",
                        run_id="run-3",
                        code="000010",
                        strategy="b2",
                        review_key="000010_b2",
                        status="recommended",
                        rank=1,
                        close=10.5,
                        extra_json={"signal": "breakout"},
                        review_payload_json={"comment": "clean breakout"},
                        chart_path="data/kline/2026-05-26/000010_day.png",
                        chart_artifact_id="artifact-chart-1",
                    ),
                    ArchiveRow(
                        id=4,
                        snapshot_id="snapshot-1",
                        pick_date="2026-05-26",
                        run_id="run-3",
                        code="000011",
                        strategy="brick",
                        review_key="000011_brick",
                        status="reviewed",
                    ),
                ],
            )
            writer.record_archive_import(
                run_id="run-3",
                snapshot=snapshot,
                rows=[
                    ArchiveRow(
                        id=3,
                        snapshot_id="snapshot-1",
                        pick_date="2026-05-26",
                        run_id="run-3",
                        code="000010",
                        strategy="b2",
                        review_key="000010_b2",
                        status="recommended",
                        rank=1,
                        close=10.8,
                        extra_json={"signal": "updated"},
                        review_payload_json={"comment": "updated review"},
                        chart_path="data/kline/2026-05-26/000010_day.png",
                        chart_artifact_id="artifact-chart-1",
                    ),
                ],
            )

            with connect_duckdb(db_path, read_only=True) as connection:
                candidate = connection.execute(
                    "SELECT close, extra_json FROM candidate_facts WHERE batch_id = 'batch-1'"
                ).fetchall()
                self.assertEqual(len(candidate), 1)
                self.assertEqual(candidate[0][0], 10.8)
                self.assertEqual(json.loads(candidate[0][1]), {"signal": "updated"})

                review = connection.execute(
                    "SELECT review_id, total_score, payload_json FROM review_facts WHERE review_run_id = 'review-run-1'"
                ).fetchone()
                self.assertEqual(review[:2], (2, 4.9))
                self.assertEqual(json.loads(review[2])["comment"], "updated review")

                archive_count = connection.execute(
                    "SELECT count(*) FROM archive_facts WHERE pick_date = DATE '2026-05-26' AND run_id = 'run-3'"
                ).fetchone()[0]
                self.assertEqual(archive_count, 1)
                archive = connection.execute(
                    """
                    SELECT chart_artifact_id, payload_json
                    FROM archive_facts
                    WHERE pick_date = DATE '2026-05-26' AND run_id = 'run-3' AND code = '000010'
                    """
                ).fetchone()
                self.assertEqual(archive[0], "artifact-chart-1")
                self.assertEqual(json.loads(archive[1])["archive_row_id"], 3)

                metrics = connection.execute(
                    """
                    SELECT strategy, total, reviewed, recommended, unreviewed
                    FROM strategy_run_metrics
                    WHERE pick_date = DATE '2026-05-26' AND run_id = 'run-3'
                    ORDER BY strategy
                    """
                ).fetchall()
                self.assertEqual(metrics, [("b2", 1, 0, 1, 0)])


if __name__ == "__main__":
    unittest.main()
