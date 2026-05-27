from __future__ import annotations

import datetime as dt
import logging
import math
import os
import sys
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .selectors import ProductStrategyFormulaFactoryPort

ROOT = Path(__file__).resolve().parents[4]
PIPELINE_DIR = ROOT / "pipeline"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelectionCandidate:
    code: str
    date: str
    strategy: str
    close: float
    turnover_n: float
    brick_growth: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["brick_growth"] is None:
            data.pop("brick_growth")
        if not data["extra"]:
            data.pop("extra")
        return data


@dataclass(frozen=True)
class PreselectParameters:
    config_path: str | None = None
    data_dir: str | None = None
    pick_date: str | None = None
    end_date: str | None = None


@dataclass(frozen=True)
class PreselectExecutionSettings:
    data_dir: str
    top_m: int
    n_turnover_days: int
    min_bars_buffer: int
    n_jobs: int | None
    prepare_executor: str


@dataclass(frozen=True)
class PreselectResult:
    run_date: str
    pick_date: str
    candidates: list[SelectionCandidate]
    meta: dict[str, Any]

    @property
    def strategy_counts(self) -> dict[str, int]:
        counts = {str(key): int(value) for key, value in self.meta.get("strategy_candidate_counts", {}).items()}
        for candidate in self.candidates:
            counts.setdefault(candidate.strategy, 0)
        return counts

    def to_candidate_run_dict(self) -> dict[str, Any]:
        return {
            "run_date": self.run_date,
            "pick_date": self.pick_date,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "meta": self.meta,
        }


LegacyRunner = Callable[..., tuple[Any, list[Any]]]
ConfigLoader = Callable[[str | None], dict[str, Any]]


class PreselectExecutionPort(Protocol):
    def load_config(self, config_path: str | None = None) -> dict[str, Any]:
        ...

    def run_preselect(self, parameters: PreselectParameters) -> tuple[Any, list[Any]]:
        ...


class MarketDataPort(Protocol):
    def load_raw_data(self, settings: PreselectExecutionSettings, parameters: PreselectParameters) -> dict[str, Any]:
        ...


class MarketPreparationPort(Protocol):
    def prepare(
        self,
        raw_data: dict[str, Any],
        *,
        config: dict[str, Any],
        settings: PreselectExecutionSettings,
        parameters: PreselectParameters,
    ) -> dict[str, Any]:
        ...


class WarmupBarsPort(Protocol):
    def calculate_warmup(self, config: dict[str, Any], min_bars_buffer: int) -> int:
        ...


class PickDatePort(Protocol):
    def resolve_pick_date(self, prepared: dict[str, Any], requested_pick_date: str | None) -> Any:
        ...


class LiquidityPoolPort(Protocol):
    def build_pool(
        self,
        prepared: dict[str, Any],
        *,
        pick_date: Any,
        settings: PreselectExecutionSettings,
    ) -> list[str]:
        ...


class StrategySelectorPort(Protocol):
    def run_strategies(
        self,
        prepared: dict[str, Any],
        *,
        pick_date: Any,
        pool_codes: list[str],
        config: dict[str, Any],
    ) -> list[Any]:
        ...


class StrategyFormulaFactoryPort(Protocol):
    def create_b1_selector(self, **parameters: Any) -> Any:
        ...

    def create_b2_selector(self, **parameters: Any) -> Any:
        ...

    def create_brick_selector(self, **parameters: Any) -> Any:
        ...


def _enabled_strategies(config: dict[str, Any]) -> list[str]:
    strategies: list[str] = []
    if config.get("b1", {}).get("enabled", True):
        strategies.append("b1")
    if config.get("b2", {}).get("enabled", False):
        strategies.append("b2")
    if config.get("brick", {}).get("enabled", True):
        strategies.append("brick")
    return strategies


def _strategy_candidate_counts(strategies: list[str], candidates: list[SelectionCandidate]) -> dict[str, int]:
    counts = {strategy: 0 for strategy in strategies}
    for candidate in candidates:
        counts[candidate.strategy] = counts.get(candidate.strategy, 0) + 1
    return counts


def _dedupe_candidates_by_code_strategy(candidates: list[Any]) -> list[Any]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Any] = []
    for candidate in candidates:
        key = (str(candidate.code), str(candidate.strategy))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _sorted_prepared_dates(prepared: dict[str, Any]) -> list[Any]:
    import pandas as pd

    return sorted(
        {
            trade_date
            for frame in prepared.values()
            if isinstance(getattr(frame, "index", None), pd.DatetimeIndex)
            for trade_date in frame.index
        }
    )


