from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from apps.api.stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.storage.duckdb import apply_migrations, connect_duckdb  # noqa: E402


class SettingsStrategyAnalyticsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_endpoint_exposes_safe_local_product_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            app = create_app(
                sqlite_path=tmp / "app.sqlite",
                duckdb_path=tmp / "analytics.duckdb",
                artifact_root=tmp / "artifacts",
                backup_root=tmp / "backups",
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/settings")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["service"], "stocktrade-api")
        self.assertFalse(payload["simulated_trading_in_scope"])
        self.assertEqual(payload["stack"]["backend"], "FastAPI")
        self.assertTrue(payload["local_state"]["sqlite_path"].endswith("app.sqlite"))
        self.assertTrue(payload["local_state"]["duckdb_path"].endswith("analytics.duckdb"))
        config_keys = {item["key"] for item in payload["config_files"]}
        self.assertEqual(config_keys, {"preselect", "review_provider", "market_data", "dashboard"})
        self.assertNotIn("paper_trading", config_keys)
        for integration in payload["external_integrations"]:
            self.assertFalse(integration["secret_exposed"])
            self.assertNotIn("token", str(integration.get("value", "")).lower())

    async def test_strategy_metadata_endpoint_reports_configured_strategy_contracts(self) -> None:
        app = create_app(duckdb_path=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/strategies")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["candidate_identity"], ["code", "strategy"])
        strategies = {item["id"]: item for item in payload["strategies"]}
        self.assertEqual(set(strategies), {"b1", "b2", "brick"})
        self.assertTrue(strategies["b1"]["enabled_by_default"])
        self.assertFalse(strategies["b2"]["enabled_by_default"])
        self.assertEqual(strategies["brick"]["parity_status"], "product_owned_with_legacy_adapter")
        self.assertIn("min_return", strategies["b2"]["parameters"])
        self.assertIn("brick_growth_ratio", strategies["brick"]["parameters"])

    async def test_analytics_strategy_summary_reads_duckdb_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            duckdb_path = tmp / "analytics.duckdb"
            apply_migrations(duckdb_path)
            with connect_duckdb(duckdb_path) as connection:
                connection.executemany(
                    """
                    INSERT INTO strategy_run_metrics (
                        pick_date, run_id, strategy, total, reviewed, recommended, unreviewed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("2026-05-26", "run-1", "b2", 2, 1, 1, 0),
                        ("2026-05-26", "run-2", "brick", 3, 1, 1, 1),
                        ("2026-05-25", "run-0", "b1", 1, 0, 0, 1),
                    ],
                )

            app = create_app(sqlite_path=tmp / "app.sqlite", duckdb_path=duckdb_path)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/analytics/strategy-summary", params={"pick_date": "2026-05-26"})
                filtered = await client.get("/api/analytics/strategy-summary", params={"strategy": "b2"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(payload["totals"]["total"], 5)
        self.assertEqual(payload["totals"]["reviewed"], 2)
        self.assertEqual(payload["totals"]["recommended"], 2)
        self.assertEqual(payload["totals"]["unreviewed"], 1)
        self.assertEqual(payload["totals"]["reviewed_rate"], 0.8)
        self.assertEqual(payload["totals"]["recommended_rate"], 0.4)
        self.assertEqual(payload["totals"]["strategies"], ["b2", "brick"])
        self.assertEqual(payload["filters"], {"pick_date": "2026-05-26", "run_id": None, "strategy": None})

        self.assertEqual(filtered.status_code, 200)
        filtered_payload = filtered.json()
        self.assertEqual(filtered_payload["totals"]["strategies"], ["b2"])
        self.assertEqual(filtered_payload["totals"]["total"], 2)

    async def test_analytics_strategy_summary_returns_503_when_duckdb_is_disabled(self) -> None:
        app = create_app(duckdb_path=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/analytics/strategy-summary")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "analytics database is not configured")


if __name__ == "__main__":
    unittest.main()
