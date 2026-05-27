from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_analytics_reader
from ..schemas.analytics import StrategySummaryResponse, StrategySummaryRow, StrategySummaryTotals
from ..storage.duckdb import DuckDBAnalyticsReadError, DuckDBAnalyticsReader, StrategySummaryMetric

router = APIRouter(tags=["analytics"])


@router.get("/analytics/strategy-summary", response_model=StrategySummaryResponse)
def get_strategy_summary(
    pick_date: str | None = Query(default=None, min_length=10, max_length=10),
    run_id: str | None = Query(default=None, min_length=1, max_length=64),
    strategy: str | None = Query(default=None, min_length=1, max_length=80),
    limit: int = Query(default=100, ge=1, le=500),
    reader: DuckDBAnalyticsReader | None = Depends(get_analytics_reader),
) -> StrategySummaryResponse:
    if reader is None:
        raise HTTPException(status_code=503, detail="analytics database is not configured")
    try:
        metrics = reader.strategy_summary(
            pick_date=pick_date,
            run_id=run_id,
            strategy=strategy,
            limit=limit,
        )
    except DuckDBAnalyticsReadError as exc:
        raise HTTPException(status_code=500, detail="failed to read analytics summary") from exc

    return StrategySummaryResponse(
        rows=[_row(metric) for metric in metrics],
        totals=_totals(metrics),
        filters={"pick_date": pick_date, "run_id": run_id, "strategy": strategy},
    )


def _row(metric: StrategySummaryMetric) -> StrategySummaryRow:
    return StrategySummaryRow(
        pick_date=metric.pick_date,
        run_id=metric.run_id,
        strategy=metric.strategy,
        total=metric.total,
        reviewed=metric.reviewed,
        recommended=metric.recommended,
        unreviewed=metric.unreviewed,
        reviewed_rate=_rate(metric.reviewed + metric.recommended, metric.total),
        recommended_rate=_rate(metric.recommended, metric.total),
    )


def _totals(metrics: list[StrategySummaryMetric]) -> StrategySummaryTotals:
    total = sum(metric.total for metric in metrics)
    reviewed = sum(metric.reviewed for metric in metrics)
    recommended = sum(metric.recommended for metric in metrics)
    unreviewed = sum(metric.unreviewed for metric in metrics)
    return StrategySummaryTotals(
        total=total,
        reviewed=reviewed,
        recommended=recommended,
        unreviewed=unreviewed,
        reviewed_rate=_rate(reviewed + recommended, total),
        recommended_rate=_rate(recommended, total),
        strategies=sorted({metric.strategy for metric in metrics}),
        pick_dates=sorted({metric.pick_date for metric in metrics}, reverse=True),
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)