def _top_turnover_pool_by_date(prepared: dict[str, Any], *, top_m: int) -> dict[Any, list[str]]:
    if top_m <= 0:
        return {}

    pool: dict[Any, list[tuple[float, str]]] = defaultdict(list)
    for code, frame in prepared.items():
        for trade_date, turnover in frame["turnover_n"].items():
            pool[trade_date].append((float(turnover), code))

    top_codes_by_date: dict[Any, list[str]] = {}
    for trade_date, entries in pool.items():
        if not entries:
            continue
        ranked = sorted(entries, key=lambda item: item[0], reverse=True)[:top_m]
        top_codes_by_date[trade_date] = [code for _, code in ranked]
    return top_codes_by_date


def _sorted_zx(m1: int, m2: int, m3: int, m4: int) -> tuple[int, int, int, int]:
    values = sorted([int(m1), int(m2), int(m3), int(m4)])
    return values[0], values[1], values[2], values[3]


def _calculate_preselect_warmup(config: dict[str, Any], min_bars_buffer: int) -> int:
    warmup = 120
    buffer = int(min_bars_buffer)

    config_b1 = config.get("b1", {})
    if config_b1.get("enabled", True):
        warmup = max(
            warmup,
            int(config_b1.get("zx_m4", 371)) + buffer,
            int(config_b1.get("wma_long", 30)) * 5 + buffer,
        )

    config_b2 = config.get("b2", {})
    if config_b2.get("enabled", False):
        warmup = max(
            warmup,
            int(config_b1.get("zx_m4", 114)) + buffer + int(config_b2.get("b1_lookback", 2)),
            int(config_b1.get("wma_long", 30)) * 5 + buffer + int(config_b2.get("b1_lookback", 2)),
        )

    config_brick = config.get("brick", {})
    if config_brick.get("enabled", True):
        warmup = max(
            warmup,
            int(config_brick.get("wma_long", 120)) * 5 + buffer,
            int(config_brick.get("zxdkx_m4", 114)) + buffer,
        )

    return warmup


def _load_csv_market_data(data_dir: str, end_date: str | None = None) -> dict[str, Any]:
    import pandas as pd

    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"data_dir 不存在: {data_dir}")

    end_ts = pd.to_datetime(end_date) if end_date else None
    data: dict[str, Any] = {}
    for filename in os.listdir(data_dir):
        if not filename.lower().endswith(".csv"):
            continue
        code = filename.rsplit(".", 1)[0]
        file_path = os.path.join(data_dir, filename)

        frame = pd.read_csv(file_path)
        frame.columns = [column.lower() for column in frame.columns]
        if "date" not in frame.columns:
            logger.warning("跳过 %s：没有 date 列", filename)
            continue

        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values("date").reset_index(drop=True)
        if end_ts is not None:
            frame = frame[frame["date"] <= end_ts].reset_index(drop=True)
        if not frame.empty:
            data[code] = frame

    if not data:
        raise ValueError(f"未找到任何 CSV 数据: {data_dir}")
    logger.info("读取股票数量: %d", len(data))
    return data


def _prepare_market_frame(args: tuple[str, Any, Any, Any, int, int]) -> tuple[str, Any | None]:
    import numpy as np
    import pandas as pd

    code, frame, start_date, end_date, warmup_bars, n_turnover_days = args
    frame = frame.copy()
    frame.columns = [column.lower() for column in frame.columns]

    if "date" not in frame.columns:
        return code, None
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").reset_index(drop=True)

    if start_date is not None:
        dates = frame["date"].values
        idx_start = int(np.searchsorted(dates, start_date.to_datetime64(), side="left"))
        if idx_start >= len(frame):
            return code, None
        warmup_start = max(0, idx_start - int(warmup_bars))
        frame = frame.iloc[warmup_start:].reset_index(drop=True)

    if end_date is not None:
        frame = frame[frame["date"] <= end_date].reset_index(drop=True)

    if frame.empty:
        return code, None

    for column in ("open", "close", "volume"):
        if column not in frame.columns:
            return code, None
    open_, close, volume = frame["open"], frame["close"], frame["volume"]
    frame["signed_turnover"] = (open_ + close) / 2 * volume
    frame["turnover_n"] = frame["signed_turnover"].rolling(int(n_turnover_days), min_periods=1).sum()
    frame = frame.set_index("date", drop=False)
    return code, frame


