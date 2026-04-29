"""
Local AgentTrader workbench.

This is an independent Streamlit entrypoint. It intentionally does not modify
or import dashboard/app.py.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKBENCH_DIR = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "data" / "runs"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

from dashboard.components.charts import make_daily_chart, make_weekly_chart  # noqa: E402


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_candidates() -> dict[str, Any]:
    return load_json(ROOT / "data" / "candidates" / "candidates_latest.json")


def latest_pick_date() -> str:
    return str(load_candidates().get("pick_date", "") or "")


def latest_suggestion() -> dict[str, Any]:
    pick_date = latest_pick_date()
    if not pick_date:
        return {}
    return load_json(ROOT / "data" / "review" / pick_date / "suggestion.json")


def csv_count(path: Path) -> int:
    return len(list(path.glob("*.csv"))) if path.exists() else 0


def environment_status() -> list[tuple[str, str, str]]:
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN")
    raw_dir = ROOT / "data" / "raw"
    candidates = load_candidates()
    suggestion = latest_suggestion()
    return [
        ("Tushare", "ok" if token else "err", "已配置" if token else "未配置"),
        ("Gemini CLI", "ok" if shutil.which("gemini") else "warn", "已安装" if shutil.which("gemini") else "未找到"),
        ("原始数据", "ok" if csv_count(raw_dir) else "warn", f"{csv_count(raw_dir)} 个 CSV"),
        ("最新候选", "ok" if candidates else "warn", candidates.get("pick_date", "无")),
        ("复评汇总", "ok" if suggestion else "warn", "已生成" if suggestion else "无"),
    ]


def render_status_bar() -> None:
    items = []
    for label, state, value in environment_status():
        items.append(
            f"<span class='status-item'><span class='status-dot {state}'></span>"
            f"{label}: {value}</span>"
        )
    st.markdown(f"<div class='workbench-status'>{''.join(items)}</div>", unsafe_allow_html=True)


def ensure_session_state() -> None:
    if "rules_cfg" not in st.session_state:
        st.session_state.rules_cfg = load_yaml(ROOT / "config" / "rules_preselect.yaml")
    if "review_cfg" not in st.session_state:
        st.session_state.review_cfg = load_yaml(ROOT / "config" / "gemini_cli_review.yaml")
    if "last_run_log" not in st.session_state:
        st.session_state.last_run_log = "[System] 工作台已启动，等待执行指令..."
    if "last_run_dir" not in st.session_state:
        st.session_state.last_run_dir = ""


def strategy_preset(cfg: dict[str, Any], preset: str) -> dict[str, Any]:
    updated = json.loads(json.dumps(cfg))
    updated.setdefault("b1", {})
    updated.setdefault("brick", {})
    if preset == "B1 策略":
        updated["b1"]["enabled"] = True
        updated["brick"]["enabled"] = False
    elif preset == "砖型图策略":
        updated["b1"]["enabled"] = False
        updated["brick"]["enabled"] = True
    elif preset == "B1 + 砖型图":
        updated["b1"]["enabled"] = True
        updated["brick"]["enabled"] = True
    return updated


def make_run_id() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def create_run_snapshot(run_mode: str) -> Path:
    run_id = make_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_yaml(run_dir / "rules_preselect.yaml", st.session_state.rules_cfg)
    write_yaml(run_dir / "gemini_cli_review.yaml", st.session_state.review_cfg)
    write_json(
        run_dir / "run_config.json",
        {
            "run_id": run_id,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "run_mode": run_mode,
            "rules_config": str(run_dir / "rules_preselect.yaml"),
            "gemini_cli_config": str(run_dir / "gemini_cli_review.yaml"),
        },
    )
    st.session_state.last_run_dir = str(run_dir)
    return run_dir


def command_plan(run_mode: str, run_dir: Path) -> list[tuple[str, list[str]]]:
    python = sys.executable
    rules_cfg = str(run_dir / "rules_preselect.yaml")
    review_cfg = str(run_dir / "gemini_cli_review.yaml")
    plans: dict[str, list[tuple[str, list[str]]]] = {
        "完整流程": [
            ("拉取 K 线数据", [python, "-m", "pipeline.fetch_kline"]),
            ("量化初选", [python, "-m", "pipeline.cli", "preselect", "--config", rules_cfg]),
            ("导出候选图表", [python, "dashboard/export_kline_charts.py"]),
            ("Gemini CLI 复评", [python, "agent/gemini_cli_review.py", "--config", review_cfg]),
        ],
        "跳过抓取": [
            ("量化初选", [python, "-m", "pipeline.cli", "preselect", "--config", rules_cfg]),
            ("导出候选图表", [python, "dashboard/export_kline_charts.py"]),
            ("Gemini CLI 复评", [python, "agent/gemini_cli_review.py", "--config", review_cfg]),
        ],
        "只跑初选": [
            ("量化初选", [python, "-m", "pipeline.cli", "preselect", "--config", rules_cfg]),
        ],
        "只导出图表": [
            ("导出候选图表", [python, "dashboard/export_kline_charts.py"]),
        ],
        "只跑复评": [
            ("Gemini CLI 复评", [python, "agent/gemini_cli_review.py", "--config", review_cfg]),
        ],
    }
    return plans[run_mode]


def run_commands(run_mode: str, log_placeholder) -> None:
    run_dir = create_run_snapshot(run_mode)
    log_lines = [
        f"[System] 运行快照: {run_dir}",
        f"[System] 运行模式: {run_mode}",
    ]
    log_path = run_dir / "run.log"

    for step_name, cmd in command_plan(run_mode, run_dir):
        log_lines.append("")
        log_lines.append(f"[Step] {step_name}")
        log_lines.append("[Command] " + " ".join(cmd))
        log_placeholder.markdown(f"<div class='log-box'>{escape_log(log_lines)}</div>", unsafe_allow_html=True)

        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=resolved_env(),
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log_lines.append(line.rstrip())
            if len(log_lines) % 4 == 0:
                log_placeholder.markdown(f"<div class='log-box'>{escape_log(log_lines)}</div>", unsafe_allow_html=True)

        return_code = proc.wait()
        if return_code != 0:
            log_lines.append(f"[ERROR] {step_name} 失败，退出码 {return_code}")
            write_json(run_dir / "run_state.json", {"status": "failed", "step": step_name, "return_code": return_code})
            break
        log_lines.append(f"[OK] {step_name} 完成")
    else:
        write_json(run_dir / "run_state.json", {"status": "success", "finished_at": dt.datetime.now().isoformat()})
        log_lines.append("")
        log_lines.append("[SUCCESS] 流程执行完毕")

    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    st.session_state.last_run_log = "\n".join(log_lines)
    log_placeholder.markdown(f"<div class='log-box'>{escape_log(log_lines)}</div>", unsafe_allow_html=True)


def resolved_env() -> dict[str, str]:
    env = os.environ.copy()
    if env.get("TS_TOKEN") and not env.get("TUSHARE_TOKEN"):
        env["TUSHARE_TOKEN"] = env["TS_TOKEN"]
    env.setdefault("NO_COLOR", "1")
    return env


def escape_log(lines: list[str] | str) -> str:
    text = "\n".join(lines) if isinstance(lines, list) else lines
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )


def render_metrics() -> None:
    candidates = load_candidates()
    suggestion = latest_suggestion()
    candidate_count = len(candidates.get("candidates", []))
    reviewed = suggestion.get("total_reviewed", 0)
    recommendations = len(suggestion.get("recommendations", [])) if suggestion else 0
    pending = len(suggestion.get("pending", [])) if suggestion else 0
    st.markdown(
        f"""
        <div class="metric-row">
          <div class="metric-card"><div class="metric-label">选股日期</div><div class="metric-value">{candidates.get("pick_date", "无")}</div></div>
          <div class="metric-card"><div class="metric-label">候选数量</div><div class="metric-value">{candidate_count}</div></div>
          <div class="metric-card"><div class="metric-label">已复评</div><div class="metric-value">{reviewed}</div></div>
          <div class="metric-card"><div class="metric-label">推荐 / 待处理</div><div class="metric-value">{recommendations} / {pending}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_run_center() -> None:
    st.title("运行中心")
    render_metrics()
    left, right = st.columns([0.42, 0.58], gap="large")

    with left:
        st.subheader("任务配置")
        run_mode = st.selectbox(
            "运行模式",
            ["完整流程", "跳过抓取", "只跑初选", "只导出图表", "只跑复评"],
            index=1,
        )
        run_dir_preview = RUNS_DIR / "本次运行会自动生成时间戳目录"
        st.markdown(f"<div class='panel-note'>运行配置会保存到 <code>{run_dir_preview}</code>，不会覆盖默认 YAML。</div>", unsafe_allow_html=True)
        if st.session_state.last_run_dir:
            st.caption(f"最近运行快照：{st.session_state.last_run_dir}")

        if st.button("开始运行", type="primary", width="stretch"):
            log_placeholder = st.empty()
            run_commands(run_mode, log_placeholder)
            st.rerun()

    with right:
        st.subheader("运行计划")
        preview_dir = RUNS_DIR / "preview"
        plan_rows = [{"步骤": name, "命令": " ".join(cmd)} for name, cmd in command_plan(run_mode, preview_dir)]
        st.dataframe(pd.DataFrame(plan_rows), width="stretch", hide_index=True)
        st.subheader("运行日志")
        st.markdown(f"<div class='log-box'>{escape_log(st.session_state.last_run_log)}</div>", unsafe_allow_html=True)


