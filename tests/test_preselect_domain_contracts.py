from __future__ import annotations

import json
import asyncio
import datetime as dt
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
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
from stocktrade.domain.selection import (  # noqa: E402
    LegacyMarketPreparationPort,
    LegacyPreselectExecutionPort,
    LegacyStrategyFormulaFactoryPort,
    LegacyWarmupBarsPort,
    PreselectExecutionSettings,
    PreselectParameters,
    PreselectService,
    ProductCsvMarketDataPort,
    ProductLiquidityPoolPort,
    ProductPickDatePort,
    ProductStrategySelectorPort,
    ProductWarmupBarsPort,
)
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

    def test_product_pick_date_port_matches_legacy_resolution(self) -> None:
        prepared = {
            "000001": pd.DataFrame(
                {"turnover_n": [10.0, 20.0]},
                index=pd.to_datetime(["2026-05-20", "2026-05-22"]),
            ),
            "000002": pd.DataFrame(
                {"turnover_n": [30.0]},
                index=pd.to_datetime(["2026-05-21"]),
            ),
            "ignored-non-trading-index": pd.DataFrame({"turnover_n": [999.0]}, index=[0]),
        }
        port = ProductPickDatePort()

        for requested_pick_date in [None, "2026-05-20", "2026-05-21", "2026-05-23"]:
            self.assertEqual(
                port.resolve_pick_date(prepared, requested_pick_date),
                select_stock._resolve_pick_date(prepared, requested_pick_date),
            )

        with self.assertRaisesRegex(ValueError, "早于最早可用日期"):
            port.resolve_pick_date(prepared, "2026-05-19")
        with self.assertRaisesRegex(ValueError, "prepared 数据中没有可用日期"):
            port.resolve_pick_date({"ignored": pd.DataFrame({"turnover_n": [1.0]}, index=[0])}, None)

    def test_product_liquidity_pool_port_matches_legacy_top_turnover_pool(self) -> None:
        dates = pd.to_datetime(["2026-05-20", "2026-05-21"])
        prepared = {
            "000001": pd.DataFrame({"turnover_n": [100.0, 10.0]}, index=dates),
            "000002": pd.DataFrame({"turnover_n": [90.0, 200.0]}, index=dates),
            "000003": pd.DataFrame({"turnover_n": [50.0, 150.0]}, index=dates),
        }
        port = ProductLiquidityPoolPort()

        for top_m in [0, 2, 10]:
            settings = PreselectExecutionSettings(
                data_dir="fixture/raw",
                top_m=top_m,
                n_turnover_days=3,
                min_bars_buffer=4,
                n_jobs=None,
                prepare_executor="thread",
            )
            legacy_pool_by_date = select_stock.TopTurnoverPoolBuilder(top_m=top_m).build(prepared)
            for pick_date in [*dates, pd.Timestamp("2026-05-22")]:
                self.assertEqual(
                    port.build_pool(prepared, pick_date=pick_date, settings=settings),
                    list(legacy_pool_by_date.get(pick_date, [])),
                )

    def test_product_strategy_selector_port_matches_legacy_strategy_dispatch(self) -> None:
        pick_date = pd.Timestamp("2026-05-22")
        config = {
            "b1": {
                "enabled": True,
                "zx_m1": 1,
                "zx_m2": 1,
                "zx_m3": 1,
                "zx_m4": 1,
                "j_threshold": 999.0,
                "j_q_threshold": 1.0,
            },
            "b2": {"enabled": True, "b1_lookback": 2},
            "brick": {"enabled": True},
        }
        prepared = {
            "000001": pd.DataFrame(
                {
                    "close": [12.0],
                    "turnover_n": [71000.0],
                    "_fixture_b1": [1],
                    "_fixture_b2": [1],
                    "_fixture_brick": [0],
                    "_fixture_b2_quality_score": [110.0],
                    "_fixture_brick_growth": [0.0],
                },
                index=[pick_date],
            ),
            "000002": pd.DataFrame(
                {
                    "close": [10.0],
                    "turnover_n": [54000.0],
                    "_fixture_b1": [0],
                    "_fixture_b2": [1],
                    "_fixture_brick": [1],
                    "_fixture_b2_quality_score": [120.0],
                    "_fixture_brick_growth": [2.5],
                },
                index=[pick_date],
            ),
            "000003": pd.DataFrame(
                {
                    "close": [8.5],
                    "turnover_n": [1000.0],
                    "_fixture_b1": [1],
                    "_fixture_b2": [1],
                    "_fixture_brick": [1],
                    "_fixture_b2_quality_score": [130.0],
                    "_fixture_brick_growth": [3.5],
                },
                index=[pick_date],
            ),
        }
        pool_codes = ["000001", "000002", "000003", "missing"]

        with (
            patch.object(select_stock, "B1Selector", FixtureB1Selector),
            patch.object(select_stock, "B2Selector", FixtureB2Selector),
            patch.object(select_stock, "BrickChartSelector", FixtureBrickSelector),
        ):
            port = ProductStrategySelectorPort(LegacyStrategyFormulaFactoryPort(lambda: select_stock))
            product_candidates = port.run_strategies(
                prepared,
                pick_date=pick_date,
                pool_codes=pool_codes,
                config=config,
            )
            legacy_candidates = []
            legacy_candidates.extend(select_stock.run_b1(prepared, pick_date, pool_codes, config["b1"]))
            legacy_candidates.extend(
                select_stock.run_b2(prepared, pick_date, pool_codes, config["b2"], config["b1"])
            )
            legacy_candidates.extend(select_stock.run_brick(prepared, pick_date, pool_codes, config["brick"]))

        self.assertEqual(
            [candidate.to_dict() for candidate in product_candidates],
            [candidate.to_dict() for candidate in legacy_candidates],
        )

    def test_product_warmup_bars_port_matches_legacy_warmup_calculation(self) -> None:
        cases = [
            ({}, 10),
            ({"b1": {"enabled": False}, "b2": {"enabled": False}, "brick": {"enabled": False}}, 3),
            ({"b1": {"enabled": True, "zx_m4": 200, "wma_long": 55}, "brick": {"enabled": False}}, 7),
            (
                {
                    "b1": {
                        "enabled": True,
                        "zx_m4": 144,
                        "wma_long": 33,
                    },
                    "b2": {
                        "enabled": True,
                        "b1_lookback": 5,
                    },
                    "brick": {"enabled": False},
                },
                11,
            ),
            (
                {
                    "b1": {"enabled": False},
                    "b2": {"enabled": False},
                    "brick": {
                        "enabled": True,
                        "wma_long": 80,
                        "zxdkx_m4": 260,
                    },
                },
                9,
            ),
        ]
        product_port = ProductWarmupBarsPort()
        legacy_port = LegacyWarmupBarsPort(lambda: select_stock)

        for config, min_bars_buffer in cases:
            with self.subTest(config=config, min_bars_buffer=min_bars_buffer):
                self.assertEqual(
                    product_port.calculate_warmup(config, min_bars_buffer),
                    legacy_port.calculate_warmup(config, min_bars_buffer),
                )
                self.assertEqual(
                    product_port.calculate_warmup(config, min_bars_buffer),
                    select_stock._calc_warmup(config, min_bars_buffer),
                )

    def test_legacy_market_preparation_default_uses_product_warmup_port(self) -> None:
        port = LegacyMarketPreparationPort(lambda: SimpleNamespace())

        self.assertIsInstance(port.warmup, ProductWarmupBarsPort)

    def test_product_csv_market_data_port_matches_legacy_raw_data_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = Path(tmpdir)
            pd.DataFrame(
                [
                    {"Date": "2026-05-22", "Open": 12.0, "Close": 12.3, "Volume": 5000},
                    {"Date": "2026-05-20", "Open": 10.0, "Close": 10.3, "Volume": 1000},
                    {"Date": "2026-05-24", "Open": 14.0, "Close": 14.3, "Volume": 9000},
                ]
            ).to_csv(raw_dir / "000001.CSV", index=False)
            pd.DataFrame(
                [
                    {"date": "2026-05-21", "open": 8.0, "close": 8.2, "volume": 2000},
                    {"date": "2026-05-22", "open": 9.0, "close": 9.2, "volume": 3000},
                ]
            ).to_csv(raw_dir / "000002.csv", index=False)
            pd.DataFrame([{"open": 1.0, "close": 1.1}]).to_csv(raw_dir / "ignored_no_date.csv", index=False)
            (raw_dir / "ignored.txt").write_text("not csv", encoding="utf-8")

            settings = PreselectExecutionSettings(
                data_dir=str(raw_dir),
                top_m=20,
                n_turnover_days=3,
                min_bars_buffer=4,
                n_jobs=None,
                prepare_executor="thread",
            )
            parameters = PreselectParameters(end_date="2026-05-22")

            product_data = ProductCsvMarketDataPort().load_raw_data(settings, parameters)
            legacy_data = select_stock.load_raw_data(str(raw_dir), end_date=parameters.end_date)

        self.assertEqual(product_data.keys(), legacy_data.keys())
        self.assertNotIn("ignored_no_date", product_data)
        self.assertEqual(product_data["000001"]["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-05-20", "2026-05-22"])
        for code in product_data:
            pd.testing.assert_frame_equal(product_data[code], legacy_data[code])

    def test_product_csv_market_data_port_matches_legacy_loader_errors(self) -> None:
        settings = PreselectExecutionSettings(
            data_dir="/missing/product/raw",
            top_m=20,
            n_turnover_days=3,
            min_bars_buffer=4,
            n_jobs=None,
            prepare_executor="thread",
        )
        with self.assertRaisesRegex(FileNotFoundError, "data_dir 不存在"):
            ProductCsvMarketDataPort().load_raw_data(settings, PreselectParameters())

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = Path(tmpdir)
            pd.DataFrame([{"open": 1.0, "close": 1.1}]).to_csv(raw_dir / "ignored.csv", index=False)
            settings = PreselectExecutionSettings(
                data_dir=str(raw_dir),
                top_m=20,
                n_turnover_days=3,
                min_bars_buffer=4,
                n_jobs=None,
                prepare_executor="thread",
            )
            with self.assertRaisesRegex(ValueError, "未找到任何 CSV 数据"):
                ProductCsvMarketDataPort().load_raw_data(settings, PreselectParameters())

    def test_legacy_preselect_default_uses_product_owned_date_and_pool_ports(self) -> None:
        port = LegacyPreselectExecutionPort(module_loader=lambda: SimpleNamespace())

        self.assertIsInstance(port.market_data, ProductCsvMarketDataPort)
        self.assertIsInstance(port.pick_dates, ProductPickDatePort)
        self.assertIsInstance(port.liquidity_pool, ProductLiquidityPoolPort)
        self.assertIsInstance(port.strategy_selectors, ProductStrategySelectorPort)

    def test_legacy_port_orchestrates_named_selection_ports_and_preserves_identity_dedupe(self) -> None:
        calls: list[str] = []
        config = {
            "global": {
                "data_dir": "fixture/raw",
                "top_m": 2,
                "n_turnover_days": 3,
                "min_bars_buffer": 4,
                "n_jobs": 1,
                "prepare_executor": "thread",
            },
            "b1": {"enabled": False},
            "b2": {"enabled": True},
            "brick": {"enabled": True},
        }

        class FixtureModule:
            def load_config(self, config_path=None):
                calls.append(f"config:{config_path}")
                return config

            def _resolve_cfg_path(self, path):
                return Path("/fixture-root") / str(path)

        class FixtureMarketData:
            def load_raw_data(self, settings, parameters):
                calls.append(f"raw:{settings.data_dir}:{parameters.end_date}")
                self.settings = settings
                return {"raw": object()}

        class FixturePreparation:
            def prepare(self, raw_data, *, config, settings, parameters):
                calls.append(f"prepare:{settings.n_turnover_days}:{settings.prepare_executor}")
                self.raw_data = raw_data
                return {"prepared": object()}

        class FixturePickDate:
            def resolve_pick_date(self, prepared, requested_pick_date):
                calls.append(f"date:{requested_pick_date}")
                return dt.date.fromisoformat("2026-05-22")

        class FixtureLiquidity:
            def build_pool(self, prepared, *, pick_date, settings):
                calls.append(f"pool:{pick_date.isoformat()}:{settings.top_m}")
                return ["000001", "000002"]

        class FixtureStrategies:
            def run_strategies(self, prepared, *, pick_date, pool_codes, config):
                calls.append(f"strategies:{pick_date.isoformat()}:{','.join(pool_codes)}")
                return [
                    SimpleNamespace(
                        code="000001",
                        date="2026-05-22",
                        strategy="b2",
                        close=12.0,
                        turnover_n=5.0,
                        extra={"b2_quality_score": 110.0},
                    ),
                    SimpleNamespace(
                        code="000001",
                        date="2026-05-22",
                        strategy="b2",
                        close=12.0,
                        turnover_n=5.0,
                        extra={"b2_quality_score": 99.0},
                    ),
                    SimpleNamespace(
                        code="000001",
                        date="2026-05-22",
                        strategy="brick",
                        close=12.0,
                        turnover_n=5.0,
                        brick_growth=2.5,
                    ),
                ]

        market_data = FixtureMarketData()
        port = LegacyPreselectExecutionPort(
            module_loader=FixtureModule,
            market_data=market_data,
            market_preparation=FixturePreparation(),
            pick_dates=FixturePickDate(),
            liquidity_pool=FixtureLiquidity(),
            strategy_selectors=FixtureStrategies(),
        )

        pick_date, candidates = port.run_preselect(
            PreselectParameters(
                config_path="fixture-config.json",
                pick_date="2026-05-23",
                end_date="2026-05-23",
            )
        )

        self.assertEqual(pick_date, dt.date.fromisoformat("2026-05-22"))
        self.assertEqual(
            [(candidate.code, candidate.strategy) for candidate in candidates],
            [("000001", "b2"), ("000001", "brick")],
        )
        self.assertEqual(market_data.settings.data_dir, "/fixture-root/fixture/raw")
        self.assertEqual(
            calls,
            [
                "config:fixture-config.json",
                "raw:/fixture-root/fixture/raw:2026-05-23",
                "prepare:3:thread",
                "date:2026-05-23",
                "pool:2026-05-22:2",
                "strategies:2026-05-22:000001,000002",
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