def _preparation_executor_class(executor: str) -> tuple[type[ThreadPoolExecutor] | type[ProcessPoolExecutor], str]:
    if str(executor).lower() in {"thread", "threads", "threadpool"}:
        return ThreadPoolExecutor, "thread"
    return ProcessPoolExecutor, "process"


def _prepare_market_frames(
    tasks: list[tuple[str, Any, Any, Any, int, int]],
    *,
    n_jobs: int | None,
    executor: str,
    allow_process_fallback: bool = True,
) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    Executor, _ = _preparation_executor_class(executor)
    try:
        with Executor(max_workers=n_jobs) as pool:
            futures = {pool.submit(_prepare_market_frame, task): task[0] for task in tasks}
            for future in as_completed(futures):
                code, frame = future.result()
                if frame is not None:
                    prepared[code] = frame
    except PermissionError as exc:
        if Executor is not ProcessPoolExecutor or not allow_process_fallback:
            raise
        logger.warning("进程池初始化失败，自动降级为线程池：%s", exc)
        with ThreadPoolExecutor(max_workers=n_jobs) as pool:
            futures = {pool.submit(_prepare_market_frame, task): task[0] for task in tasks}
            for future in as_completed(futures):
                code, frame = future.result()
                if frame is not None:
                    prepared[code] = frame
    return prepared


def _load_legacy_select_stock() -> Any:
    if str(PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(PIPELINE_DIR))
    import select_stock

    return select_stock


class ProductCsvMarketDataPort:
    def load_raw_data(self, settings: PreselectExecutionSettings, parameters: PreselectParameters) -> dict[str, Any]:
        return _load_csv_market_data(settings.data_dir, end_date=parameters.end_date)


class LegacyMarketDataPort:
    def __init__(self, module_provider: Callable[[], Any]):
        self._module_provider = module_provider

    def load_raw_data(self, settings: PreselectExecutionSettings, parameters: PreselectParameters) -> dict[str, Any]:
        return self._module_provider().load_raw_data(settings.data_dir, end_date=parameters.end_date)


class ProductWarmupBarsPort:
    def calculate_warmup(self, config: dict[str, Any], min_bars_buffer: int) -> int:
        return _calculate_preselect_warmup(config, min_bars_buffer)


class LegacyWarmupBarsPort:
    def __init__(self, module_provider: Callable[[], Any]):
        self._module_provider = module_provider

    def calculate_warmup(self, config: dict[str, Any], min_bars_buffer: int) -> int:
        return self._module_provider()._calc_warmup(config, min_bars_buffer)


class ProductMarketPreparationPort:
    def __init__(self, *, warmup: WarmupBarsPort | None = None):
        self.warmup = warmup or ProductWarmupBarsPort()

    def prepare(
        self,
        raw_data: dict[str, Any],
        *,
        config: dict[str, Any],
        settings: PreselectExecutionSettings,
        parameters: PreselectParameters,
    ) -> dict[str, Any]:
        import pandas as pd

        end_date = pd.to_datetime(parameters.end_date) if parameters.end_date else None
        warmup_bars = self.warmup.calculate_warmup(config, settings.min_bars_buffer)
        tasks = [
            (code, frame, None, end_date, warmup_bars, settings.n_turnover_days)
            for code, frame in raw_data.items()
        ]
        return _prepare_market_frames(
            tasks,
            n_jobs=settings.n_jobs,
            executor=settings.prepare_executor,
        )


class LegacyMarketPreparationPort:
    def __init__(self, module_provider: Callable[[], Any], *, warmup: WarmupBarsPort | None = None):
        self._module_provider = module_provider
        self.warmup = warmup or ProductWarmupBarsPort()

    def prepare(
        self,
        raw_data: dict[str, Any],
        *,
        config: dict[str, Any],
        settings: PreselectExecutionSettings,
        parameters: PreselectParameters,
    ) -> dict[str, Any]:
        module = self._module_provider()
        preparer = module.MarketDataPreparer(
            end_date=module.pd.to_datetime(parameters.end_date) if parameters.end_date else None,
            warmup_bars=self.warmup.calculate_warmup(config, settings.min_bars_buffer),
            n_turnover_days=settings.n_turnover_days,
            selector=None,
            n_jobs=settings.n_jobs,
            executor=settings.prepare_executor,
        )
        return preparer.prepare(raw_data)


