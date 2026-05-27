from __future__ import annotations

from pathlib import Path

from stocktrade_api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
UI_SMOKE_ROOT = ROOT / "var" / "ui-smoke"

app = create_app(
    sqlite_path=UI_SMOKE_ROOT / "db" / "app.sqlite",
    duckdb_path=UI_SMOKE_ROOT / "db" / "analytics.duckdb",
    artifact_root=UI_SMOKE_ROOT / "artifacts",
    backup_root=UI_SMOKE_ROOT / "backups",
)
