"""
scripts/export_kline_charts.py
AgentTrader · 批量导出候选股票 K线图（日线 + 周线）

用法：
    python scripts/export_kline_charts.py [--date YYYY-MM-DD] [--bars 120] [--weekly-bars 60]

输出目录：
    data/kline/<date>/<code>_<strategy>_day.jpg
    data/kline/<date>/<code>_day.jpg        （兼容旧流程）
    data/kline/<date>/<code>_week.jpg

依赖：
    pip install kaleido   （Plotly 静态图导出必需）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from PIL import Image, ImageDraw, ImageFont

# ── 路径设置 ──────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "dashboard"))

from components.charts import make_daily_chart, make_weekly_chart, prepare_daily_indicators  # noqa: E402


# ── 数据加载 ──────────────────────────────────────────────────────────────────

def _load_candidates(candidates_path: Path) -> tuple[list[dict[str, str]], str]:
    """从 candidates JSON 文件中读取股票代码列表及 pick_date。

    Returns:
        (items, pick_date)  pick_date 为空字符串时表示 JSON 中无该字段。
    """
    if not candidates_path.exists():
        print(f"[ERROR] 候选文件不存在：{candidates_path}")
        sys.exit(1)
    with open(candidates_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, str]] = []
    for candidate in data.get("candidates", []):
        code = str(candidate.get("code") or "")
        if not code:
            continue
        strategy = str(candidate.get("strategy") or "")
        key = (code, strategy)
        if key in seen:
            continue
        seen.add(key)
        items.append({"code": code, "strategy": strategy})
    pick_date = data.get("pick_date", "")
    print(f"[INFO] 候选记录数量：{len(items)}  pick_date：{pick_date or '(未设置)'}  来源：{candidates_path.name}")
    return items, pick_date


def _load_chart_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _safe_strategy_suffix(strategy: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", strategy.strip())


def _load_raw(code: str, raw_dir: Path) -> pd.DataFrame:
    """加载单只股票日线 CSV。"""
    csv = raw_dir / f"{code}.csv"
    if not csv.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv)
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ── 导出单张图 ────────────────────────────────────────────────────────────────

def _export_fig(fig, out_path: Path, width: int, height: int) -> None:
    """将 Plotly Figure 导出为 JPEG。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(
        str(out_path),
        format="jpg",
        width=width,
        height=height,
        scale=1,
    )


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scale(value: float, low: float, high: float, top: int, bottom: int) -> int:
    if not np.isfinite(value) or high <= low:
        return (top + bottom) // 2
    ratio = (value - low) / (high - low)
    return int(bottom - ratio * (bottom - top))


def _draw_line(draw: ImageDraw.ImageDraw, points: list[tuple[int, int]], color: str, width: int = 2) -> None:
    if len(points) >= 2:
        draw.line(points, fill=color, width=width, joint="curve")


