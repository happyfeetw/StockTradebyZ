from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "src"))

from archive_results import review_key as legacy_archive_review_key  # noqa: E402
from stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.services.legacy_import import (  # noqa: E402
    LegacyCandidateImportError,
    legacy_review_key,
    load_legacy_candidate_import_plan,
    scan_legacy_import_dry_run,
)

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


def build_legacy_fixture(root: Path) -> Path:
    data_root = root / "data"
    write_json(
        data_root / "candidates" / "candidates_2026-05-27.json",
        {
            "run_date": "2026-05-27",
            "pick_date": "2026-05-27",
            "candidates": [
                {"code": "000001", "date": "2026-05-27", "strategy": "b2", "close": 10.0, "turnover_n": 100.0},
                {"code": "000001", "date": "2026-05-27", "strategy": "b2", "close": 10.2, "turnover_n": 101.0},
                {"code": "000002", "date": "2026-05-27", "close": 9.0, "turnover_n": 90.0},
                {
                    "code": "000003",
                    "date": "2026-05-27",
                    "strategy": "brick",
                    "close": 8.0,
                    "turnover_n": 80.0,
                    "brick_growth": 1.4,
                },
            ],
        },
    )
    write_json(
        data_root / "candidates" / "candidates_latest.json",
        {"run_date": "2026-05-27", "pick_date": "2026-05-27", "candidates": []},
    )
    (data_root / "candidates" / "candidates_2026-05-28.json").write_text("{bad json", encoding="utf-8")
    (data_root / "candidates" / "README.txt").write_text("not imported", encoding="utf-8")

    review_dir = data_root / "review" / "2026-05-27"
    write_json(
        review_dir / "000001_b2.json",
        {"code": "000001", "strategy": "b2", "review_key": "000001_b2", "total_score": 4.5},
    )
    write_json(review_dir / "000003.json", {"code": "000003", "strategy": "brick", "total_score": 3.0})
    write_json(
        review_dir / "000004_bad.json",
        {"code": "000004", "strategy": "b2", "review_key": "000004_brick", "total_score": 3.5},
    )
    write_json(
        review_dir / "suggestion.json",
        {
            "date": "2026-05-27",
            "recommendations": [
                {"rank": 1, "code": "000001", "strategy": "b2", "review_key": "000001_b2", "total_score": 4.5},
                {"rank": 2, "code": "000003", "strategy": "brick", "review_key": "000003_b2", "total_score": 4.0},
            ],
        },
    )
    (review_dir / "README.md").write_text("not imported", encoding="utf-8")

    write_json(data_root / "history" / "index.json", {"dates": [{"date": "2026-05-27"}, {}]})
    write_json(
        data_root / "history" / "2026-05-27" / "summary.json",
        {"date": "2026-05-27", "run_id": "run-1", "candidate_count": 4},
    )
    write_json(
        data_root / "history" / "2026-05-27" / "all.json",
        [
            {"code": "000001", "strategy": "b2", "review_key": "000001_b2", "status": "recommended"},
            {"code": "000001", "strategy": "b2", "review_key": "000001_b2", "status": "reviewed"},
            {"code": "000003", "strategy": "brick", "review_key": "000003_b2", "status": "reviewed"},
            {"strategy": "b2", "review_key": "missing_b2", "status": "unreviewed"},
        ],
    )
    write_json(data_root / "history" / "2026-05-27" / "other.json", {"ignored": True})
    (data_root / "history" / "2026-05-27" / "notes.txt").write_text("not imported", encoding="utf-8")
    return data_root


def build_valid_candidate_import_fixture(root: Path) -> Path:
    data_root = root / "data"
    write_json(
        data_root / "candidates" / "candidates_2026-05-26.json",
        {
            "run_date": "2026-05-27",
            "pick_date": "2026-05-26",
            "candidates": [
                {
                    "code": "000010",
                    "date": "2026-05-26",
                    "strategy": "b2",
                    "close": 10.5,
                    "turnover_n": 105.0,
                    "signal": "breakout",
                },
                {
                    "code": "000011",
                    "date": "2026-05-26",
                    "strategy": "brick",
                    "close": "8.2",
                    "turnover_n": "82.0",
                    "brick_growth": "1.25",
                },
            ],
        },
    )
    return data_root