class ProductPickDatePort:
    def resolve_pick_date(self, prepared: dict[str, Any], requested_pick_date: str | None) -> Any:
        import pandas as pd

        all_dates = _sorted_prepared_dates(prepared)
        if not all_dates:
            raise ValueError("prepared 数据中没有可用日期。")
        if requested_pick_date is None:
            return all_dates[-1]

        target = pd.to_datetime(requested_pick_date)
        idx = bisect_right(all_dates, target) - 1
        if idx < 0:
            raise ValueError(f"pick_date={requested_pick_date} 早于最早可用日期={all_dates[0].date()}")
        return all_dates[idx]


class LegacyPickDatePort:
    def __init__(self, module_provider: Callable[[], Any]):
        self._module_provider = module_provider

    def resolve_pick_date(self, prepared: dict[str, Any], requested_pick_date: str | None) -> Any:
        return self._module_provider()._resolve_pick_date(prepared, requested_pick_date)


class ProductLiquidityPoolPort:
    def build_pool(
        self,
        prepared: dict[str, Any],
        *,
        pick_date: Any,
        settings: PreselectExecutionSettings,
    ) -> list[str]:
        pool_by_date = _top_turnover_pool_by_date(prepared, top_m=settings.top_m)
        return list(pool_by_date.get(pick_date, []))


class LegacyLiquidityPoolPort:
    def __init__(self, module_provider: Callable[[], Any]):
        self._module_provider = module_provider

    def build_pool(
        self,
        prepared: dict[str, Any],
        *,
        pick_date: Any,
        settings: PreselectExecutionSettings,
    ) -> list[str]:
        pool_by_date = self._module_provider().TopTurnoverPoolBuilder(top_m=settings.top_m).build(prepared)
        return list(pool_by_date.get(pick_date, []))


class LegacyStrategyFormulaFactoryPort:
    def __init__(self, module_provider: Callable[[], Any]):
        self._module_provider = module_provider

    def create_b1_selector(self, **parameters: Any) -> Any:
        return self._module_provider().B1Selector(**parameters)

    def create_b2_selector(self, **parameters: Any) -> Any:
        return self._module_provider().B2Selector(**parameters)

    def create_brick_selector(self, **parameters: Any) -> Any:
        return self._module_provider().BrickChartSelector(**parameters)