def _export_daily_chart_pillow(
    df_raw: pd.DataFrame,
    code: str,
    out_path: Path,
    width: int,
    height: int,
    bars: int,
    *,
    show_zx_lines: bool = True,
    show_brick_panel: bool = False,
    show_ma_lines: bool = False,
    zx_params: dict | None = None,
    brick_params: dict | None = None,
) -> None:
    """用 Pillow 直接绘制日线图，避免 Plotly/Kaleido 启动 Chrome。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = prepare_daily_indicators(
        df_raw,
        zx_params=zx_params or {},
        brick_params=brick_params or {},
    )
    df["zxdq"] = df["_zxdq"]
    df["zxdkx"] = df["_zxdkx"]
    if show_brick_panel:
        df["brick_raw"] = df["_brick"]

    if bars and bars > 0:
        df = df.tail(bars).reset_index(drop=True)
    if df.empty:
        raise ValueError("无可绘制数据")

    for window in (5, 10, 20, 60):
        df[f"ma{window}"] = df["close"].rolling(window, min_periods=1).mean()

    img = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(img)
    title_font = _font(28)
    label_font = _font(18)
    small_font = _font(15)

    margin_l, margin_r = 72, 28
    top_title = 26
    price_top = 82
    if show_brick_panel:
        price_bottom = int(height * 0.56)
        vol_top, vol_bottom = price_bottom + 38, int(height * 0.76)
        brick_top, brick_bottom = vol_bottom + 42, height - 50
    else:
        price_bottom = int(height * 0.66)
        vol_top, vol_bottom = price_bottom + 42, height - 50
        brick_top = brick_bottom = 0
    chart_w = width - margin_l - margin_r
    n = len(df)
    step = chart_w / max(n, 1)
    body_w = max(2, int(step * 0.58))

    price_cols = ["open", "high", "low", "close"]
    if show_ma_lines:
        price_cols.extend(["ma5", "ma10", "ma20", "ma60"])
    if show_zx_lines:
        price_cols.extend(["zxdq", "zxdkx"])
    price_values = pd.concat([pd.to_numeric(df[c], errors="coerce") for c in price_cols])
    price_low = float(price_values.min())
    price_high = float(price_values.max())
    pad = max((price_high - price_low) * 0.08, 0.01)
    price_low -= pad
    price_high += pad
    vol_high = max(float(pd.to_numeric(df["volume"], errors="coerce").max()), 1.0)

    first_date = df["date"].iloc[0].strftime("%Y-%m-%d")
    last_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
    close = float(df["close"].iloc[-1])
    draw.text((margin_l, top_title), f"{code} 日线  {first_date} → {last_date}  收盘 {close:.2f}", fill="#111827", font=title_font)

    for y in np.linspace(price_top, price_bottom, 5):
        draw.line((margin_l, int(y), width - margin_r, int(y)), fill="#e5e7eb", width=1)
    for y in np.linspace(vol_top, vol_bottom, 3):
        draw.line((margin_l, int(y), width - margin_r, int(y)), fill="#edf2f7", width=1)
    if show_brick_panel:
        for y in np.linspace(brick_top, brick_bottom, 3):
            draw.line((margin_l, int(y), width - margin_r, int(y)), fill="#edf2f7", width=1)
    draw.rectangle((margin_l, price_top, width - margin_r, price_bottom), outline="#d1d5db", width=1)
    draw.rectangle((margin_l, vol_top, width - margin_r, vol_bottom), outline="#d1d5db", width=1)
    if show_brick_panel:
        draw.rectangle((margin_l, brick_top, width - margin_r, brick_bottom), outline="#d1d5db", width=1)

    ma_points: dict[str, list[tuple[int, int]]] = {f"ma{w}": [] for w in (5, 10, 20, 60)}
    zx_points: dict[str, list[tuple[int, int]]] = {"zxdq": [], "zxdkx": []}
    brick_values = pd.to_numeric(df["brick_raw"], errors="coerce") if show_brick_panel else pd.Series(dtype=float)
    brick_high = max(float(brick_values.max()) if not brick_values.empty and pd.notna(brick_values.max()) else 1.0, 1.0)
    for idx, row in df.iterrows():
        x = int(margin_l + idx * step + step / 2)
        o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
        up = c >= o
        color = "#d62728" if up else "#16a34a"
        y_open = _scale(o, price_low, price_high, price_top, price_bottom)
        y_close = _scale(c, price_low, price_high, price_top, price_bottom)
        y_high = _scale(h, price_low, price_high, price_top, price_bottom)
        y_low = _scale(l, price_low, price_high, price_top, price_bottom)
        draw.line((x, y_high, x, y_low), fill=color, width=1)
        body_top = min(y_open, y_close)
        body_bottom = max(y_open, y_close)
        if body_bottom == body_top:
            body_bottom += 1
        draw.rectangle((x - body_w // 2, body_top, x + body_w // 2, body_bottom), fill=color, outline=color)

        vol_y = _scale(float(row["volume"]), 0.0, vol_high, vol_top, vol_bottom)
        draw.rectangle((x - body_w // 2, vol_y, x + body_w // 2, vol_bottom), fill=color)

        if show_ma_lines:
            for key in ma_points:
                y = _scale(float(row[key]), price_low, price_high, price_top, price_bottom)
                ma_points[key].append((x, y))
        if show_zx_lines:
            for key in zx_points:
                value = float(row[key])
                if np.isfinite(value):
                    y = _scale(value, price_low, price_high, price_top, price_bottom)
                    zx_points[key].append((x, y))

        if show_brick_panel and idx > 0:
            previous = float(df["brick_raw"].iloc[idx - 1])
            current = float(row["brick_raw"])
            top = _scale(max(previous, current), 0.0, brick_high, brick_top, brick_bottom)
            bottom = _scale(min(previous, current), 0.0, brick_high, brick_top, brick_bottom)
            if bottom == top:
                bottom += 1
            brick_color = "#d62728" if current >= previous else "#16a34a"
            draw.rectangle((x - body_w // 2, top, x + body_w // 2, bottom), fill=brick_color, outline=brick_color)

    ma_colors = {"ma5": "#f59e0b", "ma10": "#2563eb", "ma20": "#7c3aed", "ma60": "#6b7280"}
    if show_ma_lines:
        for key, points in ma_points.items():
            _draw_line(draw, points, ma_colors[key], width=2)
    if show_zx_lines:
        _draw_line(draw, zx_points["zxdq"], "#eab308", width=3)
        _draw_line(draw, zx_points["zxdkx"], "#ffffff", width=5)
        _draw_line(draw, zx_points["zxdkx"], "#111827", width=2)

    legend_x = margin_l
    if show_ma_lines:
        for label, color in [("MA5", "#f59e0b"), ("MA10", "#2563eb"), ("MA20", "#7c3aed"), ("MA60", "#6b7280")]:
            draw.line((legend_x, price_top - 18, legend_x + 28, price_top - 18), fill=color, width=3)
            draw.text((legend_x + 34, price_top - 28), label, fill="#374151", font=small_font)
            legend_x += 112
    if show_zx_lines:
        for label, color in [("知行黄", "#eab308"), ("知行白", "#111827")]:
            draw.line((legend_x, price_top - 18, legend_x + 28, price_top - 18), fill=color, width=3)
            draw.text((legend_x + 34, price_top - 28), label, fill="#374151", font=small_font)
            legend_x += 132

    draw.text((margin_l, price_bottom + 10), "成交量", fill="#374151", font=label_font)
    if show_brick_panel:
        draw.text((margin_l, vol_bottom + 10), "砖型图", fill="#374151", font=label_font)
    draw.text((margin_l, height - 34), first_date, fill="#6b7280", font=small_font)
    draw.text((width - margin_r - 110, height - 34), last_date, fill="#6b7280", font=small_font)
    draw.text((width - margin_r - 90, price_top - 8), f"{price_high:.2f}", fill="#6b7280", font=small_font)
    draw.text((width - margin_r - 90, price_bottom - 18), f"{price_low:.2f}", fill="#6b7280", font=small_font)

    img.save(out_path, format="JPEG", quality=92, optimize=True)


# ── 主流程 ────────────────────────────────────────────────────────────────────

# 配置字典（直接修改此处）
CONFIG = {
    "candidates": str(_ROOT / "data" / "candidates" / "candidates_latest.json"),
    "raw_dir":    str(_ROOT / "data" / "raw"),
    "out_dir":    str(_ROOT / "data" / "kline"),
    "bars":       120,   # 日线显示 K 线数量（0 = 全部）
    "weekly_bars": 60,   # 周线显示 K 线数量（0 = 全部）
    "day_width":  2800,
    "day_height": 1400,
    "week_width": 2800,
    "week_height": 1400,
    "engine": "pillow",  # pillow 不启动浏览器；plotly 需要 Chrome/Kaleido
    "limit": 0,
    "dashboard_config": str(_ROOT / "config" / "dashboard.yaml"),
    "show_ma_lines": False,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出候选股票 K 线图")
    parser.add_argument("--candidates", default=CONFIG["candidates"], help="候选 JSON 文件")
    parser.add_argument("--raw-dir", default=CONFIG["raw_dir"], help="原始 K 线 CSV 目录")
    parser.add_argument("--out-dir", default=CONFIG["out_dir"], help="图表输出根目录")
    parser.add_argument("--dashboard-config", default=CONFIG["dashboard_config"], help="Dashboard 图表配置文件")
    parser.add_argument("--bars", type=int, default=CONFIG["bars"], help="日线显示 K 线数量，0=全部")
    parser.add_argument("--engine", choices=("pillow", "plotly"), default=CONFIG["engine"], help="导出引擎")
    parser.add_argument("--hide-zx-lines", action="store_true", help="Pillow 引擎下不绘制知行线")
    parser.add_argument("--show-ma-lines", action="store_true", default=CONFIG["show_ma_lines"], help="Pillow 引擎下额外绘制 MA5/10/20/60")
    parser.add_argument("--show-brick-panel", choices=("auto", "always", "never"), default="auto", help="Pillow 引擎下是否绘制砖型图副图；auto=仅 brick 策略专属图")
    parser.add_argument("--limit", type=int, default=CONFIG["limit"], help="最多导出几只，0=不限制")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidates_path = Path(CONFIG["candidates"])
    candidates_path = Path(args.candidates)
    raw_dir         = Path(args.raw_dir)
    chart_cfg       = _load_chart_config(Path(args.dashboard_config)).get("chart", {})
    zx_params       = chart_cfg.get("zx_lines") or {}
    brick_params    = chart_cfg.get("brick") or {}

    items, pick_date = _load_candidates(candidates_path)
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    # 导出日期直接读取 candidates.json 的 pick_date
    export_date = pick_date
    if not export_date:
        print("[ERROR] candidates.json 中未设置 pick_date，无法确定导出日期。")
        sys.exit(1)
    print(f"[INFO] 导出日期：{export_date}")

    out_root = Path(args.out_dir) / export_date

    ok_count    = 0
    skip_count  = 0

    exported_legacy_codes: set[str] = set()
    for item in items:
        code = str(item["code"])
        strategy = str(item.get("strategy") or "")
        df_raw = _load_raw(code, raw_dir)
        if df_raw.empty:
            print(f"[SKIP] {code}  — 无日线数据")
            skip_count += 1
            continue

        # ── 日线图 ────────────────────────────────────────────────────
        strategy_suffix = _safe_strategy_suffix(strategy)
        day_path = out_root / f"{code}_{strategy_suffix}_day.jpg" if strategy_suffix else out_root / f"{code}_day.jpg"
        try:
            if args.engine == "plotly":
                fig_day = make_daily_chart(
                    df_raw, code,
                    bars=args.bars,
                    height=CONFIG["day_height"],
                    zx_params=zx_params,
                )
                _export_fig(fig_day, day_path, CONFIG["day_width"], CONFIG["day_height"])
            else:
                show_brick_panel = args.show_brick_panel == "always" or (
                    args.show_brick_panel == "auto" and strategy.lower() == "brick"
                )
                _export_daily_chart_pillow(
                    df_raw,
                    code,
                    day_path,
                    CONFIG["day_width"],
                    CONFIG["day_height"],
                    args.bars,
                    show_zx_lines=not args.hide_zx_lines,
                    show_brick_panel=show_brick_panel,
                    show_ma_lines=args.show_ma_lines,
                    zx_params=zx_params,
                    brick_params=brick_params,
                )
        except Exception as e:
            print(f"[ERROR] {code}_{strategy or 'default'} 日线导出失败：{e}")
            skip_count += 1
            continue

        if code not in exported_legacy_codes:
            legacy_day_path = out_root / f"{code}_day.jpg"
            if day_path != legacy_day_path:
                try:
                    _export_daily_chart_pillow(
                        df_raw,
                        code,
                        legacy_day_path,
                        CONFIG["day_width"],
                        CONFIG["day_height"],
                        args.bars,
                        show_zx_lines=not args.hide_zx_lines,
                        show_brick_panel=False,
                        show_ma_lines=args.show_ma_lines,
                        zx_params=zx_params,
                        brick_params=brick_params,
                    )
                except Exception as e:
                    print(f"[WARN] {code} 兼容日线图导出失败：{e}")
            exported_legacy_codes.add(code)

        # ── 周线图 ────────────────────────────────────────────────────
        # week_path = out_root / f"{code}_week.jpg"
        # try:
        #     fig_week = make_weekly_chart(
        #         df_raw, code,
        #         bars=CONFIG["weekly_bars"],
        #         height=CONFIG["week_height"],
        #     )
        #     _export_fig(fig_week, week_path, CONFIG["week_width"], CONFIG["week_height"])
        # except Exception as e:
        #     print(f"[ERROR] {code} 周线导出失败：{e}")
        #     # 日线已成功，继续计数
        #     print(f"[OK]   {code}  日线 ✓  周线 ✗")
        #     ok_count += 1
        #     continue

        print(f"[OK]   {code}_{strategy or 'default'}  → {day_path.name}")
        ok_count += 1

    print(
        f"\n导出完成：成功 {ok_count} 只，跳过 {skip_count} 只。"
        f"\n输出目录：{out_root}"
    )
    if ok_count == 0 and items:
        print("[ERROR] 没有成功导出任何图表，流程中止，避免后续复评空跑。")
        sys.exit(1)


if __name__ == "__main__":
    main()
