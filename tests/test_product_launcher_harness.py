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
        self.assertIn("仍不在本轮产品化范围内", text)

    def test_frontend_display_preferences_are_documented_and_wired(self) -> None:
        app_shell = (ROOT / "apps" / "web" / "src" / "features" / "app" / "AppShell.tsx").read_text(encoding="utf-8")
        preferences = (ROOT / "apps" / "web" / "src" / "features" / "app" / "uiPreferences.tsx").read_text(encoding="utf-8")
        css = (ROOT / "apps" / "web" / "src" / "index.css").read_text(encoding="utf-8")
        manual = (ROOT / "docs" / "product-usage-manual.md").read_text(encoding="utf-8")

        self.assertIn("UiPreferenceProvider", app_shell)
        self.assertIn("sidebar-controls", app_shell)
        self.assertIn("stocktrade.ui.language", preferences)
        self.assertIn("stocktrade.ui.theme", preferences)
        self.assertIn("document.documentElement.lang", preferences)
        self.assertIn("document.documentElement.dataset.theme", preferences)
        self.assertIn("html[data-theme=\"dark\"]", css)
        self.assertIn("语言：中文 / English", manual)
        self.assertIn("主题模式：跟随系统 / 浅色 / 深色", manual)


if __name__ == "__main__":
    unittest.main()