def render_strategy_config() -> None:
    st.title("策略配置")
    cfg = st.session_state.rules_cfg
    cfg.setdefault("global", {})
    cfg.setdefault("b1", {})
    cfg.setdefault("brick", {})

    preset = st.selectbox("策略预设", ["B1 策略", "砖型图策略", "B1 + 砖型图", "自定义"], index=0)
    if st.button("应用预设"):
        st.session_state.rules_cfg = strategy_preset(cfg, preset)
        st.rerun()

    st.divider()
    g = cfg["global"]
    c1, c2, c3 = st.columns(3)
    with c1:
        g["top_m"] = st.number_input("流动性池 top_m", min_value=1, value=int(g.get("top_m", 5000)), step=100)
    with c2:
        g["n_turnover_days"] = st.number_input("成交额窗口", min_value=1, value=int(g.get("n_turnover_days", 43)))
    with c3:
        g["min_bars_buffer"] = st.number_input("预热缓冲 bar", min_value=0, value=int(g.get("min_bars_buffer", 10)))

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("B1 策略参数")
        b1 = cfg["b1"]
        b1["enabled"] = st.toggle("启用 B1", value=bool(b1.get("enabled", True)))
        b1["j_threshold"] = st.number_input("J 阈值", value=float(b1.get("j_threshold", 15.0)), step=1.0)
        b1["j_q_threshold"] = st.number_input("J 分位阈值", value=float(b1.get("j_q_threshold", 0.10)), step=0.01, format="%.2f")
        cols = st.columns(4)
        for idx, key in enumerate(["zx_m1", "zx_m2", "zx_m3", "zx_m4"]):
            with cols[idx]:
                b1[key] = st.number_input(key, min_value=1, value=int(b1.get(key, [14, 28, 57, 114][idx])))

    with right:
        st.subheader("砖型图策略参数")
        brick = cfg["brick"]
        brick["enabled"] = st.toggle("启用砖型图", value=bool(brick.get("enabled", False)))
        brick["daily_return_threshold"] = st.number_input("今日涨幅上限", value=float(brick.get("daily_return_threshold", 0.2)), step=0.01, format="%.2f")
        brick["brick_growth_ratio"] = st.number_input("brick_growth 阈值", value=float(brick.get("brick_growth_ratio", 0.5)), step=0.1, format="%.2f")
        brick["min_prior_green_bars"] = st.number_input("最小连续绿柱", min_value=0, value=int(brick.get("min_prior_green_bars", 1)))
        brick["zxdq_ratio"] = st.number_input("zxdq_ratio", value=float(brick.get("zxdq_ratio", 1.47)), step=0.01, format="%.2f")
        brick["require_zxdq_gt_zxdkx"] = st.toggle("要求 zxdq > zxdkx", value=bool(brick.get("require_zxdq_gt_zxdkx", True)))
        brick["require_weekly_ma_bull"] = st.toggle("要求周线均线多头", value=bool(brick.get("require_weekly_ma_bull", True)))

    with st.expander("高级参数"):
        brick = cfg["brick"]
        col_a, col_b, col_c, col_d = st.columns(4)
        advanced = {
            "n": 8,
            "m1": 3,
            "m2": 12,
            "m3": 12,
            "t": 8,
            "shift1": 92,
            "shift2": 114,
            "sma_w1": 1,
            "sma_w2": 1,
            "sma_w3": 1,
            "zxdkx_m1": 14,
            "zxdkx_m2": 28,
            "zxdkx_m3": 57,
            "zxdkx_m4": 114,
            "wma_short": 5,
            "wma_mid": 10,
            "wma_long": 20,
        }
        columns = [col_a, col_b, col_c, col_d]
        for idx, (key, default) in enumerate(advanced.items()):
            with columns[idx % 4]:
                if isinstance(default, float):
                    brick[key] = st.number_input(key, value=float(brick.get(key, default)))
                else:
                    brick[key] = st.number_input(key, min_value=1, value=int(brick.get(key, default)))

    st.session_state.rules_cfg = cfg
    st.info("当前修改保存在工作台会话里。点击运行时会写入 data/runs 的快照配置，不覆盖 config/rules_preselect.yaml。")


