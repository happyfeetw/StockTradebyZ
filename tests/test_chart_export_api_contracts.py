from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.schemas.charts import ChartExportRunCreateResponse  # noqa: E402
from stocktrade_api.storage.sqlite import create_session_factory, create_sqlite_engine  # noqa: E402
from stocktrade_api.storage.sqlite_models import Candidate, CandidateBatch, Run  # noqa: E402

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


def seed_historical_batch(db_path: Path) -> None:
    engine = create_sqlite_engine(db_path)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        run = Run(id="run-preselect-history", kind="preselect", status="succeeded", pick_date="2026-05-25")
        batch = CandidateBatch(
            id="batch-history",
            run=run,
            pick_date="2026-05-25",
            source="fixture",
            strategy_counts_json={"b2": 2, "brick": 1},
        )
        session.add_all(
            [
                Candidate(
                    batch=batch,
                    code="000001",
                    strategy="b2",
                    pick_date="2026-05-25",
                    close=10.1,
                    turnover_n=100.0,
                ),
                Candidate(
                    batch=batch,
                    code="000001",
                    strategy="brick",
                    pick_date="2026-05-25",
                    close=10.1,
                    brick_growth=1.2,
                ),
                Candidate(
                    batch=batch,
                    code="000002",
                    strategy="b2",
                    pick_date="2026-05-25",
                    close=8.6,
                    turnover_n=88.0,
                ),
            ]
        )
        session.commit()
    engine.dispose()


def write_raw_csv(raw_dir: Path, code: str = "000001") -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{code}.csv").write_text(
        "\n".join(
            [
                "date,open,high,low,close,volume",
                "2026-05-21,9.7,10.0,9.5,9.8,1000",
                "2026-05-22,9.8,10.4,9.7,10.2,1200",
                "2026-05-25,10.2,10.8,10.1,10.6,1600",
            ]
        ),
        encoding="utf-8",
    )


class ChartExportApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_chart_export_generates_product_artifacts_from_selected_historical_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "app.sqlite"
            raw_dir = tmp / "raw"
            artifact_root = tmp / "artifacts"
            migrate_sqlite(db_path)
            seed_historical_batch(db_path)
            write_raw_csv(raw_dir)
            app = create_app(sqlite_path=db_path, duckdb_path=None, artifact_root=artifact_root)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/runs/chart-export",
                    json={
                        "candidate_batch_id": "batch-history",
                        "raw_dir": str(raw_dir),
                        "bars": 3,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                parsed = ChartExportRunCreateResponse.model_validate(payload)

                self.assertEqual(parsed.run.kind, "chart_export")
                self.assertEqual(parsed.run.status, "succeeded")
                self.assertEqual(parsed.run.pick_date, "2026-05-25")
                self.assertEqual(parsed.run.summary["candidate_batch_id"], "batch-history")
                self.assertEqual(parsed.run.summary["candidate_count"], 3)
                self.assertEqual(parsed.run.summary["unique_code_count"], 2)
                self.assertEqual(parsed.run.summary["exported_count"], 3)
                self.assertEqual(parsed.run.summary["strategy_chart_count"], 2)
                self.assertEqual(parsed.run.summary["compatibility_chart_count"], 1)
                self.assertEqual(parsed.run.summary["skipped_count"], 1)
                self.assertEqual(len(parsed.artifacts), 3)

                artifacts_by_scope = {
                    (
                        artifact.metadata.get("artifact_scope"),
                        artifact.metadata.get("strategy", ""),
                    ): artifact
                    for artifact in parsed.artifacts
                }
                compatibility = artifacts_by_scope[("code", "")]
                b2_chart = artifacts_by_scope[("strategy", "b2")]
                brick_chart = artifacts_by_scope[("strategy", "brick")]
                self.assertEqual(compatibility.kind, "chart")
                self.assertEqual(compatibility.content_type, "image/jpeg")
                self.assertEqual(compatibility.metadata["source"], "product:chart_export")
                self.assertEqual(compatibility.metadata["candidate_batch_id"], "batch-history")
                self.assertEqual(compatibility.metadata["pick_date"], "2026-05-25")
                self.assertEqual(compatibility.metadata["code"], "000001")
                self.assertEqual(compatibility.metadata["strategies"], ["b2", "brick"])
                self.assertIsNone(compatibility.metadata.get("review_key"))
                self.assertEqual(b2_chart.metadata["review_key"], "000001_b2")
                self.assertEqual(brick_chart.metadata["review_key"], "000001_brick")
                self.assertFalse(b2_chart.metadata["contains_brick_panel"])
                self.assertTrue(brick_chart.metadata["contains_brick_panel"])
                self.assertTrue((artifact_root / compatibility.path).is_file())
                self.assertTrue((artifact_root / b2_chart.path).is_file())
                self.assertTrue((artifact_root / brick_chart.path).is_file())
                self.assertTrue(b2_chart.path.endswith("/000001_b2_day.jpg"))
                self.assertTrue(brick_chart.path.endswith("/000001_brick_day.jpg"))
                from PIL import Image, ImageChops

                with Image.open(artifact_root / b2_chart.path).convert("RGB") as b2_image:
                    self.assertEqual(b2_image.size, (1400, 760))
                    self.assertIsNotNone(
                        ImageChops.difference(b2_image, Image.new("RGB", b2_image.size, "white")).getbbox()
                    )
                with Image.open(artifact_root / brick_chart.path).convert("RGB") as brick_image:
                    self.assertEqual(brick_image.size, (1400, 900))
                    brick_panel = brick_image.crop((74, 486, 1372, 610))
                    self.assertIsNotNone(
                        ImageChops.difference(
                            brick_panel,
                            Image.new("RGB", brick_panel.size, "white"),
                        ).getbbox()
                    )

                served = await client.get(f"/api/artifacts/{brick_chart.id}")
                self.assertEqual(served.status_code, 200)
                self.assertTrue(served.headers["content-type"].startswith("image/jpeg"))
                self.assertTrue(served.content.startswith(b"\xff\xd8"))

                run_detail = await client.get(f"/api/runs/{parsed.run.id}")
                self.assertEqual(run_detail.status_code, 200)
                self.assertEqual(len(run_detail.json()["artifacts"]), 3)
                self.assertIn(
                    "Chart export job generated 3 artifacts",
                    [event["message"] for event in run_detail.json()["events"]],
                )

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

    async def test_chart_export_rejects_missing_batch_and_empty_chart_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "app.sqlite"
            migrate_sqlite(db_path)
            seed_historical_batch(db_path)
            app = create_app(sqlite_path=db_path, duckdb_path=None, artifact_root=tmp / "artifacts")

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                missing_batch = await client.post(
                    "/api/runs/chart-export",
                    json={"candidate_batch_id": "missing-batch", "raw_dir": str(tmp / "raw")},
                )
                self.assertEqual(missing_batch.status_code, 404)

                no_raw = await client.post(
                    "/api/runs/chart-export",
                    json={"candidate_batch_id": "batch-history", "raw_dir": str(tmp / "raw")},
                )
                self.assertEqual(no_raw.status_code, 400)
                self.assertEqual(no_raw.json()["detail"], "no charts were exported for the selected candidate batch")

                runs = await client.get("/api/runs")
                self.assertEqual(runs.status_code, 200)
                failed = next(
                    run
                    for run in runs.json()["runs"]
                    if run["kind"] == "chart_export" and run["summary"]["type"] == "ChartExportValidationError"
                )
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["summary"]["type"], "ChartExportValidationError")

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()


if __name__ == "__main__":
    unittest.main()
