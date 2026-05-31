from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProductLauncherHarnessTests(unittest.TestCase):
    def test_start_product_is_executable_and_targets_react_fastapi_stack(self) -> None:
        path = ROOT / "start_product"
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")

        self.assertTrue(mode & stat.S_IXUSR)
        self.assertIn("stocktrade_api.main:app", text)
        self.assertIn("node node_modules/vite/bin/vite.js", text)
        self.assertIn("command -v node", text)
        self.assertIn("NODE_MAJOR", text)
        self.assertIn("requires Node.js 23.x", text)
        self.assertIn("Missing Vite CLI", text)
        self.assertIn("STOCKTRADE_API_PORT:-8000", text)
        self.assertIn("STOCKTRADE_WEB_PORT:-5173", text)
        self.assertIn("PYTHONPATH=\"apps/api:src:${PYTHONPATH:-}\"", text)
        self.assertIn("cleanup()", text)

    def test_web_dev_proxy_tracks_product_launcher_api_port(self) -> None:
        text = (ROOT / "apps" / "web" / "vite.config.ts").read_text(encoding="utf-8")

        self.assertIn("process.env.STOCKTRADE_API_HOST", text)
        self.assertIn("process.env.STOCKTRADE_API_PORT", text)
        self.assertIn("target: apiTarget", text)
        self.assertNotIn("'/api': 'http://127.0.0.1:8000'", text)

    def test_legacy_workbench_points_to_product_launcher(self) -> None:
        text = (ROOT / "start_workbench").read_text(encoding="utf-8")

        self.assertIn("R7 legacy write freeze", text)
        self.assertIn("./start_product", text)
        self.assertIn("React/FastAPI workflows", text)

    def test_product_launcher_doc_keeps_simulated_trading_out_of_scope(self) -> None:
        text = (ROOT / "docs" / "agent-harness" / "r7-product-launcher.md").read_text(encoding="utf-8")

        self.assertIn("./start_product", text)
        self.assertIn("simulated trading", text)
        self.assertIn("remains out of scope", text)


if __name__ == "__main__":
    unittest.main()