def render_review_config() -> None:
    st.title("Gemini CLI 复评配置")
    cfg = st.session_state.review_cfg
    left, right = st.columns(2, gap="large")
    with left:
        cfg["batch_size"] = st.number_input("批处理大小 batch_size", min_value=1, max_value=5, value=int(cfg.get("batch_size", 5)))
        cfg["request_delay"] = st.number_input("请求间隔 request_delay", min_value=0.0, value=float(cfg.get("request_delay", 10)), step=1.0)
        cfg["max_requests_per_run"] = st.number_input("单次请求上限", min_value=1, value=int(cfg.get("max_requests_per_run", 50)))
        cfg["daily_request_budget"] = st.number_input("每日请求预算", min_value=1, value=int(cfg.get("daily_request_budget", 80)))
    with right:
        cfg["timeout_seconds"] = st.number_input("单次超时秒数", min_value=30, value=int(cfg.get("timeout_seconds", 180)), step=30)
        cfg["suggest_min_score"] = st.number_input("推荐分数门槛", min_value=0.0, max_value=5.0, value=float(cfg.get("suggest_min_score", 4.0)), step=0.1)
        cfg["skip_existing"] = st.toggle("断点续跑 skip_existing", value=bool(cfg.get("skip_existing", True)))
        cfg["fallback_to_single_on_batch_error"] = st.toggle(
            "批量失败后降级逐只复评",
            value=bool(cfg.get("fallback_to_single_on_batch_error", True)),
        )
        cfg["stop_on_rate_limit"] = st.toggle("命中限流后停止", value=bool(cfg.get("stop_on_rate_limit", True)))

    st.markdown(
        "<div class='panel-note'>batch_size 会降低每分钟请求数；每日预算仍按 Gemini CLI 请求次数记录。配置会随运行快照保存。</div>",
        unsafe_allow_html=True,
    )
    st.session_state.review_cfg = cfg


