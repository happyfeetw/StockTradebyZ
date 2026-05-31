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
from stocktrade.domain.metadata import PRODUCT_STACK  # noqa: E402


class ApiScaffoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_endpoint_returns_confirmed_stack(self) -> None:
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "stocktrade-api",
                "version": "0.1.0",
                "stack": PRODUCT_STACK,
                "simulated_trading_in_scope": False,
            },
        )

    def test_app_import_does_not_pull_legacy_pipeline(self) -> None:
        modules = set(sys.modules)
        self.assertNotIn("pipeline.select_stock", modules)
        self.assertNotIn("agent.gemini_cli_review", modules)

    async def test_create_app_auto_migrates_sqlite_for_product_state_apis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_dir = tmp / "nested" / "state"
            app = create_app(
                sqlite_path=state_dir / "app.sqlite",
                duckdb_path=state_dir / "analytics.duckdb",
                artifact_root=state_dir / "artifacts",
                backup_root=state_dir / "backups",
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                responses = [
                    await client.get("/api/runs"),
                    await client.get("/api/candidate-batches"),
                    await client.get("/api/reviews"),
                    await client.get("/api/archive"),
                ]
            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

        self.assertEqual([response.status_code for response in responses], [200, 200, 200, 200])


if __name__ == "__main__":
    unittest.main()