class ProductStrategySelectorPort:
    def __init__(self, formula_factory: StrategyFormulaFactoryPort):
        self.formula_factory = formula_factory

    def run_strategies(
        self,
        prepared: dict[str, Any],
        *,
        pick_date: Any,
        pool_codes: list[str],
        config: dict[str, Any],
    ) -> list[SelectionCandidate]:
        candidates: list[SelectionCandidate] = []
        if config.get("b1", {}).get("enabled", True):
            candidates.extend(self._run_b1(prepared, pick_date, pool_codes, config["b1"]))
        if config.get("b2", {}).get("enabled", False):
            candidates.extend(self._run_b2(prepared, pick_date, pool_codes, config["b2"], config.get("b1", {})))
        if config.get("brick", {}).get("enabled", True):
            candidates.extend(self._run_brick(prepared, pick_date, pool_codes, config["brick"]))
        return candidates

    def _run_b1(
        self,
        prepared: dict[str, Any],
        pick_date: Any,
        pool_codes: list[str],
        config: dict[str, Any],
    ) -> list[SelectionCandidate]:
        zx_m1, zx_m2, zx_m3, zx_m4 = _sorted_zx(
            config["zx_m1"], config["zx_m2"], config["zx_m3"], config["zx_m4"]
        )
        selector = self.formula_factory.create_b1_selector(
            j_threshold=float(config["j_threshold"]),
            j_q_threshold=float(config["j_q_threshold"]),
            zx_m1=zx_m1,
            zx_m2=zx_m2,
            zx_m3=zx_m3,
            zx_m4=zx_m4,
        )

        date_str = pick_date.strftime("%Y-%m-%d")
        candidates: list[SelectionCandidate] = []
        for code in pool_codes:
            frame = prepared.get(code)
            if frame is None or pick_date not in frame.index:
                continue
            try:
                prepared_frame = selector.prepare_df(frame)
                if selector.vec_picks_from_prepared(prepared_frame, start=pick_date, end=pick_date):
                    row = prepared_frame.loc[pick_date]
                    candidates.append(
                        SelectionCandidate(
                            code=code,
                            date=date_str,
                            strategy="b1",
                            close=float(row["close"]),
                            turnover_n=float(row["turnover_n"]),
                        )
                    )
            except Exception as exc:
                logger.debug("B1 skip %s: %s", code, exc)
        logger.info("B1 选出: %d 只", len(candidates))
        return candidates

    def _run_b2(
        self,
        prepared: dict[str, Any],
        pick_date: Any,
        pool_codes: list[str],
        config_b2: dict[str, Any],
        config_b1: dict[str, Any],
    ) -> list[SelectionCandidate]:
        zx_m1, zx_m2, zx_m3, zx_m4 = _sorted_zx(
            config_b1.get("zx_m1", 14),
            config_b1.get("zx_m2", 28),
            config_b1.get("zx_m3", 57),
            config_b1.get("zx_m4", 114),
        )
        selector = self.formula_factory.create_b2_selector(
            j_threshold=float(config_b1.get("j_threshold", 15.0)),
            j_q_threshold=float(config_b1.get("j_q_threshold", 0.10)),
            kdj_n=int(config_b1.get("kdj_n", 9)),
            zx_m1=zx_m1,
            zx_m2=zx_m2,
            zx_m3=zx_m3,
            zx_m4=zx_m4,
            zxdq_span=int(config_b1.get("zxdq_span", 10)),
            wma_short=int(config_b1.get("wma_short", 10)),
            wma_mid=int(config_b1.get("wma_mid", 20)),
            wma_long=int(config_b1.get("wma_long", 30)),
            max_vol_lookback=config_b1.get("max_vol_lookback", 20),
            b1_lookback=int(config_b2.get("b1_lookback", 2)),
            min_return=float(config_b2.get("min_return", 0.04)),
            min_today_body_pct=float(config_b2.get("min_today_body_pct", 0.003)),
            j_ceiling=float(config_b2.get("j_ceiling", 55.0)),
            require_j_turn_up=bool(config_b2.get("require_j_turn_up", True)),
            volume_ratio_min=float(config_b2.get("volume_ratio_min", 1.0)),
            flat_volume_ratio=float(config_b2.get("flat_volume_ratio", 0.98)),
            min_yang_bao_yin_body_pct=float(config_b2.get("min_yang_bao_yin_body_pct", 0.003)),
            upper_shadow_soft_limit=float(config_b2.get("upper_shadow_soft_limit", 0.15)),
        )

        date_str = pick_date.strftime("%Y-%m-%d")
        candidates: list[SelectionCandidate] = []
        for code in pool_codes:
            frame = prepared.get(code)
            if frame is None or pick_date not in frame.index:
                continue
            try:
                prepared_frame = selector.prepare_df(frame)
                if selector.vec_picks_from_prepared(prepared_frame, start=pick_date, end=pick_date):
                    row = prepared_frame.loc[pick_date]
                    candidates.append(
                        SelectionCandidate(
                            code=code,
                            date=date_str,
                            strategy="b2",
                            close=float(row["close"]),
                            turnover_n=float(row["turnover_n"]),
                            extra={
                                "daily_return": float(row["_b2_daily_return"]),
                                "today_body_pct": float(row["_b2_today_body_pct"]),
                                "volume_ratio": float(row["_b2_volume_ratio"]),
                                "prior_b1_lag": int(row["_b2_prior_b1_lag"]),
                                "prior_b1_j": float(row["_b2_prior_b1_j"]),
                                "j": float(row["J"]),
                                "j_turn_up": bool(row["_b2_j_turn_up"]),
                                "strict_yang_bao_yin": bool(row["_b2_strict_yang_bao_yin"]),
                                "upper_shadow_ratio": float(row["_b2_upper_shadow_ratio"]),
                                "b2_quality_score": float(row["_b2_quality_score"]),
                            },
                        )
                    )
            except Exception as exc:
                logger.debug("B2 skip %s: %s", code, exc)

        candidates.sort(key=lambda candidate: float(candidate.extra.get("b2_quality_score", -999)), reverse=True)
        logger.info("B2 选出: %d 只", len(candidates))
        return candidates

    def _run_brick(
        self,
        prepared: dict[str, Any],
        pick_date: Any,
        pool_codes: list[str],
        config: dict[str, Any],
    ) -> list[SelectionCandidate]:
        selector = self.formula_factory.create_brick_selector(
            daily_return_threshold=float(config.get("daily_return_threshold", 0.05)),
            brick_growth_ratio=float(config.get("brick_growth_ratio", 1.0)),
            min_prior_green_bars=int(config.get("min_prior_green_bars", 2)),
            zxdq_ratio=config.get("zxdq_ratio"),
            zxdq_span=int(config.get("zxdq_span", 10)),
            require_zxdq_gt_zxdkx=bool(config.get("require_zxdq_gt_zxdkx", True)),
            zxdkx_m1=int(config.get("zxdkx_m1", 14)),
            zxdkx_m2=int(config.get("zxdkx_m2", 28)),
            zxdkx_m3=int(config.get("zxdkx_m3", 57)),
            zxdkx_m4=int(config.get("zxdkx_m4", 114)),
            require_weekly_ma_bull=bool(config.get("require_weekly_ma_bull", True)),
            wma_short=int(config.get("wma_short", 20)),
            wma_mid=int(config.get("wma_mid", 60)),
            wma_long=int(config.get("wma_long", 120)),
            n=int(config.get("n", 4)),
            m1=int(config.get("m1", 4)),
            m2=int(config.get("m2", 6)),
            m3=int(config.get("m3", 6)),
            t=float(config.get("t", 4.0)),
            shift1=float(config.get("shift1", 90.0)),
            shift2=float(config.get("shift2", 100.0)),
            sma_w1=int(config.get("sma_w1", 1)),
            sma_w2=int(config.get("sma_w2", 1)),
            sma_w3=int(config.get("sma_w3", 1)),
        )

        date_str = pick_date.strftime("%Y-%m-%d")
        candidates: list[SelectionCandidate] = []
        for code in pool_codes:
            frame = prepared.get(code)
            if frame is None or pick_date not in frame.index:
                continue
            try:
                prepared_frame = selector.prepare_df(frame)
                if selector.vec_picks_from_prepared(prepared_frame, start=pick_date, end=pick_date):
                    row = prepared_frame.loc[pick_date]
                    if "brick_growth" in prepared_frame.columns:
                        brick_growth = float(row["brick_growth"])
                    else:
                        brick_growth = float(selector.brick_growth_on_date(prepared_frame, pick_date))
                    candidates.append(
                        SelectionCandidate(
                            code=code,
                            date=date_str,
                            strategy="brick",
                            close=float(row["close"]),
                            turnover_n=float(row["turnover_n"]),
                            brick_growth=brick_growth if math.isfinite(brick_growth) else None,
                        )
                    )
            except Exception as exc:
                logger.debug("Brick skip %s: %s", code, exc)

        candidates.sort(key=lambda candidate: candidate.brick_growth or -999, reverse=True)
        logger.info("Brick 选出: %d 只", len(candidates))
        return candidates