def result_rows() -> list[dict[str, Any]]:
    candidates_data = load_candidates()
    suggestion = latest_suggestion()
    candidates = {item.get("code"): item for item in candidates_data.get("candidates", [])}
    rows: list[dict[str, Any]] = []
    review_dir = ROOT / "data" / "review" / str(candidates_data.get("pick_date", ""))
    for code, candidate in candidates.items():
        review = load_json(review_dir / f"{code}.json")
        rows.append(
            {
                "代码": code,
                "策略": candidate.get("strategy", ""),
                "收盘价": candidate.get("close", ""),
                "brick_growth": candidate.get("brick_growth", ""),
                "结论": review.get("verdict", ""),
                "总分": review.get("total_score", ""),
                "信号": review.get("signal_type", ""),
                "评论": review.get("comment", ""),
            }
        )
    recommendation_codes = {item.get("code") for item in suggestion.get("recommendations", [])}
    for row in rows:
        row["推荐"] = "是" if row["代码"] in recommendation_codes else "否"
    return rows


def render_result_center() -> None:
    st.title("结果中心")
    render_metrics()
    rows = result_rows()
    if not rows:
        st.warning("还没有候选结果。请先运行初选。")
        return
    df = pd.DataFrame(rows)
    f1, f2, f3 = st.columns(3)
    with f1:
        strategy = st.selectbox("策略筛选", ["全部"] + sorted([x for x in df["策略"].dropna().unique() if x]))
    with f2:
        verdict = st.selectbox("结论筛选", ["全部"] + sorted([x for x in df["结论"].dropna().unique() if x]))
    with f3:
        rec_only = st.toggle("只看推荐", value=False)
    if strategy != "全部":
        df = df[df["策略"] == strategy]
    if verdict != "全部":
        df = df[df["结论"] == verdict]
    if rec_only:
        df = df[df["推荐"] == "是"]
    st.dataframe(df, width="stretch", hide_index=True)


