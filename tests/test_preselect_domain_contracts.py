from __future__ import annotations

import json
import asyncio
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
import pandas as pd
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "src"))

import select_stock  # noqa: E402
from stocktrade.domain.selection import PreselectParameters, PreselectService  # noqa: E402
from stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.schemas.preselect import CandidateResponse, PreselectRunRequest, PreselectRunResponse  # noqa: E402
from stocktrade_api.storage.run_repository import RunRepository  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "golden_master"
SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class FixtureB1Selector:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["_vec_pick"] = prepared["_fixture_b1"].astype(bool)
        return prepared

    def vec_picks_from_prepared(
        self,
        df: pd.DataFrame,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> list[pd.Timestamp]:
        mask = df["_vec_pick"].astype(bool)
        if start is not None:
            mask = mask & (df.index >= start)
        if end is not None:
            mask = mask & (df.index <= end)
        return list(df.index[mask])


class FixtureB2Selector(FixtureB1Selector):
    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["_vec_pick"] = prepared["_fixture_b2"].astype(bool)
        prepared["_b2_daily_return"] = 0.11
        prepared["_b2_today_body_pct"] = 0.05
        prepared["_b2_volume_ratio"] = 1.25
        prepared["_b2_prior_b1_lag"] = 1
        prepared["_b2_prior_b1_j"] = 20.0
        prepared["J"] = 35.0
        prepared["_b2_j_turn_up"] = True
        prepared["_b2_strict_yang_bao_yin"] = False
        prepared["_b2_upper_shadow_ratio"] = 0.02
        prepared["_b2_quality_score"] = prepared["_fixture_b2_quality_score"].astype(float)
        return prepared


class FixtureBrickSelector(FixtureB1Selector):
    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["_vec_pick"] = prepared["_fixture_brick"].astype(bool)
        prepared["brick_growth"] = prepared["_fixture_brick_growth"].astype(float)
        return prepared


def write_strategy_case_files(tmp: Path, case: dict) -> tuple[Path, Path]:
    raw_dir = tmp / "raw"
    raw_dir.mkdir()
    for code, rows in case["raw_data"].items():
        pd.DataFrame(rows).to_csv(raw_dir / f"{code}.csv", index=False)

    config = deepcopy(case["config"])
    config["global"]["data_dir"] = str(raw_dir)
    config_path = tmp / "rules_preselect.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw_dir, config_path


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


class PreselectDomainContractTests(unittest.TestCase):
    def test_preselect_schemas_preserve_candidate_identity_fields(self) -> None:
        request = PreselectRunRequest.model_validate(
            {
                "config_path": "config/rules_preselect.yaml",
                "data_dir": "data/raw",
                "pick_date": "2026-05-23",
                "end_date": "2026-05-23",
            }
        )
        self.assertEqual(request.pick_date, "2026-05-23")

        candidate = CandidateResponse.model_validate(
            {
                "code": "000001",
                "date": "2026-05-22",
                "strategy": "b2",
                "close": 12.0,
                "turnover_n": 71000.0,
                "extra": {"b2_quality_score": 110.0},
            }
        )
        self.assertEqual((candidate.code, candidate.strategy), ("000001", "b2"))

        response = PreselectRunResponse.model_validate(
            {
                "run": {
                    "id": "run-1",
                    "kind": "preselect",
                    "status": "succeeded",
                    "pick_date": "2026-05-22",
                    "started_at": None,
                    "finished_at": None,
                    "summary": {"total": 1},
                    "created_at": "2026-05-27T00:00:00",
                },
                "batch": {
                    "id": "batch-1",
                    "run_id": "run-1",
                    "pick_date": "2026-05-22",
                    "source": "preselect",
                    "strategy_counts": {"b2": 1},
                    "total": 1,
                    "created_at": "2026-05-27T00:00:00",
                    "candidates": [candidate.model_dump()],
                },
            }
        )
        self.assertEqual(response.batch.candidates[0].strategy, "b2")

    def test_domain_service_matches_preselect_golden_master(self) -> None:
        case = load_fixture("strategy_preselect_case.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            _, config_path = write_strategy_case_files(Path(tmpdir), case)
            with (
                patch.object(select_stock, "B1Selector", FixtureB1Selector),
                patch.object(select_stock, "B2Selector", FixtureB2Selector),
                patch.object(select_stock, "BrickChartSelector", FixtureBrickSelector),
            ):
                result = PreselectService().run(
                    PreselectParameters(
                        config_path=str(config_path),
                        pick_date=case["requested_pick_date"],
                    ),
                    run_date="2026-05-27",
                )

        self.assertEqual(result.pick_date, case["expected_pick_date"])
        self.assertEqual([candidate.to_dict() for candidate in result.candidates], case["expected_candidates"])
        self.assertEqual(result.strategy_counts, {"b1": 1, "b2": 2, "brick": 1})

    def test_preselect_service_can_run_against_port_without_legacy_imports(self) -> None:
        script = f"""
import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "src"))

from stocktrade.domain.selection import PreselectParameters, PreselectService

class FixturePort:
    def load_config(self, config_path=None):
        return {{
            "b1": {{"enabled": False}},
            "b2": {{"enabled": True}},
            "brick": {{"enabled": False}},
        }}

    def run_preselect(self, parameters):
        return dt.date.fromisoformat("2026-05-22"), [
            SimpleNamespace(
                code="000001",
                date="2026-05-22",
                strategy="b2",
                close=12.0,
                turnover_n=5.0,
                brick_growth=None,
                extra={{"b2_quality_score": 110.0}},
            )
        ]

result = PreselectService(port=FixturePort()).run(PreselectParameters(pick_date="2026-05-23"), run_date="2026-05-27")
print(result.pick_date)
print(result.strategy_counts)
print("select_stock" in sys.modules)
print("pipeline.select_stock" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip().splitlines(),
            [
                "2026-05-22",
                "{'b2': 1}",
                "False",
                "False",
            ],
        )

    def test_preselect_api_records_run_batch_events_and_candidates(self) -> None:
        case = load_fixture("strategy_preselect_case.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            _, config_path = write_strategy_case_files(tmp, case)
            db_path = tmp / "app.sqlite"
            migrate_sqlite(db_path)
            app = create_app(sqlite_path=db_path)
            repository: RunRepository = app.state.run_repository

            with (
                patch.object(select_stock, "B1Selector", FixtureB1Selector),
                patch.object(select_stock, "B2Selector", FixtureB2Selector),
                patch.object(select_stock, "BrickChartSelector", FixtureBrickSelector),
            ):
                response = asyncio.run(
                    self._post_preselect(
                        app,
                        {"config_path": str(config_path), "pick_date": case["requested_pick_date"]},
                    )
                )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["run"]["kind"], "preselect")
            self.assertEqual(payload["run"]["status"], "succeeded")
            self.assertEqual(payload["run"]["pick_date"], case["expected_pick_date"])
            self.assertEqual(payload["batch"]["pick_date"], case["expected_pick_date"])
            self.assertEqual(payload["batch"]["strategy_counts"], {"b1": 1, "b2": 2, "brick": 1})
            self.assertEqual([candidate["code"] for candidate in payload["batch"]["candidates"]], ["000001", "000002", "000001", "000002"])
            self.assertEqual(
                [candidate["strategy"] for candidate in payload["batch"]["candidates"]],
                ["b1", "b2", "b2", "brick"],
            )

            detail = repository.get_run_detail(payload["run"]["id"])
            self.assertEqual(detail.status, "succeeded")
            self.assertEqual(detail.steps[0].status, "succeeded")
            self.assertIn("Preselect job selected 4 candidates", [event.message for event in detail.events])

            batch = repository.get_candidate_batch_detail(payload["batch"]["id"])
            self.assertEqual(len(batch.candidates), 4)
            self.assertEqual(
                {(candidate.code, candidate.strategy) for candidate in batch.candidates},
                {("000001", "b1"), ("000001", "b2"), ("000002", "b2"), ("000002", "brick")},
            )
            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()

    def test_api_runtime_imports_do_not_pull_ui_or_paper_trading_modules(self) -> None:
        script = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / "apps" / "api"))
sys.path.insert(0, str(root / "src"))
import stocktrade_api.jobs.runtime
import stocktrade_api.routes.runs
print("workbench.app" in sys.modules)
print("paper_trading.core" in sys.modules)
print("streamlit" in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["False", "False", "False"])

    async def _post_preselect(self, app, payload: dict) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/runs/preselect", json=payload)


if __name__ == "__main__":
    unittest.main()
