from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import (  # noqa: E402
    ArchiveRow,
    ArchiveSnapshot,
    Candidate,
    CandidateBatch,
    MigrationQuarantine,
    MigrationRun,
    Recommendation,
    Review,
    ReviewRun,
    Run,
)

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


class SQLiteProductStateTests(unittest.TestCase):
    def migrate(self, db_path: Path) -> None:
        command.upgrade(alembic_config(db_path), "head")

    def test_storage_imports_do_not_pull_heavy_legacy_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
import stocktrade_api.storage.sqlite
import stocktrade_api.storage.sqlite_models
print("pipeline.select_stock" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False"])

    def test_alembic_migration_creates_initial_product_state_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            self.migrate(db_path)
            engine = create_sqlite_engine(db_path)

            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            self.assertTrue(
                {
                    "alembic_version",
                    "runs",
                    "job_steps",
                    "job_events",
                    "artifacts",
                    "candidate_batches",
                    "candidates",
                    "review_runs",
                    "reviews",
                    "recommendations",
                    "archive_snapshots",
                    "archive_rows",
                    "migration_runs",
                    "migration_quarantine",
                }.issubset(tables)
            )

            run_columns = {column["name"] for column in inspector.get_columns("runs")}
            self.assertTrue(
                {"id", "kind", "status", "pick_date", "started_at", "finished_at", "summary_json"}.issubset(
                    run_columns
                )
            )

            job_step_columns = {column["name"] for column in inspector.get_columns("job_steps")}
            self.assertTrue({"id", "run_id", "name", "status", "error_json"}.issubset(job_step_columns))

            artifact_columns = {column["name"] for column in inspector.get_columns("artifacts")}
            self.assertTrue({"id", "run_id", "kind", "path", "content_type", "metadata_json"}.issubset(artifact_columns))

            candidate_uniques = {
                tuple(unique["column_names"])
                for unique in inspector.get_unique_constraints("candidates")
            }
            self.assertIn(("batch_id", "code", "strategy"), candidate_uniques)

            review_uniques = {
                tuple(unique["column_names"])
                for unique in inspector.get_unique_constraints("reviews")
            }
            self.assertIn(("review_run_id", "review_key"), review_uniques)

            recommendation_uniques = {
                tuple(unique["column_names"])
                for unique in inspector.get_unique_constraints("recommendations")
            }
            self.assertIn(("review_run_id", "rank"), recommendation_uniques)
            self.assertIn(("review_run_id", "review_key"), recommendation_uniques)

            archive_snapshot_uniques = {
                tuple(unique["column_names"])
                for unique in inspector.get_unique_constraints("archive_snapshots")
            }
            self.assertIn(("pick_date", "run_id"), archive_snapshot_uniques)

            archive_row_uniques = {
                tuple(unique["column_names"])
                for unique in inspector.get_unique_constraints("archive_rows")
            }
            self.assertIn(("snapshot_id", "review_key"), archive_row_uniques)

            archive_row_columns = {column["name"] for column in inspector.get_columns("archive_rows")}
            self.assertIn("chart_artifact_id", archive_row_columns)

            migration_run_columns = {column["name"] for column in inspector.get_columns("migration_runs")}
            self.assertTrue(
                {"id", "source_root", "status", "started_at", "finished_at", "report_json"}.issubset(
                    migration_run_columns
                )
            )

            migration_quarantine_columns = {column["name"] for column in inspector.get_columns("migration_quarantine")}
            self.assertTrue(
                {"id", "migration_run_id", "source_path", "reason", "payload_json"}.issubset(
                    migration_quarantine_columns
                )
            )
            engine.dispose()

    def test_candidate_identity_is_unique_per_batch_code_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            self.migrate(db_path)
            engine = create_sqlite_engine(db_path)
            session_factory = create_session_factory(engine)

            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one(), 1)

            with session_factory() as session:
                run = Run(id="run-1", kind="preselect", status="succeeded", pick_date="2026-05-27")
                batch = CandidateBatch(
                    id="batch-1",
                    run=run,
                    pick_date="2026-05-27",
                    source="legacy-golden-master",
                    strategy_counts_json={"B2": 1},
                )
                session.add(batch)
                session.add(
                    Candidate(
                        batch=batch,
                        code="000001.SZ",
                        strategy="B2",
                        pick_date="2026-05-27",
                        close=10.5,
                    )
                )
                session.commit()

                session.add(
                    Candidate(
                        batch_id="batch-1",
                        code="000001.SZ",
                        strategy="B2",
                        pick_date="2026-05-27",
                    )
                )
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()
            engine.dispose()

    def test_archive_identity_is_unique_per_snapshot_review_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            self.migrate(db_path)
            engine = create_sqlite_engine(db_path)
            session_factory = create_session_factory(engine)

            with session_factory() as session:
                archive_run = Run(id="run-archive-1", kind="archive", status="succeeded", pick_date="2026-05-27")
                snapshot = ArchiveSnapshot(
                    id="archive-1",
                    run=archive_run,
                    pick_date="2026-05-27",
                    candidate_count=1,
                    reviewed_count=0,
                    recommended_count=0,
                    strategy_counts_json={"b2": {"total": 1}},
                    executed_strategies_json=["b2"],
                )
                session.add(snapshot)
                session.add(
                    ArchiveRow(
                        snapshot=snapshot,
                        pick_date="2026-05-27",
                        run_id="run-archive-1",
                        code="000001",
                        strategy="b2",
                        review_key="000001_b2",
                        status="unreviewed",
                    )
                )
                session.commit()

                session.add(
                    ArchiveRow(
                        snapshot_id="archive-1",
                        pick_date="2026-05-27",
                        run_id="run-archive-1",
                        code="000001",
                        strategy="b2",
                        review_key="000001_b2",
                        status="reviewed",
                    )
                )
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()

                session.add(
                    ArchiveRow(
                        snapshot_id="archive-1",
                        pick_date="2026-05-27",
                        run_id="run-archive-1",
                        code="000002",
                        strategy="brick",
                        review_key="000002_brick",
                        status="invalid",
                    )
                )
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()
            engine.dispose()

    def test_review_identity_is_unique_per_review_run_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            self.migrate(db_path)
            engine = create_sqlite_engine(db_path)
            session_factory = create_session_factory(engine)

            with session_factory() as session:
                candidate_run = Run(id="run-preselect-1", kind="preselect", status="succeeded", pick_date="2026-05-27")
                review_run = Run(id="run-review-1", kind="review", status="succeeded", pick_date="2026-05-27")
                batch = CandidateBatch(
                    id="batch-1",
                    run=candidate_run,
                    pick_date="2026-05-27",
                    source="fixture",
                    strategy_counts_json={"b2": 1},
                )
                review_batch = ReviewRun(
                    id="review-batch-1",
                    run=review_run,
                    candidate_batch=batch,
                    pick_date="2026-05-27",
                    provider="gemini-cli",
                    status="succeeded",
                )
                session.add(review_batch)
                session.add(
                    Review(
                        review_run=review_batch,
                        code="000001",
                        strategy="b2",
                        review_key="000001_b2",
                        total_score=4.2,
                    )
                )
                session.commit()

                session.add(
                    Review(
                        review_run_id="review-batch-1",
                        code="000001",
                        strategy="b2",
                        review_key="000001_b2",
                    )
                )
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()

                review = session.query(Review).filter_by(review_key="000001_b2").one()
                session.add(
                    Recommendation(
                        review_run_id="review-batch-1",
                        review=review,
                        rank=1,
                        code="000001",
                        strategy="b2",
                        review_key="000001_b2",
                        total_score=4.2,
                    )
                )
                session.commit()

                session.add(
                    Recommendation(
                        review_run_id="review-batch-1",
                        rank=1,
                        code="000002",
                        strategy="brick",
                        review_key="000002_brick",
                    )
                )
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()
            engine.dispose()

    def test_migration_audit_rows_are_linked_to_legacy_import_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.sqlite"
            self.migrate(db_path)
            engine = create_sqlite_engine(db_path)
            session_factory = create_session_factory(engine)

            with session_factory() as session:
                run = Run(
                    id="migration-1",
                    kind="legacy_import",
                    status="succeeded",
                    summary_json={"dry_run": True},
                )
                migration_run = MigrationRun(
                    id="migration-1",
                    run=run,
                    source_root="data",
                    status="succeeded",
                    report_json={"dry_run": True, "data_root": "data"},
                )
                session.add(migration_run)
                session.add(
                    MigrationQuarantine(
                        migration_run=migration_run,
                        source_path="candidates/bad.json",
                        reason="malformed_json",
                        payload_json={
                            "section": "candidates",
                            "source_path": "candidates/bad.json",
                            "reason": "malformed_json",
                            "message": "invalid JSON",
                        },
                    )
                )
                session.commit()

                saved = session.get(MigrationRun, "migration-1")
                self.assertIsNotNone(saved)
                self.assertEqual(saved.run.kind, "legacy_import")
                self.assertEqual(saved.quarantine_rows[0].reason, "malformed_json")
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