def _load_raw(code: str) -> pd.DataFrame:
    csv = ROOT / "data" / "raw" / f"{code}.csv"
    if not csv.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv)
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def render_stock_view() -> None:
    st.title("单票复盘")
    candidates = load_candidates().get("candidates", [])
    codes = [item["code"] for item in candidates if item.get("code")]
    if not codes:
        st.warning("还没有候选股票。")
        return
    selected = st.selectbox("选择股票", codes)
    candidate = next((item for item in candidates if item.get("code") == selected), {})
    pick_date = latest_pick_date()
    review = load_json(ROOT / "data" / "review" / pick_date / f"{selected}.json")
    df = _load_raw(selected)
    if df.empty:
        st.error(f"未找到 data/raw/{selected}.csv")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("策略", candidate.get("strategy", ""))
    c2.metric("收盘价", candidate.get("close", ""))
    c3.metric("Gemini 结论", review.get("verdict", "未复评"))
    c4.metric("总分", review.get("total_score", ""))

    st.plotly_chart(make_daily_chart(df, selected, bars=120, height=620), width="stretch", config={"scrollZoom": True})
    st.plotly_chart(make_weekly_chart(df, selected, height=460), width="stretch", config={"scrollZoom": True})

    if review:
        st.subheader("复评摘要")
        st.write(review.get("comment", ""))
        with st.expander("原始复评 JSON"):
            st.json(review)


def main() -> None:
    st.set_page_config(page_title="AgentTrader 工作台", layout="wide", initial_sidebar_state="expanded")
    ensure_session_state()
    css = _read_text(WORKBENCH_DIR / "assets" / "style.css")
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

    with st.sidebar:
        st.title("AgentTrader")
        st.caption("本地选股工作台")
        page = st.radio(
            "导航",
            ["运行中心", "策略配置", "复评配置", "结果中心", "单票复盘"],
            label_visibility="collapsed",
        )

    render_status_bar()
    if page == "运行中心":
        render_run_center()
    elif page == "策略配置":
        render_strategy_config()
    elif page == "复评配置":
        render_review_config()
    elif page == "结果中心":
        render_result_center()
    else:
        render_stock_view()


if __name__ == "__main__":
    main()
