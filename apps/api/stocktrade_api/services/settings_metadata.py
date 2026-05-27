from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from stocktrade.domain.metadata import PRODUCT_STACK, SERVICE_NAME, VERSION

ROOT = Path(__file__).resolve().parents[4]

SAFE_CONFIG_FILES = {
    "preselect": ROOT / "config" / "rules_preselect.yaml",
    "review_provider": ROOT / "config" / "gemini_cli_review.yaml",
    "market_data": ROOT / "config" / "fetch_kline.yaml",
    "dashboard": ROOT / "config" / "dashboard.yaml",
}


def build_product_settings(
    *,
    sqlite_path: str | Path,
    duckdb_path: str | Path | None,
    artifact_root: str | Path,
    backup_root: str | Path,
    product_preferences: dict[str, Any],
) -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "stack": PRODUCT_STACK,
        "simulated_trading_in_scope": False,
        "product_preferences": product_preferences,
        "local_state": {
            "sqlite_path": _path_str(sqlite_path),
            "duckdb_path": _path_str(duckdb_path) if duckdb_path is not None else None,
            "artifact_root": _path_str(artifact_root),
            "backup_root": _path_str(backup_root),
        },
        "config_files": [
            {
                "key": key,
                "path": _relative_path(path),
                "exists": path.exists(),
                "sections": _config_sections(path),
                "writable": False,
                "exposed": True,
            }
            for key, path in SAFE_CONFIG_FILES.items()
        ],
        "external_integrations": [
            {
                "key": "tushare",
                "label": "Tushare market data",
                "configured": bool(os.environ.get("TUSHARE_TOKEN")),
                "source": "environment:TUSHARE_TOKEN",
                "secret_exposed": False,
            },
            {
                "key": "gemini_api",
                "label": "Gemini API reviewer",
                "configured": bool(os.environ.get("GEMINI_API_KEY")),
                "source": "environment:GEMINI_API_KEY",
                "secret_exposed": False,
            },
            {
                "key": "gemini_cli",
                "label": "Gemini CLI reviewer",
                "configured": bool(_gemini_cli_configured()),
                "source": "config:gemini_cli_review.yaml",
                "secret_exposed": False,
            },
        ],
    }


def build_strategy_metadata(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path is not None else SAFE_CONFIG_FILES["preselect"]
    if not path.is_absolute():
        path = ROOT / path
    payload = _load_yaml_mapping(path)
    strategies = [
        _strategy_definition("b1", "B1", "KDJ quantile, ZX trend, weekly MA bull, and max-volume confirmation.", payload),
        _strategy_definition("b2", "B2", "Recent B1 confirmation followed by strong bullish price and volume action.", payload),
        _strategy_definition("brick", "Brick", "Brick-chart reversal pattern with optional ZX and weekly trend filters.", payload),
    ]
    return {
        "config_path": _relative_path(path),
        "config_exists": path.exists(),
        "candidate_identity": ["code", "strategy"],
        "strategies": strategies,
    }


def _strategy_definition(
    strategy_id: str,
    label: str,
    description: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    section = config.get(strategy_id, {})
    section = section if isinstance(section, dict) else {}
    return {
        "id": strategy_id,
        "label": label,
        "description": description,
        "enabled_by_default": bool(section.get("enabled", strategy_id == "b1")),
        "candidate_identity": ["code", "strategy"],
        "parity_status": "product_owned_with_legacy_adapter",
        "config_provenance": {
            "path": _relative_path(SAFE_CONFIG_FILES["preselect"]),
            "exists": SAFE_CONFIG_FILES["preselect"].exists(),
            "section": strategy_id,
        },
        "parameters": {key: value for key, value in section.items() if key != "enabled"},
    }


def _config_sections(path: Path) -> list[str]:
    payload = _load_yaml_mapping(path)
    return sorted(str(key) for key, value in payload.items() if isinstance(value, (dict, list, str, int, float, bool)))


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _path_str(path: str | Path) -> str:
    return Path(path).as_posix()


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _gemini_cli_configured() -> bool:
    config = _load_yaml_mapping(SAFE_CONFIG_FILES["review_provider"])
    gemini_bin = str(config.get("gemini_bin") or "").strip()
    return bool(gemini_bin)