class LegacyImportDryRunTests(unittest.TestCase):
    def test_dry_run_returns_deterministic_structured_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = build_legacy_fixture(Path(tmpdir))

            report = scan_legacy_import_dry_run(data_root)
            payload = report.model_dump()

            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["sections"]["candidates"]["files_seen"], 4)
            self.assertEqual(payload["sections"]["candidates"]["files_valid"], 2)
            self.assertEqual(payload["sections"]["candidates"]["records_seen"], 4)
            self.assertEqual(payload["sections"]["candidates"]["records_valid"], 2)
            self.assertEqual(payload["sections"]["reviews"]["by_kind"], {"review": 3, "suggestion": 1})
            self.assertEqual(payload["sections"]["history"]["by_kind"], {"all": 1, "index": 1, "summary": 1})
            self.assertEqual(payload["totals"]["files_seen"], 14)
            self.assertEqual(payload["totals"]["files_valid"], 9)
            self.assertEqual(payload["totals"]["records_seen"], 16)
            self.assertEqual(payload["totals"]["records_valid"], 8)

    def test_dry_run_reports_malformed_unsupported_and_identity_problems(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = build_legacy_fixture(Path(tmpdir))

            report = scan_legacy_import_dry_run(data_root)
            warning_reasons = {issue.reason for issue in report.warnings}
            quarantine_reasons = {issue.reason for issue in report.quarantine}

            self.assertIn("unsupported_file", warning_reasons)
            self.assertIn("unsupported_history_file", warning_reasons)
            self.assertIn("missing_review_key", warning_reasons)
            self.assertIn("malformed_json", quarantine_reasons)
            self.assertIn("duplicate_candidate_identity", quarantine_reasons)
            self.assertIn("missing_candidate_fields", quarantine_reasons)
            self.assertIn("review_key_mismatch", quarantine_reasons)
            self.assertIn("duplicate_history_review_key", quarantine_reasons)
            self.assertEqual(report.totals.warning_count, 5)
            self.assertEqual(report.totals.quarantine_count, 8)

    def test_review_key_semantics_match_legacy_archive_contract(self) -> None:
        cases = [
            ("000001", "b2"),
            ("000001", "brick"),
            ("000001", ""),
            ("000001", "B2 strategy"),
            ("600000.SH", "b1+b2"),
        ]
        for code, strategy in cases:
            self.assertEqual(legacy_review_key(code, strategy), legacy_archive_review_key(code, strategy))

    def test_candidate_import_plan_loads_one_dated_candidate_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = build_valid_candidate_import_fixture(Path(tmpdir))

            plan = load_legacy_candidate_import_plan(data_root, "2026-05-26")

            self.assertEqual(plan.source_path, "candidates/candidates_2026-05-26.json")
            self.assertEqual(plan.pick_date, "2026-05-26")
            self.assertEqual(plan.strategy_counts, {"b2": 1, "brick": 1})
            self.assertEqual([(candidate.code, candidate.strategy) for candidate in plan.candidates], [("000010", "b2"), ("000011", "brick")])
            self.assertEqual(plan.candidates[0].extra["signal"], "breakout")
            self.assertEqual(plan.candidates[1].brick_growth, 1.25)

    def test_candidate_import_plan_rejects_duplicate_identity_before_db_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = build_legacy_fixture(Path(tmpdir))

            with self.assertRaisesRegex(LegacyCandidateImportError, "duplicate candidate identity"):
                load_legacy_candidate_import_plan(data_root, "2026-05-27")

    def test_service_and_route_imports_do_not_pull_heavy_legacy_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
import stocktrade_api.services.legacy_import
import stocktrade_api.routes.migrations
print("pipeline.select_stock" in sys.modules)
print("agent.gemini_cli_review" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False"])


class LegacyImportDryRunApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_legacy_api_accepts_dry_run_and_rejects_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data_root = build_legacy_fixture(tmp)
            db_path = tmp / "app.sqlite"
            migrate_sqlite(db_path)
            app = create_app(sqlite_path=db_path)
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                dry_run = await client.post(
                    "/api/migrations/import-legacy",
                    json={"dry_run": True, "data_root": str(data_root)},
                )
                self.assertEqual(dry_run.status_code, 200)
                dry_run_payload = dry_run.json()
                migration_id = dry_run_payload["migration_id"]
                self.assertEqual(dry_run_payload["totals"]["quarantine_count"], 8)
                self.assertIsNotNone(migration_id)

                persisted = await client.get(f"/api/migrations/{migration_id}")
                self.assertEqual(persisted.status_code, 200)
                persisted_payload = persisted.json()
                self.assertEqual(persisted_payload["id"], migration_id)
                self.assertEqual(persisted_payload["source_root"], str(data_root))
                self.assertEqual(persisted_payload["status"], "succeeded")
                self.assertEqual(persisted_payload["report"]["migration_id"], migration_id)
                self.assertEqual(persisted_payload["report"]["totals"]["quarantine_count"], 8)
                self.assertEqual(len(persisted_payload["quarantine"]), 8)
                self.assertIn(
                    "duplicate_candidate_identity",
                    {row["reason"] for row in persisted_payload["quarantine"]},
                )

                runs = await client.get("/api/runs")
                self.assertEqual(runs.status_code, 200)
                legacy_runs = [run for run in runs.json()["runs"] if run["id"] == migration_id]
                self.assertEqual(len(legacy_runs), 1)
                self.assertEqual(legacy_runs[0]["kind"], "legacy_import")

                write_attempt = await client.post(
                    "/api/migrations/import-legacy",
                    json={"dry_run": False, "data_root": str(data_root)},
                )
                self.assertEqual(write_attempt.status_code, 409)

                missing = await client.get("/api/migrations/not-found")
                self.assertEqual(missing.status_code, 404)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

    async def test_import_legacy_api_imports_candidate_batch_when_scope_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data_root = build_valid_candidate_import_fixture(tmp)
            db_path = tmp / "app.sqlite"
            migrate_sqlite(db_path)
            app = create_app(sqlite_path=db_path)
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                imported = await client.post(
                    "/api/migrations/import-legacy",
                    json={
                        "dry_run": False,
                        "data_root": str(data_root),
                        "scope": "candidates",
                        "pick_date": "2026-05-26",
                    },
                )
                self.assertEqual(imported.status_code, 200)
                imported_payload = imported.json()
                migration_id = imported_payload["migration_id"]
                import_summary = imported_payload["import_summary"]
                self.assertFalse(imported_payload["dry_run"])
                self.assertEqual(import_summary["run_id"], migration_id)
                self.assertEqual(import_summary["pick_date"], "2026-05-26")
                self.assertEqual(import_summary["source_file"], "candidates/candidates_2026-05-26.json")
                self.assertEqual(import_summary["candidates_imported"], 2)
                self.assertEqual(import_summary["strategy_counts"], {"b2": 1, "brick": 1})

                candidates = await client.get("/api/candidates", params={"run_id": migration_id})
                self.assertEqual(candidates.status_code, 200)
                candidates_payload = candidates.json()
                self.assertEqual(candidates_payload["total"], 2)
                first = candidates_payload["candidates"][0]
                self.assertEqual((first["code"], first["strategy"], first["pick_date"]), ("000010", "b2", "2026-05-26"))
                self.assertEqual(first["batch"]["source"], "legacy:candidates")
                self.assertEqual(first["batch"]["strategy_counts"], {"b2": 1, "brick": 1})
                self.assertEqual(first["extra"]["signal"], "breakout")

                persisted = await client.get(f"/api/migrations/{migration_id}")
                self.assertEqual(persisted.status_code, 200)
                self.assertEqual(persisted.json()["report"]["import_summary"]["batch_id"], import_summary["batch_id"])

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

    async def test_import_legacy_api_rejects_invalid_candidate_import_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data_root = build_legacy_fixture(tmp)
            db_path = tmp / "app.sqlite"
            migrate_sqlite(db_path)
            app = create_app(sqlite_path=db_path)
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                imported = await client.post(
                    "/api/migrations/import-legacy",
                    json={
                        "dry_run": False,
                        "data_root": str(data_root),
                        "scope": "candidates",
                        "pick_date": "2026-05-27",
                    },
                )
                self.assertEqual(imported.status_code, 422)

                runs = await client.get("/api/runs")
                self.assertEqual(runs.status_code, 200)
                self.assertEqual(runs.json()["runs"], [])

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