class LegacyStrategySelectorPort:
    def __init__(self, module_provider: Callable[[], Any]):
        self._module_provider = module_provider

    def run_strategies(
        self,
        prepared: dict[str, Any],
        *,
        pick_date: Any,
        pool_codes: list[str],
        config: dict[str, Any],
    ) -> list[Any]:
        module = self._module_provider()
        candidates: list[Any] = []
        if config.get("b1", {}).get("enabled", True):
            candidates.extend(module.run_b1(prepared, pick_date, pool_codes, config["b1"]))
        if config.get("b2", {}).get("enabled", False):
            candidates.extend(module.run_b2(prepared, pick_date, pool_codes, config["b2"], config.get("b1", {})))
        if config.get("brick", {}).get("enabled", True):
            candidates.extend(module.run_brick(prepared, pick_date, pool_codes, config["brick"]))
        return candidates


class LegacyPreselectExecutionPort:
    """Adapter around legacy pipeline selection behavior.

    This keeps legacy imports behind a named port while the product domain
    boundary is rewritten and parity-tested in smaller slices.
    """

    def __init__(
        self,
        module_loader: Callable[[], Any] = _load_legacy_select_stock,
        *,
        market_data: MarketDataPort | None = None,
        market_preparation: MarketPreparationPort | None = None,
        pick_dates: PickDatePort | None = None,
        liquidity_pool: LiquidityPoolPort | None = None,
        strategy_selectors: StrategySelectorPort | None = None,
    ):
        self._module_loader = module_loader
        self._module: Any | None = None
        self.market_data = market_data or ProductCsvMarketDataPort()
        self.market_preparation = market_preparation or ProductMarketPreparationPort()
        self.pick_dates = pick_dates or ProductPickDatePort()
        self.liquidity_pool = liquidity_pool or ProductLiquidityPoolPort()
        self.strategy_selectors = strategy_selectors or ProductStrategySelectorPort(
            ProductStrategyFormulaFactoryPort()
        )

    @property
    def module(self) -> Any:
        if self._module is None:
            self._module = self._module_loader()
        return self._module

    def load_config(self, config_path: str | None = None) -> dict[str, Any]:
        return self.module.load_config(config_path)

    def run_preselect(self, parameters: PreselectParameters) -> tuple[Any, list[Any]]:
        config = self.load_config(parameters.config_path)
        settings = _execution_settings(self.module, config=config, parameters=parameters)
        raw_data = self.market_data.load_raw_data(settings, parameters)
        prepared = self.market_preparation.prepare(
            raw_data,
            config=config,
            settings=settings,
            parameters=parameters,
        )
        pick_date = self.pick_dates.resolve_pick_date(prepared, parameters.pick_date)
        pool_codes = self.liquidity_pool.build_pool(prepared, pick_date=pick_date, settings=settings)
        if not pool_codes:
            return pick_date, []

        candidates = self.strategy_selectors.run_strategies(
            prepared,
            pick_date=pick_date,
            pool_codes=pool_codes,
            config=config,
        )
        return pick_date, _dedupe_candidates_by_code_strategy(candidates)


