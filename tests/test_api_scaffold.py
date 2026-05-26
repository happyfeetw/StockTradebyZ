from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
