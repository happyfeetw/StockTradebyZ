from __future__ import annotations

import datetime as dt
import sys
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[4]
PIPELINE_DIR = ROOT / "pipeline"


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


def _load_legacy_select_stock() -> Any:
    if str(PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(PIPELINE_DIR))
    import select_stock

    return select_stock


class LegacyMarketDataPort:
    def __init__(self, module_provider: Callable[[], Any]):
        self._module_provider = module_provider

    def load_raw_data(self, settings: PreselectExecutionSettings, parameters: PreselectParameters) -> dict[str, Any]:
        return self._module_provider().load_raw_data(settings.data_dir, end_date=parameters.end_date)


class LegacyMarketPreparationPort:
    def __init__(self, module_provider: Callable[[], Any]):
        self._module_provider = module_provider

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
            warmup_bars=module._calc_warmup(config, settings.min_bars_buffer),
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
        self.market_data = market_data or LegacyMarketDataPort(lambda: self.module)
        self.market_preparation = market_preparation or LegacyMarketPreparationPort(lambda: self.module)
        self.pick_dates = pick_dates or ProductPickDatePort()
        self.liquidity_pool = liquidity_pool or ProductLiquidityPoolPort()
        self.strategy_selectors = strategy_selectors or LegacyStrategySelectorPort(lambda: self.module)

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
