from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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


def _load_legacy_select_stock() -> Any:
    if str(PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(PIPELINE_DIR))
    import select_stock

    return select_stock


class PreselectService:
    def __init__(self, runner: LegacyRunner | None = None):
        self.runner = runner

    def run(self, parameters: PreselectParameters, *, run_date: str | None = None) -> PreselectResult:
        legacy_select_stock = _load_legacy_select_stock()
        runner = self.runner or legacy_select_stock.run_preselect
        config = legacy_select_stock.load_config(parameters.config_path)
        pick_ts, legacy_candidates = runner(
            config_path=parameters.config_path,
            data_dir=parameters.data_dir,
            end_date=parameters.end_date,
            pick_date=parameters.pick_date,
        )

        pick_date = pick_ts.strftime("%Y-%m-%d")
        candidates = [
            SelectionCandidate(
                code=str(candidate.code),
                date=str(candidate.date),
                strategy=str(candidate.strategy),
                close=float(candidate.close),
                turnover_n=float(candidate.turnover_n),
                brick_growth=float(candidate.brick_growth) if candidate.brick_growth is not None else None,
                extra=dict(candidate.extra or {}),
            )
            for candidate in legacy_candidates
        ]
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
