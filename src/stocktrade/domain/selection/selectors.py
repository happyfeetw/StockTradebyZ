from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .indicators import (
    compute_b1_pick_mask,
    compute_b2_pick_mask,
    compute_b2_quality_score,
    compute_body_pct,
    compute_brick_growth,
    compute_brick_pick_mask,
    compute_brick_values,
    compute_daily_return,
    compute_kdj,
    compute_recent_b1_prior_j,
    compute_recent_b1_prior_lag,
    compute_strict_yang_bao_yin,
    compute_upper_shadow_ratio,
    compute_volume_ratio,
    compute_weekly_ma_bull,
    compute_zx_lines,
)


def _vec_picks_from_prepared(
    frame: pd.DataFrame,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> list[pd.Timestamp]:
    if "_vec_pick" not in frame.columns:
        return []
    mask = frame["_vec_pick"].astype(bool)
    if start is not None:
        mask = mask & (frame.index >= start)
    if end is not None:
        mask = mask & (frame.index <= end)
    return list(frame.index[mask])


def _history_until(frame: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    if "date" in frame.columns:
        return frame[frame["date"] <= date]
    if isinstance(frame.index, pd.DatetimeIndex):
        return frame.loc[:date]
    raise KeyError("DataFrame must have 'date' column or a DatetimeIndex.")


class ProductB1Selector:
    def __init__(
        self,
        j_threshold: float = -5.0,
        j_q_threshold: float = 0.10,
        kdj_n: int = 9,
        zx_m1: int = 10,
        zx_m2: int = 50,
        zx_m3: int = 200,
        zx_m4: int = 300,
        zxdq_span: int = 10,
        require_close_gt_long: bool = True,
        require_short_gt_long: bool = True,
        wma_short: int = 10,
        wma_mid: int = 20,
        wma_long: int = 30,
        max_vol_lookback: int | None = 20,
        **_: Any,
    ) -> None:
        self.j_threshold = float(j_threshold)
        self.j_q_threshold = float(j_q_threshold)
        self.kdj_n = int(kdj_n)
        self.zx_m1 = int(zx_m1)
        self.zx_m2 = int(zx_m2)
        self.zx_m3 = int(zx_m3)
        self.zx_m4 = int(zx_m4)
        self.zxdq_span = int(zxdq_span)
        self.require_close_gt_long = bool(require_close_gt_long)
        self.require_short_gt_long = bool(require_short_gt_long)
        self.wma_short = int(wma_short)
        self.wma_mid = int(wma_mid)
        self.wma_long = int(wma_long)
        self.max_vol_lookback = int(max_vol_lookback) if max_vol_lookback is not None else None

    def prepare_df(self, frame: pd.DataFrame) -> pd.DataFrame:
        prepared = frame.copy()
        zxdq, zxdkx = compute_zx_lines(
            prepared,
            self.zx_m1,
            self.zx_m2,
            self.zx_m3,
            self.zx_m4,
            zxdq_span=self.zxdq_span,
        )
        prepared["zxdq"] = zxdq
        prepared["zxdkx"] = zxdkx
        kdj = compute_kdj(prepared, n=self.kdj_n)
        prepared["K"] = kdj["K"]
        prepared["D"] = kdj["D"]
        prepared["J"] = kdj["J"]
        prepared["wma_bull"] = compute_weekly_ma_bull(
            prepared,
            ma_periods=(self.wma_short, self.wma_mid, self.wma_long),
        ).to_numpy()
        prepared["_vec_pick"] = compute_b1_pick_mask(
            prepared,
            j_threshold=self.j_threshold,
            j_q_threshold=self.j_q_threshold,
            require_close_gt_long=self.require_close_gt_long,
            require_short_gt_long=self.require_short_gt_long,
            max_vol_lookback=self.max_vol_lookback,
        )
        return prepared

    def vec_picks_from_prepared(
        self,
        frame: pd.DataFrame,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> list[pd.Timestamp]:
        return _vec_picks_from_prepared(frame, start=start, end=end)


class ProductB2Selector:
    def __init__(
        self,
        *,
        j_threshold: float = 15.0,
        j_q_threshold: float = 0.10,
        kdj_n: int = 9,
        zx_m1: int = 14,
        zx_m2: int = 28,
        zx_m3: int = 57,
        zx_m4: int = 114,
        zxdq_span: int = 10,
        wma_short: int = 10,
        wma_mid: int = 20,
        wma_long: int = 30,
        max_vol_lookback: int | None = 20,
        b1_lookback: int = 2,
        min_return: float = 0.04,
        min_today_body_pct: float = 0.003,
        j_ceiling: float = 55.0,
        require_j_turn_up: bool = True,
        volume_ratio_min: float = 1.0,
        flat_volume_ratio: float = 0.98,
        min_yang_bao_yin_body_pct: float = 0.003,
        upper_shadow_soft_limit: float = 0.15,
        **_: Any,
    ) -> None:
        self.b1_selector = ProductB1Selector(
            j_threshold=j_threshold,
            j_q_threshold=j_q_threshold,
            kdj_n=kdj_n,
            zx_m1=zx_m1,
            zx_m2=zx_m2,
            zx_m3=zx_m3,
            zx_m4=zx_m4,
            zxdq_span=zxdq_span,
            wma_short=wma_short,
            wma_mid=wma_mid,
            wma_long=wma_long,
            max_vol_lookback=max_vol_lookback,
        )
        self.b1_lookback = int(b1_lookback)
        self.min_return = float(min_return)
        self.return_tolerance = 1e-12
        self.min_today_body_pct = float(min_today_body_pct)
        self.j_ceiling = float(j_ceiling)
        self.require_j_turn_up = bool(require_j_turn_up)
        self.volume_ratio_min = float(volume_ratio_min)
        self.flat_volume_ratio = float(flat_volume_ratio)
        self.min_yang_bao_yin_body_pct = float(min_yang_bao_yin_body_pct)
        self.upper_shadow_soft_limit = float(upper_shadow_soft_limit)

    def _upper_shadow_ratio(self, frame: pd.DataFrame) -> np.ndarray:
        return compute_upper_shadow_ratio(frame)

    def prepare_df(self, frame: pd.DataFrame) -> pd.DataFrame:
        prepared = self.b1_selector.prepare_df(frame)
        prepared["_b1_pick"] = prepared["_vec_pick"].to_numpy(dtype=bool)
        prior_lag = compute_recent_b1_prior_lag(prepared, lookback=self.b1_lookback)
        prepared["_b2_prior_b1_lag"] = prior_lag
        prepared["_b2_prior_b1_j"] = compute_recent_b1_prior_j(
            prepared,
            prior_lag,
            lookback=self.b1_lookback,
        )
        prepared["_b2_j_turn_up"] = (
            prepared["J"].to_numpy(dtype=float) > prepared["_b2_prior_b1_j"].to_numpy(dtype=float)
        )
        prepared["_b2_daily_return"] = compute_daily_return(prepared)
        prepared["_b2_today_body_pct"] = compute_body_pct(prepared)
        prepared["_b2_volume_ratio"] = compute_volume_ratio(prepared)
        prepared["_b2_strict_yang_bao_yin"] = compute_strict_yang_bao_yin(
            prepared,
            min_today_body_pct=self.min_today_body_pct,
            min_yang_bao_yin_body_pct=self.min_yang_bao_yin_body_pct,
        )
        prepared["_b2_upper_shadow_ratio"] = self._upper_shadow_ratio(prepared)
        prepared["_b2_quality_score"] = compute_b2_quality_score(
            prepared,
            upper_shadow_soft_limit=self.upper_shadow_soft_limit,
        )
        prepared["_vec_pick"] = compute_b2_pick_mask(
            prepared,
            require_j_turn_up=self.require_j_turn_up,
            j_ceiling=self.j_ceiling,
            min_return=self.min_return,
            return_tolerance=self.return_tolerance,
            min_today_body_pct=self.min_today_body_pct,
            volume_ratio_min=self.volume_ratio_min,
            flat_volume_ratio=self.flat_volume_ratio,
        )
        return prepared

    def vec_picks_from_prepared(
        self,
        frame: pd.DataFrame,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> list[pd.Timestamp]:
        return _vec_picks_from_prepared(frame, start=start, end=end)


class ProductBrickChartSelector:
    def __init__(
        self,
        *,
        daily_return_threshold: float = 0.05,
        brick_growth_ratio: float = 1.0,
        min_prior_green_bars: int = 1,
        n: int = 4,
        m1: int = 4,
        m2: int = 6,
        m3: int = 6,
        t: float = 4.0,
        shift1: float = 90.0,
        shift2: float = 100.0,
        sma_w1: int = 1,
        sma_w2: int = 1,
        sma_w3: int = 1,
        zxdq_span: int = 10,
        zxdkx_m1: int = 14,
        zxdkx_m2: int = 28,
        zxdkx_m3: int = 57,
        zxdkx_m4: int = 114,
        zxdq_ratio: float | None = 1.0,
        require_zxdq_gt_zxdkx: bool = True,
        require_weekly_ma_bull: bool = True,
        wma_short: int = 20,
        wma_mid: int = 60,
        wma_long: int = 120,
        **_: Any,
    ) -> None:
        self.daily_return_threshold = float(daily_return_threshold)
        self.brick_growth_ratio = float(brick_growth_ratio)
        self.min_prior_green_bars = int(min_prior_green_bars)
        self.n = int(n)
        self.m1 = int(m1)
        self.m2 = int(m2)
        self.m3 = int(m3)
        self.t = float(t)
        self.shift1 = float(shift1)
        self.shift2 = float(shift2)
        self.sma_w1 = int(sma_w1)
        self.sma_w2 = int(sma_w2)
        self.sma_w3 = int(sma_w3)
        self.zxdq_span = int(zxdq_span)
        self.zxdkx_m1 = int(zxdkx_m1)
        self.zxdkx_m2 = int(zxdkx_m2)
        self.zxdkx_m3 = int(zxdkx_m3)
        self.zxdkx_m4 = int(zxdkx_m4)
        self.zxdq_ratio = None if zxdq_ratio is None else float(zxdq_ratio)
        self.require_zxdq_gt_zxdkx = bool(require_zxdq_gt_zxdkx)
        self.require_weekly_ma_bull = bool(require_weekly_ma_bull)
        self.wma_short = int(wma_short)
        self.wma_mid = int(wma_mid)
        self.wma_long = int(wma_long)

    def _compute_brick_values(self, frame: pd.DataFrame) -> np.ndarray:
        return compute_brick_values(
            frame,
            n=self.n,
            m1=self.m1,
            m2=self.m2,
            m3=self.m3,
            t=self.t,
            shift1=self.shift1,
            shift2=self.shift2,
            sma_w1=self.sma_w1,
            sma_w2=self.sma_w2,
            sma_w3=self.sma_w3,
        )

    def _precompute_zx_wma(self, frame: pd.DataFrame) -> None:
        zxdq, zxdkx = compute_zx_lines(
            frame,
            self.zxdkx_m1,
            self.zxdkx_m2,
            self.zxdkx_m3,
            self.zxdkx_m4,
            zxdq_span=self.zxdq_span,
        )
        frame["zxdq"] = zxdq
        frame["zxdkx"] = zxdkx
        if self.require_weekly_ma_bull:
            frame["wma_bull"] = compute_weekly_ma_bull(
                frame,
                ma_periods=(self.wma_short, self.wma_mid, self.wma_long),
            ).to_numpy()

    def _precompute_brick(self, frame: pd.DataFrame) -> None:
        brick_values = self._compute_brick_values(frame)
        frame["brick"] = brick_values
        frame["brick_growth"] = compute_brick_growth(brick_values)

    def prepare_df(self, frame: pd.DataFrame) -> pd.DataFrame:
        prepared = frame.copy()
        self._precompute_zx_wma(prepared)
        self._precompute_brick(prepared)
        prepared["_vec_pick"] = compute_brick_pick_mask(
            prepared,
            prepared["brick"].to_numpy(dtype=float),
            daily_return_threshold=self.daily_return_threshold,
            brick_growth_ratio=self.brick_growth_ratio,
            min_prior_green_bars=self.min_prior_green_bars,
            zxdq_ratio=self.zxdq_ratio,
            require_zxdq_gt_zxdkx=self.require_zxdq_gt_zxdkx,
            require_weekly_ma_bull=self.require_weekly_ma_bull,
        )
        return prepared

    def prepare_df_brick_only(self, frame: pd.DataFrame) -> pd.DataFrame:
        self._precompute_brick(frame)
        frame["_vec_pick"] = compute_brick_pick_mask(
            frame,
            frame["brick"].to_numpy(dtype=float),
            daily_return_threshold=self.daily_return_threshold,
            brick_growth_ratio=self.brick_growth_ratio,
            min_prior_green_bars=self.min_prior_green_bars,
            zxdq_ratio=self.zxdq_ratio,
            require_zxdq_gt_zxdkx=self.require_zxdq_gt_zxdkx,
            require_weekly_ma_bull=self.require_weekly_ma_bull,
        )
        return frame

    def brick_growth_on_date(self, frame: pd.DataFrame, date: pd.Timestamp) -> float:
        history = _history_until(frame, date)
        if len(history) < 3:
            return -math.inf
        if "brick_growth" in history.columns:
            value = float(history["brick_growth"].iloc[-1])
            return value if math.isfinite(value) else -math.inf
        values = self._compute_brick_values(history)
        growth = compute_brick_growth(values)
        return float(growth[-1])

    def vec_picks_from_prepared(
        self,
        frame: pd.DataFrame,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> list[pd.Timestamp]:
        return _vec_picks_from_prepared(frame, start=start, end=end)


class ProductStrategyFormulaFactoryPort:
    def create_b1_selector(self, **parameters: Any) -> ProductB1Selector:
        return ProductB1Selector(**parameters)

    def create_b2_selector(self, **parameters: Any) -> ProductB2Selector:
        return ProductB2Selector(**parameters)

    def create_brick_selector(self, **parameters: Any) -> ProductBrickChartSelector:
        return ProductBrickChartSelector(**parameters)
