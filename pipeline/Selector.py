"""
Selector.py — 模块化、向量化、Numba 加速选股框架
=================================================

设计原则
--------
1. **每个指标 / 条件独立成 Filter dataclass**
   - ``__call__(hist) -> bool``         逐行点查（回调 / 调试用）
   - ``vec_mask(df)  -> ndarray[bool]`` 全量向量化（批量回测用）

2. **PipelineSelector 基类**
   - ``prepare_df(df)``                  预计算所有中间列，返回含 ``_vec_pick`` 的 df
   - ``passes_df_on_date(df, date)``     单日判断
   - ``vec_picks_from_prepared(df)``     从预计算列批量获取通过日期

3. **Numba 加速**
   - KDJ 递推、砖型图核心循环、连续绿柱计数均使用 ``@njit`` 加速

Selector 一览
-------------
- ``B1Selector``          KDJ 分位 + 知行线 + 周线多头排列
- ``B2Selector``          B1 后强阳 + 量能确认 + J 拐头
- ``BrickChartSelector``  砖型图形态 + 知行线 + 周线多头排列
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence

import numpy as np
import pandas as pd
try:
    from numba import njit as _njit
except ImportError:
    def _njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda func: func

try:
    from stocktrade.domain.selection.indicators import (
        compute_b1_pick_mask as _product_compute_b1_pick_mask,
        compute_b2_pick_mask as _product_compute_b2_pick_mask,
        compute_b2_quality_score as _product_compute_b2_quality_score,
        compute_body_pct as _product_compute_body_pct,
        compute_brick_chart as _product_compute_brick_chart,
        compute_brick_growth as _product_compute_brick_growth,
        compute_brick_pattern_mask as _product_compute_brick_pattern_mask,
        compute_brick_pick_mask as _product_compute_brick_pick_mask,
        compute_brick_values as _product_compute_brick_values,
        compute_daily_return as _product_compute_daily_return,
        compute_kdj as _product_compute_kdj,
        compute_kdj_quantile_mask as _product_compute_kdj_quantile_mask,
        compute_max_volume_not_bearish as _product_compute_max_volume_not_bearish,
        compute_recent_b1_prior_j as _product_compute_recent_b1_prior_j,
        compute_recent_b1_prior_lag as _product_compute_recent_b1_prior_lag,
        compute_strict_yang_bao_yin as _product_compute_strict_yang_bao_yin,
        compute_upper_shadow_ratio as _product_compute_upper_shadow_ratio,
        compute_volume_ratio as _product_compute_volume_ratio,
        compute_weekly_close as _product_compute_weekly_close,
        compute_weekly_ma_bull as _product_compute_weekly_ma_bull,
        compute_zx_condition_mask as _product_compute_zx_condition_mask,
        compute_zx_lines as _product_compute_zx_lines,
        compute_zxdq_ratio_mask as _product_compute_zxdq_ratio_mask,
    )
except ImportError:
    _product_compute_b1_pick_mask = None
    _product_compute_b2_pick_mask = None
    _product_compute_b2_quality_score = None
    _product_compute_body_pct = None
    _product_compute_brick_chart = None
    _product_compute_brick_growth = None
    _product_compute_brick_pattern_mask = None
    _product_compute_brick_pick_mask = None
    _product_compute_brick_values = None
    _product_compute_daily_return = None
    _product_compute_kdj = None
    _product_compute_kdj_quantile_mask = None
    _product_compute_max_volume_not_bearish = None
    _product_compute_recent_b1_prior_j = None
    _product_compute_recent_b1_prior_lag = None
    _product_compute_strict_yang_bao_yin = None
    _product_compute_upper_shadow_ratio = None
    _product_compute_volume_ratio = None
    _product_compute_weekly_close = None
    _product_compute_weekly_ma_bull = None
    _product_compute_zx_condition_mask = None
    _product_compute_zx_lines = None
    _product_compute_zxdq_ratio_mask = None

# =============================================================================
# Numba 加速核心函数
# =============================================================================

# ── KDJ 核心递推 ──────────────────────────────────────────────────────────
@_njit(cache=True)
def _kdj_core(rsv: np.ndarray) -> tuple:          # noqa: UP006
    n = len(rsv)
    K = np.empty(n, dtype=np.float64)
    D = np.empty(n, dtype=np.float64)
    K[0] = D[0] = 50.0
    for i in range(1, n):
        K[i] = 2.0 / 3.0 * K[i - 1] + 1.0 / 3.0 * rsv[i]
        D[i] = 2.0 / 3.0 * D[i - 1] + 1.0 / 3.0 * K[i]
    J = 3.0 * K - 2.0 * D
    return K, D, J

# ── 连续绿柱计数 ──────────────────────────────────────────────────────────
@_njit(cache=True)
def _green_run(brick_vals: np.ndarray) -> np.ndarray:
    """green_run[i] = 截至 i-1 连续绿柱根数（brick < 0）。"""
    n = len(brick_vals)
    out = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        if brick_vals[i - 1] < 0.0:
            out[i] = out[i - 1] + 1
        else:
            out[i] = 0
    return out

# ── 成交量最大日非阴线核心 ───────────────────────────────────────────────
@_njit(cache=True)
def _max_vol_not_bearish(
    vol: np.ndarray, open_: np.ndarray, close: np.ndarray, n: int,
) -> np.ndarray:
    """滚动 n 日窗口内，成交量最大那天不为阴线（close >= open）。"""
    length = len(vol)
    mask = np.zeros(length, dtype=np.bool_)
    for i in range(length):
        start = max(0, i - n + 1)
        max_v   = vol[start]
        max_idx = start
        for j in range(start + 1, i + 1):
            if vol[j] > max_v:
                max_v   = vol[j]
                max_idx = j
        mask[i] = close[max_idx] >= open_[max_idx]
    return mask

# ── 砖型图核心 ────────────────────────────────────────────────────────────
@_njit(cache=True)
def _compute_brick_numba(
    high: np.ndarray, low: np.ndarray, close: np.ndarray,
    n: int, m1: int, m2: int, m3: int,
    t: float, shift1: float, shift2: float,
    sma_w1: int, sma_w2: int, sma_w3: int,
) -> np.ndarray:
        length = len(close)
        hhv = np.empty(length, dtype=np.float64)
        llv = np.empty(length, dtype=np.float64)
        for i in range(length):
            start = max(0, i - n + 1)
            h_max = high[start]; l_min = low[start]
            for j in range(start + 1, i + 1):
                if high[j] > h_max: h_max = high[j]
                if low[j]  < l_min: l_min = low[j]
            hhv[i] = h_max; llv[i] = l_min

        a1 = sma_w1 / m1; b1 = 1.0 - a1
        var2a = np.empty(length, dtype=np.float64)
        for i in range(length):
            rng = hhv[i] - llv[i]
            if rng == 0.0: rng = 0.01
            v1 = (hhv[i] - close[i]) / rng * 100.0 - shift1
            var2a[i] = (v1 + shift2) if i == 0 else (a1 * v1 + b1 * (var2a[i - 1] - shift2) + shift2)

        a2 = sma_w2 / m2; b2 = 1.0 - a2
        a3 = sma_w3 / m3; b3 = 1.0 - a3
        var4a = np.empty(length, dtype=np.float64)
        var5a = np.empty(length, dtype=np.float64)
        for i in range(length):
            rng = hhv[i] - llv[i]
            if rng == 0.0: rng = 0.01
            v3 = (close[i] - llv[i]) / rng * 100.0
            if i == 0:
                var4a[i] = v3; var5a[i] = v3 + shift2
            else:
                var4a[i] = a2 * v3 + b2 * var4a[i - 1]
                var5a[i] = a3 * var4a[i] + b3 * (var5a[i - 1] - shift2) + shift2

        raw = np.empty(length, dtype=np.float64)
        for i in range(length):
            diff = var5a[i] - var2a[i]
            raw[i] = diff - t if diff > t else 0.0

        brick = np.empty(length, dtype=np.float64)
        brick[0] = 0.0
        for i in range(1, length):
            brick[i] = raw[i] - raw[i - 1]
        return brick


# =============================================================================
# 指标计算辅助函数
# =============================================================================

def compute_kdj(df: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    """返回带 K/D/J 列的 DataFrame（Numba 加速 KDJ 递推）。"""
    if _product_compute_kdj is not None:
        return _product_compute_kdj(df, n=n)

    if df.empty:
        return df.assign(K=np.nan, D=np.nan, J=np.nan)
    low_n  = df["low"].rolling(window=n, min_periods=1).min()
    high_n = df["high"].rolling(window=n, min_periods=1).max()
    rsv    = ((df["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy(dtype=np.float64)

    K, D, J = _kdj_core(rsv)
    return df.assign(K=K, D=D, J=J)


def _tdx_sma(series: pd.Series, period: int, weight: int = 1) -> pd.Series:
    """通达信 SMA(X,N,M)，alpha = weight/period。"""
    return series.ewm(alpha=weight / period, adjust=False).mean()


def compute_zx_lines(
    df: pd.DataFrame,
    m1: int = 14, m2: int = 28, m3: int = 57, m4: int = 114,
    zxdq_span: int = 10,
) -> tuple[pd.Series, pd.Series]:
    """返回 (zxdq, zxdkx)。zxdq=double-EWM；zxdkx=四均线平均。"""
    if _product_compute_zx_lines is not None:
        return _product_compute_zx_lines(df, m1=m1, m2=m2, m3=m3, m4=m4, zxdq_span=zxdq_span)

    close = df["close"].astype(float)
    zxdq  = close.ewm(span=zxdq_span, adjust=False).mean().ewm(span=zxdq_span, adjust=False).mean()
    zxdkx = (
        close.rolling(m1, min_periods=m1).mean()
        + close.rolling(m2, min_periods=m2).mean()
        + close.rolling(m3, min_periods=m3).mean()
        + close.rolling(m4, min_periods=m4).mean()
    ) / 4.0
    return zxdq, zxdkx


def compute_weekly_close(df: pd.DataFrame) -> pd.Series:
    """日线 → 周线收盘价（每周最后一个实际交易日）。

    不依赖固定 resample 锚点（周日/周五），而是直接按
    ISO 周编号分组取最后一行，index 保持为真实交易日日期。
    """
    if _product_compute_weekly_close is not None:
        return _product_compute_weekly_close(df)

    close = (
        df["close"].astype(float)
        if isinstance(df.index, pd.DatetimeIndex)
        else df.set_index("date")["close"].astype(float)
    )
    # 按 ISO 年+周分组，取每组最后一个交易日的收盘价
    # isocalendar().week 返回 1-53，加上年份避免跨年混淆
    idx = close.index
    year_week = idx.isocalendar().year.astype(str) + "-" + idx.isocalendar().week.astype(str).str.zfill(2)
    weekly = close.groupby(year_week).last()
    # 将 index 换回真实日期（每周最后交易日）
    last_date_per_week = close.groupby(year_week).apply(lambda s: s.index[-1])
    weekly.index = pd.DatetimeIndex(last_date_per_week.values)
    return weekly.dropna()


def compute_weekly_ma_bull(
    df: pd.DataFrame,
    ma_periods: tuple[int, int, int] = (20, 60, 120),
) -> pd.Series:
    """
    周线均线多头排列标志（MA_short > MA_mid > MA_long），
    forward-fill 到日线 index，返回 bool Series。

    周线收盘价 index 为真实交易日，reindex 后 ffill 可正确对齐。
    """
    if _product_compute_weekly_ma_bull is not None:
        return _product_compute_weekly_ma_bull(df, ma_periods=ma_periods)

    weekly_close = compute_weekly_close(df)
    s, m, l = ma_periods
    ma_s = weekly_close.rolling(s, min_periods=s).mean()
    ma_m = weekly_close.rolling(m, min_periods=m).mean()
    ma_l = weekly_close.rolling(l, min_periods=l).mean()
    bull = (ma_s > ma_m) & (ma_m > ma_l)

    daily_index = (
        df.index if isinstance(df.index, pd.DatetimeIndex)
        else pd.DatetimeIndex(df["date"])
    )
    # 转 float（1.0/0.0/NaN）→ reindex → ffill → 填 0 → bool
    # 避免 bool reindex 后升级为 object dtype 触发 FutureWarning
    bull_daily = (
        bull.astype(float)
        .reindex(daily_index)
        .ffill()
        .fillna(0.0)
        .astype(bool)
    )
    return bull_daily


def compute_brick_chart(
    df: pd.DataFrame,
    *,
    n: int = 4, m1: int = 4, m2: int = 6, m3: int = 6,
    t: float = 4.0, shift1: float = 90.0, shift2: float = 100.0,
    sma_w1: int = 1, sma_w2: int = 1, sma_w3: int = 1,
) -> pd.Series:
    """通达信砖型图公式 → 砖高 Series（red>0，green<0）。"""
    if _product_compute_brick_chart is not None:
        return _product_compute_brick_chart(
            df,
            n=n,
            m1=m1,
            m2=m2,
            m3=m3,
            t=t,
            shift1=shift1,
            shift2=shift2,
            sma_w1=sma_w1,
            sma_w2=sma_w2,
            sma_w3=sma_w3,
        )

    arr = _compute_brick_numba(
        df["high"].to_numpy(dtype=np.float64),
        df["low"].to_numpy(dtype=np.float64),
        df["close"].to_numpy(dtype=np.float64),
        n, m1, m2, m3, float(t), float(shift1), float(shift2),
        sma_w1, sma_w2, sma_w3,
    )
    return pd.Series(arr, index=df.index, name="brick")



# =============================================================================
# Protocol / 基类
# =============================================================================

class StockFilter(Protocol):
    """单股票过滤器：给定截至 date 的历史 DataFrame，返回是否通过。"""
    def __call__(self, hist: pd.DataFrame) -> bool: ...


class PipelineSelector:
    """
    通用 Selector 基类。

    子类通过 ``prepare_df()`` 预计算中间列（含 ``_vec_pick``），
    之后调用 ``vec_picks_from_prepared()`` 批量获取通过日期（回测提速 10-50×）。
    """

    def __init__(
        self,
        filters: Sequence[StockFilter],
        *,
        date_col: str = "date",
        min_bars: int = 1,
        extra_bars_buffer: int = 0,
    ) -> None:
        self.filters           = list(filters)
        self.date_col          = date_col
        self.min_bars          = int(min_bars)
        self.extra_bars_buffer = int(extra_bars_buffer)

    # ── 内部工具 ─────────────────────────────────────────────────────────────

    def _get_hist(self, df: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        if self.date_col in df.columns:
            return df[df[self.date_col] <= date]
        if isinstance(df.index, pd.DatetimeIndex):
            return df.loc[:date]
        raise KeyError(
            f"DataFrame must have '{self.date_col}' column or a DatetimeIndex."
        )

    def _passes(self, hist: pd.DataFrame) -> bool:
        for f in self.filters:
            if not f(hist):
                return False
        return True

    # ── 公开 API ─────────────────────────────────────────────────────────────

    def get_hist(self, df: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        return self._get_hist(df, date)

    def passes_hist(self, hist: pd.DataFrame) -> bool:
        if hist is None or hist.empty:
            return False
        if len(hist) < self.min_bars + self.extra_bars_buffer:
            return False
        return self._passes(hist)

    def passes_df_on_date(self, df: pd.DataFrame, date: pd.Timestamp) -> bool:
        return self.passes_hist(self._get_hist(df, date))

    def select(self, date: pd.Timestamp, data: Dict[str, pd.DataFrame]) -> List[str]:
        return [
            code for code, df in data.items()
            if self.passes_df_on_date(df, date)
        ]

    # ── 向量化批量接口（子类实现 prepare_df） ────────────────────────────────

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """子类重写：预计算所有中间列及 ``_vec_pick``。"""
        return df

    def vec_picks_from_prepared(
        self,
        df: pd.DataFrame,
        start: Optional[pd.Timestamp] = None,
        end: Optional[pd.Timestamp] = None,
    ) -> List[pd.Timestamp]:
        """从已 prepare_df 的 df 快速获取通过日期列表（比逐日调用快 10-50×）。"""
        if "_vec_pick" not in df.columns:
            return []
        mask = df["_vec_pick"].astype(bool)
        if start is not None:
            mask = mask & (df.index >= start)
        if end is not None:
            mask = mask & (df.index <= end)
        return list(df.index[mask])


# =============================================================================
# ── 独立 Filter 模块 ──────────────────────────────────────────────────────────
#
# 每个 Filter 提供两套接口：
#   __call__(hist: pd.DataFrame) -> bool       点查（含 fallback 计算，调试用）
#   vec_mask(df: pd.DataFrame)  -> np.ndarray  全量向量化（prepare_df 内调用）
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# 1. KDJ 分位过滤
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KDJQuantileFilter:
    """
    J 值过滤：今日 J < j_threshold  OR  今日 J ≤ 历史累积 j_q_threshold 分位。

    vec_mask 使用 expanding().quantile() 保持无未来信息（真正历史分位）。
    """
    j_threshold:   float = -5.0
    j_q_threshold: float = 0.10
    kdj_n:         int   = 9

    def _j_series(self, hist: pd.DataFrame) -> pd.Series:
        if "J" in hist.columns:
            return hist["J"].astype(float)
        return compute_kdj(hist, n=self.kdj_n)["J"].astype(float)

    def __call__(self, hist: pd.DataFrame) -> bool:
        j = self._j_series(hist).dropna()
        if j.empty:
            return False
        j_today = float(j.iloc[-1])
        j_q     = float(j.quantile(self.j_q_threshold))
        return (j_today < self.j_threshold) or (j_today <= j_q)

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        """
        向量化：expanding 历史分位（无未来泄漏）。
        """
        J = self._j_series(df)
        if _product_compute_kdj_quantile_mask is not None:
            return _product_compute_kdj_quantile_mask(
                J,
                j_threshold=self.j_threshold,
                j_q_threshold=self.j_q_threshold,
            )

        j_vals  = J.to_numpy(dtype=float)
        j_q_exp = J.expanding(min_periods=1).quantile(self.j_q_threshold).to_numpy(dtype=float)
        return (j_vals < self.j_threshold) | (j_vals <= j_q_exp)


# ─────────────────────────────────────────────────────────────────────────────
# 2. 知行线条件过滤
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ZXConditionFilter:
    """
    知行线过滤：
      - close > zxdkx（长期均线）      [require_close_gt_long]
      - zxdq  > zxdkx（快线在均线上）  [require_short_gt_long]

    优先读 df 中预计算列 'zxdq' / 'zxdkx'。
    """
    zx_m1:  int = 10
    zx_m2:  int = 50
    zx_m3:  int = 200
    zx_m4:  int = 300
    zxdq_span: int = 10
    require_close_gt_long: bool = True
    require_short_gt_long: bool = True

    def _zx_vals(self, hist: pd.DataFrame) -> tuple[float, float, float]:
        """返回 (zxdq, zxdkx, close) 最新值。"""
        c = float(hist["close"].iloc[-1])
        if "zxdq" in hist.columns and "zxdkx" in hist.columns:
            s = float(hist["zxdq"].iloc[-1])
            lv = hist["zxdkx"].iloc[-1]
            l  = float(lv) if pd.notna(lv) else float("nan")
        else:
            zxdq, zxdkx = compute_zx_lines(
                hist, self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4,
                zxdq_span=self.zxdq_span,
            )
            s = float(zxdq.iloc[-1])
            l = float(zxdkx.iloc[-1]) if pd.notna(zxdkx.iloc[-1]) else float("nan")
        return s, l, c

    def __call__(self, hist: pd.DataFrame) -> bool:
        if hist.empty:
            return False
        s, l, c = self._zx_vals(hist)
        if not (np.isfinite(s) and np.isfinite(l)):
            return False
        if self.require_close_gt_long and not (c > l):
            return False
        if self.require_short_gt_long and not (s > l):
            return False
        return True

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        if "zxdq" in df.columns and "zxdkx" in df.columns:
            zxdq_v  = df["zxdq"].to_numpy(dtype=float)
            zxdkx_v = df["zxdkx"].to_numpy(dtype=float)
        else:
            zs, zk  = compute_zx_lines(
                df, self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4,
                zxdq_span=self.zxdq_span,
            )
            zxdq_v  = zs.to_numpy(dtype=float)
            zxdkx_v = zk.to_numpy(dtype=float)
        if _product_compute_zx_condition_mask is not None:
            return _product_compute_zx_condition_mask(
                df,
                zxdq_v,
                zxdkx_v,
                require_close_gt_long=self.require_close_gt_long,
                require_short_gt_long=self.require_short_gt_long,
            )

        close_v = df["close"].to_numpy(dtype=float)
        mask    = np.isfinite(zxdq_v) & np.isfinite(zxdkx_v)
        if self.require_close_gt_long:
            mask &= close_v > zxdkx_v
        if self.require_short_gt_long:
            mask &= zxdq_v > zxdkx_v
        return mask


# ─────────────────────────────────────────────────────────────────────────────
# 3. 周线均线多头排列过滤
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WeeklyMABullFilter:
    """
    周线均线多头排列：MA_short > MA_mid > MA_long（默认 20/60/120 周）。
    优先读预计算列 'wma_bull'。
    """
    wma_short: int = 20
    wma_mid:   int = 60
    wma_long:  int = 120

    def __call__(self, hist: pd.DataFrame) -> bool:
        if "wma_bull" in hist.columns:
            return bool(hist["wma_bull"].iloc[-1])
        wc = compute_weekly_close(hist)
        if len(wc) < self.wma_long:
            return False
        ma_s = wc.rolling(self.wma_short, min_periods=self.wma_short).mean()
        ma_m = wc.rolling(self.wma_mid,   min_periods=self.wma_mid).mean()
        ma_l = wc.rolling(self.wma_long,  min_periods=self.wma_long).mean()
        sv, mv, lv = float(ma_s.iloc[-1]), float(ma_m.iloc[-1]), float(ma_l.iloc[-1])
        return bool(np.isfinite(sv) and np.isfinite(mv) and np.isfinite(lv) and sv > mv > lv)

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        if "wma_bull" in df.columns:
            return df["wma_bull"].to_numpy(dtype=bool)
        return compute_weekly_ma_bull(
            df, ma_periods=(self.wma_short, self.wma_mid, self.wma_long)
        ).to_numpy(dtype=bool)


# ─────────────────────────────────────────────────────────────────────────────
# 4. 最大成交量非阴线过滤
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MaxVolNotBearishFilter:
    """
    成交量最大日非阴线过滤：
    过去 n 个交易日（含当日）内，成交量最大的那一天不能是阴线
    （阴线定义：close < open）。

    ``vec_mask`` 使用 Numba 加速滚动窗口，复杂度 O(N×n)。
    """
    n: int = 20

    def __call__(self, hist: pd.DataFrame) -> bool:
        window = hist.tail(self.n)
        if window.empty or "volume" not in window.columns:
            return False
        idx_max_vol = window["volume"].idxmax()
        row = window.loc[idx_max_vol]
        return float(row["close"]) >= float(row["open"])

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_max_volume_not_bearish is not None:
            return _product_compute_max_volume_not_bearish(df, lookback=self.n)

        return _max_vol_not_bearish(
            df["volume"].to_numpy(dtype=np.float64),
            df["open"].to_numpy(dtype=np.float64),
            df["close"].to_numpy(dtype=np.float64),
            self.n,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. 砖型图相关 Filter（拆分为独立模块）
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BrickComputeParams:
    """
    砖型图计算参数容器，提供 compute / compute_arr 两个接口。
    作为其他 Filter 的共享配置嵌入使用。
    """
    n:      int   = 4
    m1:     int   = 4
    m2:     int   = 6
    m3:     int   = 6
    t:      float = 4.0
    shift1: float = 90.0
    shift2: float = 100.0
    sma_w1: int   = 1
    sma_w2: int   = 1
    sma_w3: int   = 1

    def compute(self, df: pd.DataFrame) -> pd.Series:
        """返回砖高 Series（index 同 df）。"""
        return compute_brick_chart(
            df, n=self.n, m1=self.m1, m2=self.m2, m3=self.m3,
            t=self.t, shift1=self.shift1, shift2=self.shift2,
            sma_w1=self.sma_w1, sma_w2=self.sma_w2, sma_w3=self.sma_w3,
        )

    def compute_arr(self, df: pd.DataFrame) -> np.ndarray:
        """返回砖高 ndarray（直接调用 Numba JIT，避免 Series 包装开销）。"""
        if _product_compute_brick_values is not None:
            return _product_compute_brick_values(
                df, n=self.n, m1=self.m1, m2=self.m2, m3=self.m3,
                t=self.t, shift1=self.shift1, shift2=self.shift2,
                sma_w1=self.sma_w1, sma_w2=self.sma_w2, sma_w3=self.sma_w3,
            )

        return _compute_brick_numba(
            df["high"].to_numpy(dtype=np.float64),
            df["low"].to_numpy(dtype=np.float64),
            df["close"].to_numpy(dtype=np.float64),
            self.n, self.m1, self.m2, self.m3,
            float(self.t), float(self.shift1), float(self.shift2),
            self.sma_w1, self.sma_w2, self.sma_w3,
        )


@dataclass(frozen=True)
class BrickPatternFilter:
    """
    砖型图形态过滤（条件 1-5）：
      1. 今日涨幅 < daily_return_threshold
      2. 今日红柱（brick > 0）
      3. 昨日绿柱（brick[-1] < 0）
      4. 今日红柱高度 >= brick_growth_ratio × 昨日绿柱绝对高度
      5. 红柱前连续绿柱数 >= min_prior_green_bars

    优先读 df 中预计算列 'brick'。
    """
    daily_return_threshold: float = 0.05
    brick_growth_ratio:     float = 1.0
    min_prior_green_bars:   int   = 1
    brick_params: BrickComputeParams = field(default_factory=BrickComputeParams)

    def _brick_arr(self, hist: pd.DataFrame) -> np.ndarray:
        if "brick" in hist.columns:
            return hist["brick"].to_numpy(dtype=float)
        return self.brick_params.compute_arr(hist)

    def __call__(self, hist: pd.DataFrame) -> bool:
        min_len = max(3, 1 + self.min_prior_green_bars + 1)
        if len(hist) < min_len:
            return False
        close = hist["close"].to_numpy(dtype=float)
        c0, c1 = close[-1], close[-2]
        if c1 <= 0 or (c0 / c1 - 1.0) >= self.daily_return_threshold:
            return False
        vals = self._brick_arr(hist)
        b0, b1 = vals[-1], vals[-2]
        if not (b0 > 0 and b1 < 0):
            return False
        if b0 < self.brick_growth_ratio * abs(b1):
            return False
        # 连续绿柱
        green_count = 1
        i = len(vals) - 3
        while green_count < self.min_prior_green_bars and i > 0:
            if vals[i] < 0:
                green_count += 1
                i -= 1
            else:
                break
        return green_count >= self.min_prior_green_bars

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        """向量化：O(N)，使用预计算 'brick' 列（优先）或实时计算。"""
        bv  = self._brick_arr(df)
        if _product_compute_brick_pattern_mask is not None:
            return _product_compute_brick_pattern_mask(
                df,
                bv,
                daily_return_threshold=self.daily_return_threshold,
                brick_growth_ratio=self.brick_growth_ratio,
                min_prior_green_bars=self.min_prior_green_bars,
            )

        cv  = df["close"].to_numpy(dtype=float)

        # 安全 shift（不用 np.roll，避免边界回绕）
        bp  = np.empty_like(bv);  bp[0]  = np.nan; bp[1:]  = bv[:-1]
        cp  = np.empty_like(cv);  cp[0]  = np.nan; cp[1:]  = cv[:-1]
        abp = np.abs(bp)

        cond_ret    = (cv / cp - 1.0) < self.daily_return_threshold
        cond_red    = bv > 0
        cond_green  = bp < 0
        cond_growth = bv >= self.brick_growth_ratio * abp

        if self.min_prior_green_bars <= 1:
            cond_gc = cond_green
        else:
            gr      = _green_run(bv)
            cond_gc = cond_green & (gr >= self.min_prior_green_bars)

        return cond_ret & cond_red & cond_gc & cond_growth

    def brick_growth_arr(self, df: pd.DataFrame) -> np.ndarray:
        """每日砖型图增长倍数数组（用于 top-k 排序）。"""
        bv  = self._brick_arr(df)
        if _product_compute_brick_growth is not None:
            return _product_compute_brick_growth(bv)

        bp  = np.empty_like(bv); bp[0] = np.nan; bp[1:] = bv[:-1]
        abp = np.abs(bp)
        safe = np.where(abp > 0, abp, 1.0)   # 分母置 1 避免除零警告
        return np.where(abp > 0, bv / safe, bv)


@dataclass(frozen=True)
class ZXDQRatioFilter:
    """
    砖型图选股条件 6：close < zxdq × zxdq_ratio。
    优先读预计算列 'zxdq'。
    """
    zxdq_ratio: float = 1.0
    zxdq_span:  int   = 10
    zxdkx_m1: int = 14; zxdkx_m2: int = 28; zxdkx_m3: int = 57; zxdkx_m4: int = 114

    def _zxdq_arr(self, df: pd.DataFrame) -> np.ndarray:
        if "zxdq" in df.columns:
            return df["zxdq"].to_numpy(dtype=float)
        zs, _ = compute_zx_lines(
            df, self.zxdkx_m1, self.zxdkx_m2, self.zxdkx_m3, self.zxdkx_m4,
            zxdq_span=self.zxdq_span,
        )
        return zs.to_numpy(dtype=float)

    def __call__(self, hist: pd.DataFrame) -> bool:
        zxdq_arr = self._zxdq_arr(hist)
        zv = float(zxdq_arr[-1])
        if not np.isfinite(zv) or zv <= 0:
            return False
        return float(hist["close"].iloc[-1]) < zv * self.zxdq_ratio

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        zxdq_v  = self._zxdq_arr(df)
        if _product_compute_zxdq_ratio_mask is not None:
            return _product_compute_zxdq_ratio_mask(df, zxdq_v, zxdq_ratio=self.zxdq_ratio)

        close_v = df["close"].to_numpy(dtype=float)
        return (
            np.isfinite(zxdq_v)
            & (zxdq_v > 0)
            & (close_v < zxdq_v * self.zxdq_ratio)
        )


# =============================================================================
# ── 具体 Selector 实现 ────────────────────────────────────────────────────────
# =============================================================================

def _apply_vec_filters(df: pd.DataFrame, filters: list) -> np.ndarray:
    """对列表中所有实现了 vec_mask 的 Filter 取交集，返回布尔数组。"""
    mask = np.ones(len(df), dtype=bool)
    for f in filters:
        mask &= f.vec_mask(df)
    return mask


# ─────────────────────────────────────────────────────────────────────────────
# B1Selector
# ─────────────────────────────────────────────────────────────────────────────

class B1Selector(PipelineSelector):
    """
    B1 选股器：
      ① KDJQuantileFilter    — J 值低位（< j_threshold 或历史低分位）
      ② ZXConditionFilter    — close > zxdkx，zxdq > zxdkx
      ③ WeeklyMABullFilter   — 周线多头排列

    ``prepare_df()`` 预计算 K/D/J、zxdq/zxdkx、wma_bull、_vec_pick，
    之后用 ``vec_picks_from_prepared()`` 批量获取选股日期。
    """

    def __init__(
        self,
        j_threshold:          float = -5.0,
        j_q_threshold:        float = 0.10,
        kdj_n:                int   = 9,
        zx_m1:                int   = 10,
        zx_m2:                int   = 50,
        zx_m3:                int   = 200,
        zx_m4:                int   = 300,
        zxdq_span:            int   = 10,
        require_close_gt_long: bool = True,
        require_short_gt_long: bool = True,
        wma_short:            int   = 10,
        wma_mid:              int   = 20,
        wma_long:             int   = 30,
        max_vol_lookback:     Optional[int] = 20,
        *,
        date_col:          str = "date",
        extra_bars_buffer: int = 20,
    ) -> None:
        self._kdj_filter = KDJQuantileFilter(
            j_threshold=j_threshold, j_q_threshold=j_q_threshold, kdj_n=kdj_n,
        )
        self._zx_filter = ZXConditionFilter(
            zx_m1=zx_m1, zx_m2=zx_m2, zx_m3=zx_m3, zx_m4=zx_m4,
            zxdq_span=zxdq_span,
            require_close_gt_long=require_close_gt_long,
            require_short_gt_long=require_short_gt_long,
        )
        self._wma_filter = WeeklyMABullFilter(
            wma_short=wma_short, wma_mid=wma_mid, wma_long=wma_long,
        )
        self._max_vol_filter:MaxVolNotBearishFilter = MaxVolNotBearishFilter(n=max_vol_lookback) 
        _b1_filters: list = [self._kdj_filter, self._zx_filter, self._wma_filter, self._max_vol_filter]
        super().__init__(
            filters=_b1_filters,
            date_col=date_col,
            min_bars=max(30, zx_m4),
            extra_bars_buffer=extra_bars_buffer,
        )
        # 保存参数供 prepare_df
        self.kdj_n    = kdj_n
        self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4 = zx_m1, zx_m2, zx_m3, zx_m4
        self.zxdq_span = zxdq_span
        self.wma_short, self.wma_mid, self.wma_long = wma_short, wma_mid, wma_long

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """预计算知行线、KDJ、周线多头排列、向量化 pick mask。"""
        df = df.copy()
        # 知行线
        zs, zk = compute_zx_lines(
            df, self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4,
            zxdq_span=self.zxdq_span,
        )
        df["zxdq"] = zs; df["zxdkx"] = zk
        # KDJ
        kdj = compute_kdj(df, n=self.kdj_n)
        df["K"] = kdj["K"]; df["D"] = kdj["D"]; df["J"] = kdj["J"]
        # 周线多头排列
        df["wma_bull"] = compute_weekly_ma_bull(
            df, ma_periods=(self.wma_short, self.wma_mid, self.wma_long)
        ).to_numpy()
        # 向量化 pick
        if _product_compute_b1_pick_mask is not None:
            df["_vec_pick"] = _product_compute_b1_pick_mask(
                df,
                j_threshold=self._kdj_filter.j_threshold,
                j_q_threshold=self._kdj_filter.j_q_threshold,
                require_close_gt_long=self._zx_filter.require_close_gt_long,
                require_short_gt_long=self._zx_filter.require_short_gt_long,
                max_vol_lookback=self._max_vol_filter.n,
            )
            return df

        _b1_vec_filters: list = [self._kdj_filter, self._zx_filter, self._wma_filter]
        if self._max_vol_filter is not None:
            _b1_vec_filters.append(self._max_vol_filter)
        df["_vec_pick"] = _apply_vec_filters(df, _b1_vec_filters)
        return df


# ─────────────────────────────────────────────────────────────────────────────
# B2Selector
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DailyReturnFilter:
    """当日收盘涨幅过滤：close / prev_close - 1 >= min_return。"""
    min_return: float = 0.04
    tolerance: float = 1e-12

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 2:
            return False
        close = hist["close"].astype(float)
        prev_close = float(close.iloc[-2])
        if prev_close <= 0:
            return False
        return float(close.iloc[-1]) / prev_close - 1.0 >= self.min_return - self.tolerance

    def values(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_daily_return is not None:
            return _product_compute_daily_return(df)

        close = df["close"].to_numpy(dtype=float)
        prev_close = np.empty_like(close)
        prev_close[0] = np.nan
        prev_close[1:] = close[:-1]
        out = np.full(len(close), np.nan, dtype=float)
        np.divide(close, prev_close, out=out, where=prev_close > 0)
        return out - 1.0

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        return self.values(df) >= self.min_return - self.tolerance


@dataclass(frozen=True)
class BullBodyFilter:
    """当日必须是有实体阳线，过滤假阴真阳和十字星。"""
    min_body_pct: float = 0.003

    def __call__(self, hist: pd.DataFrame) -> bool:
        if hist.empty:
            return False
        row = hist.iloc[-1]
        open_ = float(row["open"])
        close = float(row["close"])
        if open_ <= 0 or not close > open_:
            return False
        return (close - open_) / open_ >= self.min_body_pct

    def values(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_body_pct is not None:
            return _product_compute_body_pct(df)

        open_ = df["open"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)
        out = np.full(len(df), np.nan, dtype=float)
        np.divide(close - open_, open_, out=out, where=open_ > 0)
        return out

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        open_ = df["open"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)
        return (close > open_) & (self.values(df) >= self.min_body_pct)


@dataclass(frozen=True)
class JValueCeilingFilter:
    """KDJ J 值必须低于安全上限。"""
    j_ceiling: float = 55.0
    kdj_n: int = 9

    def _j_series(self, hist: pd.DataFrame) -> pd.Series:
        if "J" in hist.columns:
            return hist["J"].astype(float)
        return compute_kdj(hist, n=self.kdj_n)["J"].astype(float)

    def __call__(self, hist: pd.DataFrame) -> bool:
        if hist.empty:
            return False
        j = self._j_series(hist).dropna()
        return bool(not j.empty and float(j.iloc[-1]) < self.j_ceiling)

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        return self._j_series(df).to_numpy(dtype=float) < self.j_ceiling


@dataclass(frozen=True)
class VolumeConfirmFilter:
    """放量通过；近似平量时必须由严格阳包阴补偿。"""
    volume_ratio_min: float = 1.0
    flat_volume_ratio: float = 0.98
    min_today_body_pct: float = 0.003
    min_yang_bao_yin_body_pct: float = 0.003

    def volume_ratio_arr(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_volume_ratio is not None:
            return _product_compute_volume_ratio(df)

        volume = df["volume"].to_numpy(dtype=float)
        prev_volume = np.empty_like(volume)
        prev_volume[0] = np.nan
        prev_volume[1:] = volume[:-1]
        out = np.full(len(df), np.nan, dtype=float)
        np.divide(volume, prev_volume, out=out, where=prev_volume > 0)
        return out

    def strict_yang_bao_yin_arr(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_strict_yang_bao_yin is not None:
            return _product_compute_strict_yang_bao_yin(
                df,
                min_today_body_pct=self.min_today_body_pct,
                min_yang_bao_yin_body_pct=self.min_yang_bao_yin_body_pct,
            )

        open_ = df["open"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)

        prev_open = np.empty_like(open_)
        prev_close = np.empty_like(close)
        prev_open[0] = np.nan
        prev_close[0] = np.nan
        prev_open[1:] = open_[:-1]
        prev_close[1:] = close[:-1]

        prev_body_pct = np.full(len(df), np.nan, dtype=float)
        np.divide(prev_open - prev_close, prev_close, out=prev_body_pct, where=prev_close > 0)

        today_body_pct = np.full(len(df), np.nan, dtype=float)
        np.divide(close - open_, open_, out=today_body_pct, where=open_ > 0)

        prev_bear_body_ok = (
            (prev_close < prev_open)
            & (prev_body_pct >= self.min_yang_bao_yin_body_pct)
        )
        today_bull_body_ok = (
            (close > open_)
            & (today_body_pct >= self.min_today_body_pct)
        )
        return (
            prev_bear_body_ok
            & today_bull_body_ok
            & (open_ <= prev_close)
            & (close >= prev_open)
        )

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 2:
            return False
        prepared = hist.tail(2)
        return bool(self.vec_mask(prepared)[-1])

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        volume_ratio = self.volume_ratio_arr(df)
        vol_up = volume_ratio > self.volume_ratio_min
        vol_flat = volume_ratio >= self.flat_volume_ratio
        return vol_up | (vol_flat & self.strict_yang_bao_yin_arr(df))


@dataclass(frozen=True)
class RecentB1PickFilter:
    """检查 T-1/T-2 等前置窗口是否出现 B1 信号，并取最近一天。"""
    lookback: int = 2

    def prior_lag_arr(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_recent_b1_prior_lag is not None:
            return _product_compute_recent_b1_prior_lag(df, lookback=self.lookback)

        if "_b1_pick" not in df.columns:
            raise KeyError("RecentB1PickFilter requires precomputed '_b1_pick'.")
        b1_pick = df["_b1_pick"].to_numpy(dtype=bool)
        out = np.zeros(len(df), dtype=np.int16)
        for lag in range(1, self.lookback + 1):
            shifted = np.zeros(len(df), dtype=bool)
            shifted[lag:] = b1_pick[:-lag]
            fill = (out == 0) & shifted
            out[fill] = lag
        return out

    def prior_j_arr(self, df: pd.DataFrame, prior_lag: np.ndarray) -> np.ndarray:
        if _product_compute_recent_b1_prior_j is not None:
            return _product_compute_recent_b1_prior_j(df, prior_lag, lookback=self.lookback)

        if "J" not in df.columns:
            raise KeyError("RecentB1PickFilter requires precomputed 'J'.")
        j_vals = df["J"].to_numpy(dtype=float)
        out = np.full(len(df), np.nan, dtype=float)
        for lag in range(1, self.lookback + 1):
            mask = prior_lag == lag
            shifted_j = np.full(len(df), np.nan, dtype=float)
            shifted_j[lag:] = j_vals[:-lag]
            out[mask] = shifted_j[mask]
        return out

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < self.lookback + 1:
            return False
        return bool(self.vec_mask(hist)[-1])

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        return self.prior_lag_arr(df) > 0


class B2Selector(PipelineSelector):
    """
    B2 选股器：
      ① T-1/T-2 满足系统 B1
      ② T 日 J 相对 B1 日拐头向上且 J < j_ceiling
      ③ T 日涨幅 >= min_return 且为实体阳线
      ④ 放量，或近似平量且严格阳包阴
      ⑤ T 日满足知行线与周线多头基础趋势
    """

    def __init__(
        self,
        *,
        # B1 继承参数
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
        max_vol_lookback: Optional[int] = 20,
        # B2 参数
        b1_lookback: int = 2,
        min_return: float = 0.04,
        min_today_body_pct: float = 0.003,
        j_ceiling: float = 55.0,
        require_j_turn_up: bool = True,
        volume_ratio_min: float = 1.0,
        flat_volume_ratio: float = 0.98,
        min_yang_bao_yin_body_pct: float = 0.003,
        upper_shadow_soft_limit: float = 0.15,
        date_col: str = "date",
        extra_bars_buffer: int = 20,
    ) -> None:
        self._b1_kdj_filter = KDJQuantileFilter(
            j_threshold=j_threshold,
            j_q_threshold=j_q_threshold,
            kdj_n=kdj_n,
        )
        self._zx_filter = ZXConditionFilter(
            zx_m1=zx_m1,
            zx_m2=zx_m2,
            zx_m3=zx_m3,
            zx_m4=zx_m4,
            zxdq_span=zxdq_span,
            require_close_gt_long=True,
            require_short_gt_long=True,
        )
        self._wma_filter = WeeklyMABullFilter(
            wma_short=wma_short,
            wma_mid=wma_mid,
            wma_long=wma_long,
        )
        self._max_vol_filter = MaxVolNotBearishFilter(n=max_vol_lookback) if max_vol_lookback else None
        self._daily_return_filter = DailyReturnFilter(min_return=min_return)
        self._bull_body_filter = BullBodyFilter(min_body_pct=min_today_body_pct)
        self._j_ceiling_filter = JValueCeilingFilter(j_ceiling=j_ceiling, kdj_n=kdj_n)
        self._volume_filter = VolumeConfirmFilter(
            volume_ratio_min=volume_ratio_min,
            flat_volume_ratio=flat_volume_ratio,
            min_today_body_pct=min_today_body_pct,
            min_yang_bao_yin_body_pct=min_yang_bao_yin_body_pct,
        )
        self._recent_b1_filter = RecentB1PickFilter(lookback=b1_lookback)
        super().__init__(
            filters=[],
            date_col=date_col,
            min_bars=max(30, zx_m4, wma_long * 5),
            extra_bars_buffer=extra_bars_buffer + b1_lookback,
        )
        self.kdj_n = kdj_n
        self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4 = zx_m1, zx_m2, zx_m3, zx_m4
        self.zxdq_span = zxdq_span
        self.wma_short, self.wma_mid, self.wma_long = wma_short, wma_mid, wma_long
        self.require_j_turn_up = require_j_turn_up
        self.upper_shadow_soft_limit = upper_shadow_soft_limit

    def _b1_pick_mask(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_b1_pick_mask is not None:
            return _product_compute_b1_pick_mask(
                df,
                j_threshold=self._b1_kdj_filter.j_threshold,
                j_q_threshold=self._b1_kdj_filter.j_q_threshold,
                require_close_gt_long=self._zx_filter.require_close_gt_long,
                require_short_gt_long=self._zx_filter.require_short_gt_long,
                max_vol_lookback=(
                    self._max_vol_filter.n
                    if self._max_vol_filter is not None
                    else None
                ),
            )

        filters: list = [self._b1_kdj_filter, self._zx_filter, self._wma_filter]
        if self._max_vol_filter is not None:
            filters.append(self._max_vol_filter)
        return _apply_vec_filters(df, filters)

    def _upper_shadow_ratio(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_upper_shadow_ratio is not None:
            return _product_compute_upper_shadow_ratio(df)

        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)
        span = high - low
        out = np.zeros(len(df), dtype=float)
        np.divide(high - close, span, out=out, where=span > 0)
        return out

    def _quality_score(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_b2_quality_score is not None:
            return _product_compute_b2_quality_score(
                df,
                upper_shadow_soft_limit=self.upper_shadow_soft_limit,
            )

        score = np.full(len(df), 100.0, dtype=float)
        j_vals = df["J"].to_numpy(dtype=float)
        prior_j = df["_b2_prior_b1_j"].to_numpy(dtype=float)
        j_delta = j_vals - prior_j
        volume_ratio = df["_b2_volume_ratio"].to_numpy(dtype=float)
        body_pct = df["_b2_today_body_pct"].to_numpy(dtype=float)
        upper_shadow = df["_b2_upper_shadow_ratio"].to_numpy(dtype=float)

        score += np.where(j_vals < 25.0, 5.0, 0.0)
        score += np.where((j_vals >= 45.0) & (j_vals < 55.0), -5.0, 0.0)
        score += np.where(j_delta >= 10.0, 5.0, 0.0)
        score += np.where(volume_ratio > 1.2, 8.0, np.where(volume_ratio > 1.0, 3.0, 0.0))
        score += np.where(body_pct >= 0.03, 5.0, np.where(body_pct < 0.01, -5.0, 0.0))
        score += np.where(upper_shadow <= 0.03, 5.0, 0.0)
        score += np.where(upper_shadow > self.upper_shadow_soft_limit, -10.0, 0.0)
        return score

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """预计算 B1 前置、B2 中间列和向量化 pick mask。"""
        df = df.copy()
        zs, zk = compute_zx_lines(
            df,
            self.zx_m1,
            self.zx_m2,
            self.zx_m3,
            self.zx_m4,
            zxdq_span=self.zxdq_span,
        )
        df["zxdq"] = zs
        df["zxdkx"] = zk
        kdj = compute_kdj(df, n=self.kdj_n)
        df["K"] = kdj["K"]
        df["D"] = kdj["D"]
        df["J"] = kdj["J"]
        df["wma_bull"] = compute_weekly_ma_bull(
            df,
            ma_periods=(self.wma_short, self.wma_mid, self.wma_long),
        ).to_numpy()

        df["_b1_pick"] = self._b1_pick_mask(df)
        prior_lag = self._recent_b1_filter.prior_lag_arr(df)
        df["_b2_prior_b1_lag"] = prior_lag
        df["_b2_prior_b1_j"] = self._recent_b1_filter.prior_j_arr(df, prior_lag)
        df["_b2_j_turn_up"] = df["J"].to_numpy(dtype=float) > df["_b2_prior_b1_j"].to_numpy(dtype=float)
        df["_b2_daily_return"] = self._daily_return_filter.values(df)
        df["_b2_today_body_pct"] = self._bull_body_filter.values(df)
        df["_b2_volume_ratio"] = self._volume_filter.volume_ratio_arr(df)
        df["_b2_strict_yang_bao_yin"] = self._volume_filter.strict_yang_bao_yin_arr(df)
        df["_b2_upper_shadow_ratio"] = self._upper_shadow_ratio(df)
        df["_b2_quality_score"] = self._quality_score(df)

        if _product_compute_b2_pick_mask is not None:
            df["_vec_pick"] = _product_compute_b2_pick_mask(
                df,
                require_j_turn_up=self.require_j_turn_up,
                j_ceiling=self._j_ceiling_filter.j_ceiling,
                min_return=self._daily_return_filter.min_return,
                return_tolerance=self._daily_return_filter.tolerance,
                min_today_body_pct=self._bull_body_filter.min_body_pct,
                volume_ratio_min=self._volume_filter.volume_ratio_min,
                flat_volume_ratio=self._volume_filter.flat_volume_ratio,
            )
            return df

        current_zx_ok = self._zx_filter.vec_mask(df)
        current_weekly_ok = self._wma_filter.vec_mask(df)
        recent_b1_ok = prior_lag > 0
        j_turn_up_ok = (
            df["_b2_j_turn_up"].to_numpy(dtype=bool)
            if self.require_j_turn_up
            else np.ones(len(df), dtype=bool)
        )
        df["_vec_pick"] = (
            current_zx_ok
            & current_weekly_ok
            & recent_b1_ok
            & j_turn_up_ok
            & self._j_ceiling_filter.vec_mask(df)
            & self._daily_return_filter.vec_mask(df)
            & self._bull_body_filter.vec_mask(df)
            & self._volume_filter.vec_mask(df)
        )
        return df

    def passes_hist(self, hist: pd.DataFrame) -> bool:
        if hist is None or hist.empty:
            return False
        if len(hist) < self.min_bars + self.extra_bars_buffer:
            return False
        prepared = self.prepare_df(hist)
        return bool(prepared["_vec_pick"].iloc[-1])


# ─────────────────────────────────────────────────────────────────────────────
# BrickChartSelector
# ─────────────────────────────────────────────────────────────────────────────

class BrickChartSelector(PipelineSelector):
    """
    砖型图选股器，由以下四个独立模块组成：
      ① BrickPatternFilter   — 形态（红/绿柱 + 涨幅 + 连续绿柱数）
      ② ZXDQRatioFilter      — close < zxdq × ratio          [可选]
      ③ ZXConditionFilter     — zxdq > zxdkx                  [可选]
      ④ WeeklyMABullFilter   — 周线多头排列                    [可选]

    快速用法::

        sel = BrickChartSelector()
        pf  = sel.prepare_df(daily_df)          # 一次性预计算（O(N)）
        dates = sel.vec_picks_from_prepared(pf)  # 批量获取通过日期

    超参搜索时，先 prepare_df() 预热慢速列，然后对每组砖型图参数
    调用 prepare_df_brick_only()（快 3-10×）。
    """

    def __init__(
        self,
        *,
        # ── 砖型图形态参数 ──
        daily_return_threshold: float = 0.05,
        brick_growth_ratio:     float = 1.0,
        min_prior_green_bars:   int   = 1,
        # ── 砖型图计算参数 ──
        n:      int   = 4,  m1: int = 4, m2: int = 6, m3: int = 6,
        t:      float = 4.0, shift1: float = 90.0, shift2: float = 100.0,
        sma_w1: int   = 1,   sma_w2: int = 1, sma_w3: int = 1,
        # ── 知行线参数 ──
        zxdq_span:  int = 10,
        zxdkx_m1: int = 14, zxdkx_m2: int = 28,
        zxdkx_m3: int = 57, zxdkx_m4: int = 114,
        # ── 条件开关 ──
        zxdq_ratio:             Optional[float] = 1.0,
        require_zxdq_gt_zxdkx: bool = True,
        require_weekly_ma_bull: bool = True,
        # ── 周线参数 ──
        wma_short: int = 20, wma_mid: int = 60, wma_long: int = 120,
        # ── 基类参数 ──
        date_col:          str = "date",
        extra_bars_buffer: int = 10,
    ) -> None:
        self._bp = BrickComputeParams(
            n=n, m1=m1, m2=m2, m3=m3,
            t=t, shift1=shift1, shift2=shift2,
            sma_w1=sma_w1, sma_w2=sma_w2, sma_w3=sma_w3,
        )
        self._pattern_filter = BrickPatternFilter(
            daily_return_threshold=daily_return_threshold,
            brick_growth_ratio=brick_growth_ratio,
            min_prior_green_bars=min_prior_green_bars,
            brick_params=self._bp,
        )
        self._zxdq_ratio_filter: Optional[ZXDQRatioFilter] = (
            ZXDQRatioFilter(
                zxdq_ratio=zxdq_ratio, zxdq_span=zxdq_span,
                zxdkx_m1=zxdkx_m1, zxdkx_m2=zxdkx_m2,
                zxdkx_m3=zxdkx_m3, zxdkx_m4=zxdkx_m4,
            ) if zxdq_ratio is not None else None
        )
        self._zxdq_gt_filter: Optional[ZXConditionFilter] = (
            ZXConditionFilter(
                zx_m1=zxdkx_m1, zx_m2=zxdkx_m2,
                zx_m3=zxdkx_m3, zx_m4=zxdkx_m4,
                zxdq_span=zxdq_span,
                require_close_gt_long=False,
                require_short_gt_long=True,
            ) if require_zxdq_gt_zxdkx else None
        )
        self._wma_filter: Optional[WeeklyMABullFilter] = (
            WeeklyMABullFilter(wma_short=wma_short, wma_mid=wma_mid, wma_long=wma_long)
            if require_weekly_ma_bull else None
        )

        # 传给基类的 filters（用于 _passes / passes_hist）
        _filters: list = [self._pattern_filter]
        if self._zxdq_ratio_filter is not None: _filters.append(self._zxdq_ratio_filter)
        if self._zxdq_gt_filter    is not None: _filters.append(self._zxdq_gt_filter)
        if self._wma_filter        is not None: _filters.append(self._wma_filter)

        super().__init__(
            _filters, date_col=date_col,
            min_bars=max(n + 3, 1 + min_prior_green_bars + 1, zxdkx_m4, wma_long * 5),
            extra_bars_buffer=extra_bars_buffer,
        )
        # 保存参数供 prepare_df
        self.zxdq_span  = zxdq_span
        self.zxdkx_m1, self.zxdkx_m2 = zxdkx_m1, zxdkx_m2
        self.zxdkx_m3, self.zxdkx_m4 = zxdkx_m3, zxdkx_m4
        self.wma_short, self.wma_mid, self.wma_long = wma_short, wma_mid, wma_long
        self.require_weekly_ma_bull = require_weekly_ma_bull

    # ── 预计算辅助 ─────────────────────────────────────────────────────────

    def _precompute_zx_wma(self, df: pd.DataFrame) -> None:
        """就地写入 zxdq / zxdkx / wma_bull。"""
        zs, zk = compute_zx_lines(
            df, self.zxdkx_m1, self.zxdkx_m2, self.zxdkx_m3, self.zxdkx_m4,
            zxdq_span=self.zxdq_span,
        )
        df["zxdq"] = zs; df["zxdkx"] = zk
        if self.require_weekly_ma_bull:
            df["wma_bull"] = compute_weekly_ma_bull(
                df, ma_periods=(self.wma_short, self.wma_mid, self.wma_long)
            ).to_numpy()

    def _precompute_brick(self, df: pd.DataFrame) -> None:
        """就地写入 brick / brick_growth。"""
        bv   = self._bp.compute_arr(df)
        df["brick"]        = bv
        if _product_compute_brick_growth is not None:
            df["brick_growth"] = _product_compute_brick_growth(bv)
            return

        bp_  = np.empty_like(bv); bp_[0] = np.nan; bp_[1:] = bv[:-1]
        abp  = np.abs(bp_)
        safe = np.where(abp > 0, abp, 1.0)    # 分母置 1 避免除零警告
        df["brick_growth"] = np.where(abp > 0, bv / safe, bv)

    def _compute_vec_pick(self, df: pd.DataFrame) -> np.ndarray:
        if _product_compute_brick_pick_mask is not None:
            brick_values = (
                df["brick"].to_numpy(dtype=float)
                if "brick" in df.columns
                else self._bp.compute_arr(df)
            )
            return _product_compute_brick_pick_mask(
                df,
                brick_values,
                daily_return_threshold=self._pattern_filter.daily_return_threshold,
                brick_growth_ratio=self._pattern_filter.brick_growth_ratio,
                min_prior_green_bars=self._pattern_filter.min_prior_green_bars,
                zxdq_ratio=(
                    self._zxdq_ratio_filter.zxdq_ratio
                    if self._zxdq_ratio_filter is not None
                    else None
                ),
                require_zxdq_gt_zxdkx=self._zxdq_gt_filter is not None,
                require_weekly_ma_bull=self._wma_filter is not None,
            )

        fs: list = [self._pattern_filter]
        if self._zxdq_ratio_filter is not None: fs.append(self._zxdq_ratio_filter)
        if self._zxdq_gt_filter    is not None: fs.append(self._zxdq_gt_filter)
        if self._wma_filter        is not None: fs.append(self._wma_filter)
        return _apply_vec_filters(df, fs)

    # ── 公开接口 ───────────────────────────────────────────────────────────

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        完整预计算：brick + zxdq/zxdkx + wma_bull + brick_growth + _vec_pick。
        返回 df 副本。
        """
        df = df.copy()
        self._precompute_zx_wma(df)
        self._precompute_brick(df)
        df["_vec_pick"] = self._compute_vec_pick(df)
        return df

    def prepare_df_brick_only(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        轻量版 prepare_df：假设 zxdq / zxdkx / wma_bull 列已存在，
        仅重新计算 brick / brick_growth / _vec_pick。
        **就地修改 df**（不 copy），超参搜索内层循环专用，速度快 3-10×。
        """
        self._precompute_brick(df)
        df["_vec_pick"] = self._compute_vec_pick(df)
        return df

    def brick_growth_on_date(self, df: pd.DataFrame, date: pd.Timestamp) -> float:
        """
        返回指定日期的砖型图增长倍数（top-k 排序用）。
        优先读预计算列 'brick_growth'。
        """
        hist = self._get_hist(df, date)
        if len(hist) < 3:
            return -np.inf
        if "brick_growth" in hist.columns:
            val = float(hist["brick_growth"].iloc[-1])
            return val if np.isfinite(val) else -np.inf
        return float(self._pattern_filter.brick_growth_arr(hist)[-1])


# =============================================================================
# AnySelector Protocol（外部类型提示用）
# =============================================================================

class AnySelector(Protocol):
    """外部代码面向接口编程时使用的 Protocol 类型。"""
    def passes_df_on_date(self, df: pd.DataFrame, date: pd.Timestamp) -> bool: ...
    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame: ...
    def vec_picks_from_prepared(
        self, df: pd.DataFrame,
        start: Optional[pd.Timestamp] = None,
        end:   Optional[pd.Timestamp] = None,
    ) -> List[pd.Timestamp]: ...