class PreselectService:
    def __init__(
        self,
        runner: LegacyRunner | None = None,
        *,
        config_loader: ConfigLoader | None = None,
        port: PreselectExecutionPort | None = None,
    ):
        self.runner = runner
        self.config_loader = config_loader
        self.port = port or LegacyPreselectExecutionPort()

    def run(self, parameters: PreselectParameters, *, run_date: str | None = None) -> PreselectResult:
        config = (self.config_loader or self.port.load_config)(parameters.config_path)
        if self.runner is not None:
            pick_ts, legacy_candidates = self.runner(
                config_path=parameters.config_path,
                data_dir=parameters.data_dir,
                end_date=parameters.end_date,
                pick_date=parameters.pick_date,
            )
        else:
            pick_ts, legacy_candidates = self.port.run_preselect(parameters)

        pick_date = pick_ts.strftime("%Y-%m-%d")
        candidates = [_selection_candidate_from_port(candidate) for candidate in legacy_candidates]
        executed_strategies = _enabled_strategies(config)
        meta = {
            "config": parameters.config_path,
            "data_dir": parameters.data_dir,
            "requested_pick_date": parameters.pick_date,
            "end_date": parameters.end_date,
            "total": len(candidates),
            "executed_strategies": executed_strategies,
            "strategy_candidate_counts": _strategy_candidate_counts(executed_strategies, candidates),
        }
        return PreselectResult(
            run_date=run_date or dt.date.today().isoformat(),
            pick_date=pick_date,
            candidates=candidates,
            meta=meta,
        )


def _selection_candidate_from_port(candidate: Any) -> SelectionCandidate:
    brick_growth = getattr(candidate, "brick_growth", None)
    return SelectionCandidate(
        code=str(candidate.code),
        date=str(candidate.date),
        strategy=str(candidate.strategy),
        close=float(candidate.close),
        turnover_n=float(candidate.turnover_n),
        brick_growth=float(brick_growth) if brick_growth is not None else None,
        extra=dict(getattr(candidate, "extra", None) or {}),
    )


def _execution_settings(
    module: Any,
    *,
    config: dict[str, Any],
    parameters: PreselectParameters,
) -> PreselectExecutionSettings:
    global_config = config.get("global", {})
    data_dir = str(module._resolve_cfg_path(parameters.data_dir or global_config.get("data_dir", "./data/raw")))
    n_jobs = global_config.get("n_jobs")
    return PreselectExecutionSettings(
        data_dir=data_dir,
        top_m=int(global_config.get("top_m", 20)),
        n_turnover_days=int(global_config.get("n_turnover_days", 43)),
        min_bars_buffer=int(global_config.get("min_bars_buffer", 10)),
        n_jobs=int(n_jobs) if n_jobs is not None else None,
        prepare_executor=str(global_config.get("prepare_executor", "process")),
    )
