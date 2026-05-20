"""
Local AgentTrader workbench.

This is an independent Streamlit entrypoint. It intentionally does not modify
or import dashboard/app.py.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import signal
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKBENCH_DIR = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "data" / "runs"
HISTORY_DIR = ROOT / "data" / "history"
RUN_MODES = ["完整流程", "跳过抓取", "初选+导出图表", "只抓取数据", "只跑初选", "只导出图表", "只跑复评"]

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

from dashboard.components.charts import make_daily_chart, make_weekly_chart  # noqa: E402
from paper_trading.core import (  # noqa: E402
    complete_history_result,
    execute_plan,
    generate_plan,
    plan_files,
    plan_path,
    portfolio_value,
    save_snapshot,
    trading_config,
    trading_dir,
    update_plan_status,
)
import paper_trading.core as paper_trading_core  # noqa: E402


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


def default_run_cfg() -> dict[str, Any]:
    return {
        "pick_date": dt.date.today().isoformat(),
        "end_date": "",
        "preselect_log_dir": "./data/logs",
        "reviewer": "gemini-cli",
    }


def apply_workbench_defaults() -> None:
    st.session_state.rules_cfg.setdefault("global", {})
    g = st.session_state.rules_cfg["global"]
    g.setdefault("prepare_executor", "thread")
    g.setdefault("n_jobs", None)


def load_candidates() -> dict[str, Any]:
    return load_json(ROOT / "data" / "candidates" / "candidates_latest.json")


def latest_pick_date() -> str:
    return str(load_candidates().get("pick_date", "") or "")


def latest_suggestion() -> dict[str, Any]:
    pick_date = latest_pick_date()
    if not pick_date:
        return {}
    return load_json(ROOT / "data" / "review" / pick_date / "suggestion.json")


def history_index() -> dict[str, Any]:
    return load_json(HISTORY_DIR / "index.json")


def history_dates() -> list[str]:
    dates = [str(item.get("date")) for item in history_index().get("dates", []) if item.get("date")]
    if dates:
        return dates
    if not HISTORY_DIR.exists():
        return []
    return sorted([p.name for p in HISTORY_DIR.iterdir() if p.is_dir()], reverse=True)


def load_history_results(pick_date: str, strategy: str = "all") -> dict[str, Any]:
    safe_strategy = strategy if strategy else "all"
    return load_json(HISTORY_DIR / pick_date / f"{safe_strategy}.json")


def load_history_summary(pick_date: str) -> dict[str, Any]:
    return load_json(HISTORY_DIR / pick_date / "summary.json")


def csv_count(path: Path) -> int:
    return len(list(path.glob("*.csv"))) if path.exists() else 0


def environment_status() -> list[tuple[str, str, str]]:
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN")
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    raw_dir = ROOT / "data" / "raw"
    candidates = load_candidates()
    suggestion = latest_suggestion()
    return [
        ("Tushare", "ok" if token else "err", "已配置" if token else "未配置"),
        ("Gemini CLI", "ok" if shutil.which("gemini") else "warn", "已安装" if shutil.which("gemini") else "未找到"),
        ("Gemini API", "ok" if gemini_api_key else "warn", "已配置" if gemini_api_key else "未配置"),
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
    if "fetch_cfg" not in st.session_state:
        st.session_state.fetch_cfg = load_yaml(ROOT / "config" / "fetch_kline.yaml")
    if "rules_cfg" not in st.session_state:
        st.session_state.rules_cfg = load_yaml(ROOT / "config" / "rules_preselect.yaml")
    if "review_cfg" not in st.session_state:
        st.session_state.review_cfg = load_yaml(ROOT / "config" / "gemini_cli_review.yaml")
    if "api_review_cfg" not in st.session_state:
        st.session_state.api_review_cfg = load_yaml(ROOT / "config" / "gemini_review.yaml")
    if "trading_cfg" not in st.session_state:
        st.session_state.trading_cfg = trading_config(ROOT / "config" / "paper_trading.yaml")
    if "run_cfg" not in st.session_state:
        st.session_state.run_cfg = default_run_cfg()
    apply_workbench_defaults()
    if "last_run_log" not in st.session_state:
        st.session_state.last_run_log = "[System] 工作台已启动，等待执行指令..."
    if "last_run_dir" not in st.session_state:
        st.session_state.last_run_dir = ""


def list_run_dirs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return sorted([p for p in RUNS_DIR.iterdir() if p.is_dir()], reverse=True)


def latest_run_dir() -> Path | None:
    if st.session_state.get("last_run_dir"):
        p = Path(str(st.session_state.last_run_dir))
        if p.exists():
            return p
    runs = list_run_dirs()
    return runs[0] if runs else None


def run_state(run_dir: Path | None = None) -> dict[str, Any]:
    if run_dir is None:
        run_dir = latest_run_dir()
    if run_dir is None:
        return {}
    return load_json(run_dir / "run_state.json")


def is_pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_run_active(run_dir: Path | None = None) -> bool:
    state = run_state(run_dir)
    if state.get("status") != "running":
        return False
    return is_pid_running(int(state.get("runner_pid") or 0))


def active_run_dir() -> Path | None:
    for run_dir in list_run_dirs():
        if is_run_active(run_dir):
            return run_dir
    return None


def active_run_label(run_dir: Path | None) -> str:
    if run_dir is None:
        return ""
    state = run_state(run_dir)
    return str(state.get("owner_label") or state.get("owner") or "运行任务")


def display_run_status(run_dir: Path | None) -> str:
    state = run_state(run_dir)
    status = str(state.get("status", "idle"))
    if status == "running" and not is_run_active(run_dir):
        return "stale"
    return status


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


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def parse_date_or_today(value: Any) -> dt.date:
    text = clean_text(value)
    if not text:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return dt.date.today()


def date_input_iso(
    label: str,
    value: Any,
    *,
    key: str | None = None,
    help: str | None = None,
) -> str:
    selected = st.date_input(label, value=parse_date_or_today(value), key=key, help=help)
    if isinstance(selected, tuple):
        selected = selected[0] if selected else dt.date.today()
    return selected.isoformat()


def create_run_snapshot(run_mode: str, *, owner: str = "run_center", owner_label: str = "运行中心") -> Path:
    run_id = make_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_yaml(run_dir / "fetch_kline.yaml", st.session_state.fetch_cfg)
    write_yaml(run_dir / "rules_preselect.yaml", st.session_state.rules_cfg)
    write_yaml(run_dir / "gemini_cli_review.yaml", st.session_state.review_cfg)
    write_yaml(run_dir / "gemini_review.yaml", st.session_state.api_review_cfg)
    write_json(run_dir / "run_options.json", st.session_state.run_cfg)
    reviewer = clean_text(st.session_state.run_cfg.get("reviewer")) or "gemini-cli"
    write_json(
        run_dir / "run_config.json",
        {
            "run_id": run_id,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "run_mode": run_mode,
            "owner": owner,
            "owner_label": owner_label,
            "reviewer": reviewer,
            "fetch_config": str(run_dir / "fetch_kline.yaml"),
            "rules_config": str(run_dir / "rules_preselect.yaml"),
            "gemini_cli_config": str(run_dir / "gemini_cli_review.yaml"),
            "gemini_api_config": str(run_dir / "gemini_review.yaml"),
            "run_options": str(run_dir / "run_options.json"),
            "commands": [
                {"name": name, "cmd": cmd}
                for name, cmd in command_plan(run_mode, run_dir)
            ],
        },
    )
    st.session_state.last_run_dir = str(run_dir)
    return run_dir


def create_paper_run_snapshot() -> Path:
    run_id = make_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_yaml(run_dir / "fetch_kline.yaml", st.session_state.fetch_cfg)
    write_yaml(run_dir / "rules_preselect.yaml", st.session_state.rules_cfg)
    write_yaml(run_dir / "gemini_cli_review.yaml", st.session_state.review_cfg)
    write_yaml(run_dir / "gemini_review.yaml", st.session_state.api_review_cfg)
    write_yaml(run_dir / "paper_trading.yaml", st.session_state.trading_cfg)
    write_json(run_dir / "run_options.json", st.session_state.run_cfg)
    command = [sys.executable, "-m", "paper_trading.daily_flow", "--run-dir", str(run_dir)]
    reviewer = clean_text(st.session_state.run_cfg.get("reviewer")) or "gemini-cli"
    write_json(
        run_dir / "run_config.json",
        {
            "run_id": run_id,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "run_mode": "模拟交易每日流程",
            "owner": "paper_trading",
            "owner_label": "模拟交易",
            "reviewer": reviewer,
            "fetch_config": str(run_dir / "fetch_kline.yaml"),
            "rules_config": str(run_dir / "rules_preselect.yaml"),
            "gemini_cli_config": str(run_dir / "gemini_cli_review.yaml"),
            "gemini_api_config": str(run_dir / "gemini_review.yaml"),
            "paper_trading_config": str(run_dir / "paper_trading.yaml"),
            "run_options": str(run_dir / "run_options.json"),
            "commands": [{"name": "模拟交易每日流程", "cmd": command}],
        },
    )
    st.session_state.last_run_dir = str(run_dir)
    return run_dir


def command_plan(run_mode: str, run_dir: Path) -> list[tuple[str, list[str]]]:
    python = sys.executable
    fetch_cfg = str(run_dir / "fetch_kline.yaml")
    rules_cfg = str(run_dir / "rules_preselect.yaml")
    run_id = run_dir.name
    run_cfg = st.session_state.get("run_cfg", default_run_cfg())
    reviewer = clean_text(run_cfg.get("reviewer")) or "gemini-cli"
    if reviewer == "gemini-api":
        review_step = ("Gemini API 复评", [python, "agent/gemini_review.py", "--config", str(run_dir / "gemini_review.yaml")])
    else:
        review_step = (
            "Gemini CLI 复评",
            [python, "agent/gemini_cli_review.py", "--config", str(run_dir / "gemini_cli_review.yaml")],
        )
    preselect_cmd = [python, "-m", "pipeline.cli", "preselect", "--config", rules_cfg]
    preselect_cmd += ["--merge-same-date"]
    if clean_text(run_cfg.get("pick_date")):
        preselect_cmd += ["--date", clean_text(run_cfg.get("pick_date"))]
    if clean_text(run_cfg.get("end_date")):
        preselect_cmd += ["--end-date", clean_text(run_cfg.get("end_date"))]
    if clean_text(run_cfg.get("preselect_log_dir")):
        preselect_cmd += ["--log-dir", clean_text(run_cfg.get("preselect_log_dir"))]

    plans: dict[str, list[tuple[str, list[str]]]] = {
        "完整流程": [
            ("拉取 K 线数据", [python, "-m", "pipeline.fetch_kline", "--config", fetch_cfg]),
            ("量化初选", preselect_cmd),
            ("导出候选图表", [python, "dashboard/export_kline_charts.py"]),
            review_step,
            ("归档当日结果", [python, "-m", "pipeline.archive_results", "--run-id", run_id]),
        ],
        "跳过抓取": [
            ("量化初选", preselect_cmd),
            ("导出候选图表", [python, "dashboard/export_kline_charts.py"]),
            review_step,
            ("归档当日结果", [python, "-m", "pipeline.archive_results", "--run-id", run_id]),
        ],
        "初选+导出图表": [
            ("量化初选", preselect_cmd),
            ("导出候选图表", [python, "dashboard/export_kline_charts.py"]),
        ],
        "只抓取数据": [
            ("拉取 K 线数据", [python, "-m", "pipeline.fetch_kline", "--config", fetch_cfg]),
        ],
        "只跑初选": [
            ("量化初选", preselect_cmd),
        ],
        "只导出图表": [
            ("导出候选图表", [python, "dashboard/export_kline_charts.py"]),
        ],
        "只跑复评": [
            review_step,
            ("归档当日结果", [python, "-m", "pipeline.archive_results", "--run-id", run_id]),
        ],
    }
    return plans[run_mode]


def start_background_run(run_mode: str) -> Path:
    running = active_run_dir()
    if running:
        raise RuntimeError(f"已有{active_run_label(running)}任务正在运行：{running}")

    run_dir = create_run_snapshot(run_mode)
    write_json(
        run_dir / "run_state.json",
        {
            "status": "starting",
            "owner": "run_center",
            "owner_label": "运行中心",
            "run_dir": str(run_dir),
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "workbench.runner", str(run_dir)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=resolved_env(),
        start_new_session=True,
    )
    write_json(
        run_dir / "run_state.json",
        {
            "status": "running",
            "owner": "run_center",
            "owner_label": "运行中心",
            "runner_pid": proc.pid,
            "run_dir": str(run_dir),
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
    )
    return run_dir


def start_paper_trading_run() -> Path:
    running = active_run_dir()
    if running:
        raise RuntimeError(f"已有{active_run_label(running)}任务正在运行：{running}")

    run_dir = create_paper_run_snapshot()
    write_json(
        run_dir / "run_state.json",
        {
            "status": "starting",
            "owner": "paper_trading",
            "owner_label": "模拟交易",
            "run_dir": str(run_dir),
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "workbench.runner", str(run_dir)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=resolved_env(),
        start_new_session=True,
    )
    write_json(
        run_dir / "run_state.json",
        {
            "status": "running",
            "owner": "paper_trading",
            "owner_label": "模拟交易",
            "runner_pid": proc.pid,
            "run_dir": str(run_dir),
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
    )
    return run_dir


def stop_background_run(run_dir: Path) -> None:
    state = run_state(run_dir)
    pid = int(state.get("runner_pid") or 0)
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            os.kill(pid, signal.SIGTERM)
    write_json(
        run_dir / "run_state.json",
        {
            **state,
            "status": "stopped",
            "stopped_at": dt.datetime.now().isoformat(timespec="seconds"),
            "run_dir": str(run_dir),
        },
    )
    with open(run_dir / "run.log", "a", encoding="utf-8") as f:
        f.write("\n[System] 收到停止指令，任务已终止。\n")


def resolved_env() -> dict[str, str]:
    env = os.environ.copy()
    if env.get("TS_TOKEN") and not env.get("TUSHARE_TOKEN"):
        env["TUSHARE_TOKEN"] = env["TS_TOKEN"]
    env.setdefault("NO_COLOR", "1")
    return env


def normalize_console_text(text: str, max_lines: int = 600) -> str:
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\r":
            current = ""
        elif char == "\n":
            lines.append(current.rstrip())
            current = ""
        else:
            current += char
    if current:
        lines.append(current.rstrip())
    return "\n".join(lines[-max_lines:])


def read_run_log(run_dir: Path | None) -> str:
    if run_dir is None:
        return "[System] 工作台已启动，等待执行指令..."
    log_path = run_dir / "run.log"
    if not log_path.exists():
        return "[System] 任务已创建，等待后台进程写入日志..."
    with open(log_path, "r", encoding="utf-8", errors="replace", newline="") as f:
        return normalize_console_text(f.read())


def escape_log(lines: list[str] | str) -> str:
    text = "\n".join(lines) if isinstance(lines, list) else lines
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )


def render_log_console(log_text: str, *, storage_key: str) -> None:
    escaped_key = json.dumps(storage_key)
    st.iframe(
        f"""
        <style>
        :root {{
          color-scheme: light;
        }}
        body {{
          background: transparent;
          margin: 0;
        }}
        .log-box {{
          background: #1f242c;
          border: 1px solid #111820;
          border-radius: 6px;
          box-sizing: border-box;
          color: #d8dee9;
          font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
          font-size: 12px;
          height: 420px;
          line-height: 1.45;
          overflow: auto;
          padding: 14px;
          white-space: pre-wrap;
          width: 100%;
        }}
        </style>
        <div id="workbench-log-console" class="log-box">{escape_log(log_text)}</div>
        <script>
        (() => {{
          const box = document.getElementById("workbench-log-console");
          const storageKey = "workbench-log-scroll:" + {escaped_key};
          const thresholdPx = 24;
          let restoring = true;

          const readState = () => {{
            try {{
              return JSON.parse(window.sessionStorage.getItem(storageKey) || "{{}}");
            }} catch {{
              return {{}};
            }}
          }};

          const writeState = (state) => {{
            try {{
              window.sessionStorage.setItem(storageKey, JSON.stringify(state));
            }} catch {{}}
          }};

          const maxScrollTop = () =>
            Math.max(0, box.scrollHeight - box.clientHeight);

          const isAtBottom = () =>
            maxScrollTop() - box.scrollTop <= thresholdPx;

          const persistCurrentState = () => {{
            writeState({{
              sticky: isAtBottom(),
              scrollTop: box.scrollTop,
            }});
          }};

          box.addEventListener("scroll", () => {{
            if (!restoring) {{
              persistCurrentState();
            }}
          }}, {{ passive: true }});

          const restoreScrollPosition = () => {{
            const state = readState();
            if (state.sticky === false && Number.isFinite(state.scrollTop)) {{
              box.scrollTop = Math.min(Math.max(0, state.scrollTop), maxScrollTop());
            }} else {{
              box.scrollTop = maxScrollTop();
            }}

            window.setTimeout(() => {{
              restoring = false;
              persistCurrentState();
            }}, 0);
          }};

          window.requestAnimationFrame(() => {{
            window.requestAnimationFrame(restoreScrollPosition);
          }});
        }})();
        </script>
        """,
        width="stretch",
        height=430,
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


def render_result_metrics(pick_date: str, rows: list[dict[str, Any]]) -> None:
    candidate_count = len(rows)
    reviewed = sum(1 for row in rows if clean_text(row.get("结论")))
    recommendations = sum(1 for row in rows if row.get("推荐") == "是")
    pending = max(candidate_count - reviewed, 0)
    st.markdown(
        f"""
        <div class="metric-row">
          <div class="metric-card"><div class="metric-label">当前选股日期</div><div class="metric-value">{pick_date}</div></div>
          <div class="metric-card"><div class="metric-label">候选数量</div><div class="metric-value">{candidate_count}</div></div>
          <div class="metric-card"><div class="metric-label">已复评</div><div class="metric-value">{reviewed}</div></div>
          <div class="metric-card"><div class="metric-label">推荐 / 待处理</div><div class="metric-value">{recommendations} / {pending}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.fragment(run_every=1.0)
def render_run_log_panel(run_dir_str: str) -> None:
    run_dir = Path(run_dir_str) if run_dir_str else None
    state = run_state(run_dir)
    active = is_run_active(run_dir)
    status_label = "running" if active else display_run_status(run_dir)
    st.subheader(f"运行日志 · {status_label}")
    render_log_console(read_run_log(run_dir), storage_key=str(run_dir or "default"))
    if active:
        step = state.get("current_step") or "启动中"
        idx = state.get("step_index")
        total = state.get("step_total")
        suffix = f"（{idx}/{total}）" if idx and total else ""
        st.caption(f"实时刷新中：{step}{suffix}。切换菜单或刷新页面不会中断后台任务。")


def render_run_center() -> None:
    st.title("运行中心")
    render_metrics()
    current_run = latest_run_dir()
    state = run_state(current_run)
    running = active_run_dir()
    active = running is not None
    left, right = st.columns([0.42, 0.58], gap="large")

    with left:
        st.subheader("任务配置")
        run_mode = st.selectbox("运行模式", RUN_MODES, index=1)
        run_dir_preview = RUNS_DIR / "本次运行会自动生成时间戳目录"
        st.markdown(f"<div class='panel-note'>运行配置会保存到 <code>{run_dir_preview}</code>，不会覆盖默认 YAML。</div>", unsafe_allow_html=True)
        if run_mode == "只抓取数据":
            st.caption("只执行「拉取 K 线数据」这一环节；抓取参数在「数据配置」的行情下载区域设置。")
        if current_run:
            st.caption(f"最近运行快照：{current_run}")

        run_cfg = st.session_state.run_cfg
        with st.expander("本次运行参数", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                run_cfg["pick_date"] = date_input_iso(
                    "选股基准日期",
                    value=clean_text(run_cfg.get("pick_date")),
                    help="默认今天；如果当天原始数据不存在，初选会使用不晚于该日期的最新可用交易日。",
                )
            with c2:
                run_cfg["end_date"] = st.text_input(
                    "数据截断日期",
                    value=clean_text(run_cfg.get("end_date")),
                    placeholder="留空=不截断；回测用 YYYY-MM-DD",
                )
            run_cfg["preselect_log_dir"] = st.text_input(
                "初选日志目录",
                value=clean_text(run_cfg.get("preselect_log_dir") or "./data/logs"),
            )
        st.session_state.run_cfg = run_cfg

        if active:
            active_state = run_state(running)
            step_text = active_state.get("current_step") or "启动中"
            st.info(f"{active_run_label(running)}任务运行中：{step_text}")
        if st.button("开始运行", type="primary", width="stretch", disabled=active):
            try:
                run_dir = start_background_run(run_mode)
                st.session_state.last_run_dir = str(run_dir)
                st.success(f"任务已在后台启动：{run_dir}")
            except RuntimeError as exc:
                st.warning(str(exc))
            st.rerun()
        if st.button("停止当前任务", width="stretch", disabled=not active):
            if running:
                stop_background_run(running)
            st.rerun()

    with right:
        st.subheader("运行计划")
        preview_dir = RUNS_DIR / "preview"
        plan_rows = [{"步骤": name, "命令": " ".join(cmd)} for name, cmd in command_plan(run_mode, preview_dir)]
        st.dataframe(pd.DataFrame(plan_rows), width="stretch", hide_index=True)
        render_run_log_panel(str(current_run) if current_run else "")


def render_data_config() -> None:
    st.title("数据与运行配置")
    fetch_cfg = st.session_state.fetch_cfg
    rules_cfg = st.session_state.rules_cfg
    run_cfg = st.session_state.run_cfg
    fetch_cfg.setdefault("exclude_boards", [])
    rules_cfg.setdefault("global", {})

    st.subheader("行情下载")
    c1, c2, c3 = st.columns(3)
    with c1:
        fetch_cfg["start"] = st.text_input("下载开始日期", value=str(fetch_cfg.get("start", "20190101")), placeholder="YYYYMMDD 或 today")
    with c2:
        fetch_cfg["end"] = st.text_input("下载结束日期", value=str(fetch_cfg.get("end", "today")), placeholder="YYYYMMDD 或 today")
    with c3:
        fetch_cfg["workers"] = st.number_input("并发下载线程 workers", min_value=1, max_value=16, value=int(fetch_cfg.get("workers", 4)))

    c4, c5 = st.columns(2)
    with c4:
        fetch_cfg["stocklist"] = st.text_input("股票清单 CSV", value=str(fetch_cfg.get("stocklist", "./pipeline/stocklist.csv")))
        fetch_cfg["out"] = st.text_input("K 线输出目录", value=str(fetch_cfg.get("out", "./data/raw")))
    with c5:
        fetch_cfg["exclude_boards"] = st.multiselect(
            "排除板块",
            ["gem", "star", "bj", "st"],
            default=[x for x in fetch_cfg.get("exclude_boards", []) if x in {"gem", "star", "bj", "st"}],
            help="gem=创业板，star=科创板，bj=北交所，st=ST/*ST 股票",
        )
        fetch_cfg["log"] = st.text_input("抓取日志文件", value=str(fetch_cfg.get("log", "")), placeholder="留空=按日期写入 data/logs")

    st.divider()
    st.subheader("初选输入输出")
    g = rules_cfg["global"]
    d1, d2 = st.columns(2)
    with d1:
        g["data_dir"] = st.text_input("初选 CSV 数据目录", value=str(g.get("data_dir", "./data/raw")))
        run_cfg["pick_date"] = date_input_iso(
            "选股基准日期",
            value=clean_text(run_cfg.get("pick_date")),
            key="data_pick_date",
            help="默认今天；如果当天原始数据不存在，初选会使用不晚于该日期的最新可用交易日。",
        )
    with d2:
        g["output_dir"] = st.text_input("候选结果输出目录", value=str(g.get("output_dir", "./data/candidates")))
        run_cfg["end_date"] = st.text_input(
            "数据截断日期",
            value=clean_text(run_cfg.get("end_date")),
            placeholder="留空=不截断；回测用 YYYY-MM-DD",
            key="data_end_date",
        )
    run_cfg["preselect_log_dir"] = st.text_input(
        "初选流水日志目录",
        value=clean_text(run_cfg.get("preselect_log_dir") or "./data/logs"),
        key="data_preselect_log_dir",
    )

    st.session_state.fetch_cfg = fetch_cfg
    st.session_state.rules_cfg = rules_cfg
    st.session_state.run_cfg = run_cfg
    st.info("这些配置只保存在工作台会话中；点击运行时会写入本次 data/runs 快照。")


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
    c4, c5 = st.columns(2)
    with c4:
        executor_options = ["thread", "process"]
        current_executor = str(g.get("prepare_executor", "thread"))
        g["prepare_executor"] = st.selectbox(
            "预处理执行器",
            executor_options,
            index=executor_options.index(current_executor) if current_executor in executor_options else 0,
            help="thread 更适合本地工作台；process 在部分 macOS/沙箱环境可能无法初始化 semaphore。",
        )
    with c5:
        use_default_jobs = g.get("n_jobs") is None
        if st.toggle("预处理并发数使用默认值", value=use_default_jobs):
            g["n_jobs"] = None
        else:
            g["n_jobs"] = st.number_input("预处理并发数 n_jobs", min_value=1, max_value=32, value=int(g.get("n_jobs") or 4))
    p1, p2 = st.columns(2)
    with p1:
        g["data_dir"] = st.text_input("初选数据目录 data_dir", value=str(g.get("data_dir", "./data/raw")))
    with p2:
        g["output_dir"] = st.text_input("候选输出目录 output_dir", value=str(g.get("output_dir", "./data/candidates")))

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
        use_zxdq_ratio = st.toggle("启用 zxdq_ratio 过滤", value=brick.get("zxdq_ratio") is not None)
        brick["zxdq_ratio"] = (
            st.number_input("zxdq_ratio", value=float(brick.get("zxdq_ratio") or 1.47), step=0.01, format="%.2f")
            if use_zxdq_ratio
            else None
        )
        brick["zxdq_span"] = st.number_input("zxdq_span", min_value=1, value=int(brick.get("zxdq_span", 10)))
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
    st.title("复评配置")
    run_cfg = st.session_state.run_cfg
    reviewer_options = {
        "gemini-cli": "Gemini CLI（本机登录）",
        "gemini-api": "Gemini API Key",
    }
    current_reviewer = clean_text(run_cfg.get("reviewer")) or "gemini-cli"
    if current_reviewer not in reviewer_options:
        current_reviewer = "gemini-cli"
    selected_reviewer_label = st.radio(
        "复评方式",
        list(reviewer_options.values()),
        index=list(reviewer_options).index(current_reviewer),
        horizontal=True,
    )
    reviewer = next(key for key, label in reviewer_options.items() if label == selected_reviewer_label)
    run_cfg["reviewer"] = reviewer
    st.session_state.run_cfg = run_cfg

    if reviewer == "gemini-api":
        cfg = st.session_state.api_review_cfg
        left, right = st.columns(2, gap="large")
        with left:
            cfg["model"] = st.text_input("模型 model", value=str(cfg.get("model", "gemini-3.1-pro-preview")))
            cfg["request_delay"] = st.number_input("请求间隔 request_delay", min_value=0.0, value=float(cfg.get("request_delay", 5)), step=1.0)
            cfg["suggest_min_score"] = st.number_input(
                "推荐分数门槛",
                min_value=0.0,
                max_value=5.0,
                value=float(cfg.get("suggest_min_score", 4.0)),
                step=0.1,
            )
            cfg["skip_existing"] = st.toggle("断点续跑 skip_existing", value=bool(cfg.get("skip_existing", False)))
        with right:
            if os.environ.get("GEMINI_API_KEY"):
                st.success("GEMINI_API_KEY 已配置")
            else:
                st.warning("GEMINI_API_KEY 未配置，运行 API Key 复评会失败。")

        with st.expander("路径配置"):
            p1, p2 = st.columns(2)
            with p1:
                cfg["candidates"] = st.text_input("候选列表 JSON", value=str(cfg.get("candidates", "data/candidates/candidates_latest.json")))
                cfg["kline_dir"] = st.text_input("候选图表目录", value=str(cfg.get("kline_dir", "data/kline")))
                cfg["prompt_path"] = st.text_input("提示词文件", value=str(cfg.get("prompt_path", "agent/prompt.md")))
            with p2:
                cfg["output_dir"] = st.text_input("复评输出目录", value=str(cfg.get("output_dir", "data/review")))

        st.markdown(
            "<div class='panel-note'>API Key 模式调用 <code>agent/gemini_review.py</code>，密钥只从环境变量 <code>GEMINI_API_KEY</code> 读取，不写入运行快照。</div>",
            unsafe_allow_html=True,
        )
        st.session_state.api_review_cfg = cfg
        return

    cfg = st.session_state.review_cfg
    left, right = st.columns(2, gap="large")
    with left:
        cfg["gemini_bin"] = st.text_input("Gemini CLI 路径", value=str(cfg.get("gemini_bin", "gemini")))
        cfg["model"] = st.text_input("模型 model", value=str(cfg.get("model", "")), placeholder="留空=Gemini CLI 默认模型")
        cfg["batch_size"] = st.number_input("批处理大小 batch_size", min_value=1, max_value=2700, value=int(cfg.get("batch_size", 5)))
        cfg["request_delay"] = st.number_input("请求间隔 request_delay", min_value=0.0, value=float(cfg.get("request_delay", 10)), step=1.0)
        cfg["max_requests_per_run"] = st.number_input("单次请求上限", min_value=1, value=int(cfg.get("max_requests_per_run", 50)))
        cfg["daily_request_budget"] = st.number_input("每日请求预算", min_value=1, value=int(cfg.get("daily_request_budget", 2000)))
    with right:
        output_format_options = ["stream-json", "json", "text"]
        current_output_format = str(cfg.get("output_format", "stream-json"))
        cfg["output_format"] = st.selectbox(
            "CLI 输出格式",
            output_format_options,
            index=output_format_options.index(current_output_format) if current_output_format in output_format_options else 0,
        )
        cfg["timeout_seconds"] = st.number_input("单次超时秒数", min_value=30, value=int(cfg.get("timeout_seconds", 900)), step=30)
        cfg["idle_timeout_seconds"] = st.number_input(
            "空闲超时秒数",
            min_value=0,
            value=int(cfg.get("idle_timeout_seconds", 0) or 0),
            step=30,
            help="超过该时间没有 stdout/stderr 输出即中止；0 表示关闭空闲超时。",
        )
        cfg["suggest_min_score"] = st.number_input("推荐分数门槛", min_value=0.0, max_value=5.0, value=float(cfg.get("suggest_min_score", 4.0)), step=0.1)
        retry_backoff = cfg.get("retry_backoff_seconds", [30, 90, 180, 480, 900])
        if isinstance(retry_backoff, list):
            retry_backoff_text = ",".join(str(int(x) if float(x).is_integer() else x) for x in retry_backoff)
        else:
            retry_backoff_text = str(retry_backoff)
        retry_text = st.text_input("错误退避序列", value=retry_backoff_text, help="逗号分隔秒数，用于 429、容量不足、Premature close、超时等错误。")
        cfg["retry_backoff_seconds"] = [float(x.strip()) for x in retry_text.split(",") if x.strip()]
        cfg["retry_jitter_ratio"] = st.number_input("退避 jitter 比例", min_value=0.0, max_value=1.0, value=float(cfg.get("retry_jitter_ratio", 0.2)), step=0.05)
        cfg["skip_existing"] = st.toggle("断点续跑 skip_existing", value=bool(cfg.get("skip_existing", True)))
        cfg["save_raw_cli_io"] = st.toggle("保存 CLI 原始调用日志", value=bool(cfg.get("save_raw_cli_io", True)))
        cfg["fallback_to_single_on_batch_error"] = st.toggle(
            "批量失败后降级逐只复评",
            value=bool(cfg.get("fallback_to_single_on_batch_error", True)),
        )
        cfg["stop_on_rate_limit"] = st.toggle("重试耗尽后命中限流则停止", value=bool(cfg.get("stop_on_rate_limit", False)))

    with st.expander("路径配置"):
        p1, p2 = st.columns(2)
        with p1:
            cfg["candidates"] = st.text_input("候选列表 JSON", value=str(cfg.get("candidates", "data/candidates/candidates_latest.json")))
            cfg["kline_dir"] = st.text_input("候选图表目录", value=str(cfg.get("kline_dir", "data/kline")))
            cfg["prompt_path"] = st.text_input("提示词文件", value=str(cfg.get("prompt_path", "agent/prompt.md")))
        with p2:
            cfg["output_dir"] = st.text_input("复评输出目录", value=str(cfg.get("output_dir", "data/review")))
            cfg["usage_file"] = st.text_input("每日使用计数文件", value=str(cfg.get("usage_file", "data/review/.gemini_cli_usage.json")))
            cfg["raw_log_dir"] = st.text_input("CLI 原始日志目录", value=str(cfg.get("raw_log_dir", "")), help="留空时使用 data/review/{pick_date}/gemini_cli_runs。")

    st.markdown(
        "<div class='panel-note'>batch_size 默认 5；Gemini CLI 会在图片目录运行并引用本地 @图片文件；stream-json 原始输出会写入单独 raw log 目录，主运行日志只显示摘要。</div>",
        unsafe_allow_html=True,
    )
    st.session_state.review_cfg = cfg


def result_rows() -> list[dict[str, Any]]:
    candidates_data = load_candidates()
    suggestion = latest_suggestion()
    rows: list[dict[str, Any]] = []
    review_dir = ROOT / "data" / "review" / str(candidates_data.get("pick_date", ""))
    for candidate in candidates_data.get("candidates", []):
        code = str(candidate.get("code") or "")
        if not code:
            continue
        review = load_json(review_dir / f"{code}.json")
        close = candidate.get("close")
        brick_growth = candidate.get("brick_growth")
        total_score = review.get("total_score")
        rows.append(
            {
                "代码": code,
                "策略": candidate.get("strategy") or "",
                "收盘价": float(close) if close is not None else None,
                "brick_growth": float(brick_growth) if brick_growth is not None else None,
                "结论": review.get("verdict") or "",
                "总分": float(total_score) if total_score is not None else None,
                "信号": review.get("signal_type") or "",
                "评论": review.get("comment") or "",
            }
        )
    recommendation_codes = {item.get("code") for item in suggestion.get("recommendations", [])}
    for row in rows:
        row["推荐"] = "是" if row["代码"] in recommendation_codes else "否"
    return rows


def result_rows_from_history(pick_date: str) -> list[dict[str, Any]]:
    payload = load_history_results(pick_date, "all")
    rows: list[dict[str, Any]] = []
    for row in payload.get("results", []):
        code = str(row.get("code") or "")
        if not code:
            continue
        review = row.get("review") or {}
        close = row.get("close")
        brick_growth = row.get("brick_growth")
        total_score = review.get("total_score")
        rows.append(
            {
                "代码": code,
                "策略": row.get("strategy") or "",
                "收盘价": float(close) if close is not None else None,
                "brick_growth": float(brick_growth) if brick_growth is not None else None,
                "结论": review.get("verdict") or "",
                "总分": float(total_score) if total_score is not None else None,
                "信号": review.get("signal_type") or "",
                "评论": review.get("comment") or "",
                "推荐": "是" if row.get("status") == "recommended" else "否",
            }
        )
    return rows


def result_center_dates() -> list[str]:
    dates = set(history_dates())
    latest = latest_pick_date()
    if latest:
        dates.add(latest)
    return sorted(dates, reverse=True)


def result_rows_for_date(pick_date: str) -> list[dict[str, Any]]:
    history_payload = load_history_results(pick_date, "all")
    if history_payload:
        return result_rows_from_history(pick_date)
    if pick_date == latest_pick_date():
        return result_rows()
    return []


def qfq_return_since(code: str, start_date: str, end_date: str | None = None) -> dict[str, Any]:
    path = ROOT / "data" / "raw" / f"{code}.csv"
    if not path.exists():
        return {"error": f"未找到 {path}"}
    df = pd.read_csv(path)
    if df.empty or "date" not in df or "close" not in df:
        return {"error": "本地 K 线缺少 date/close 列"}
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return {"error": "本地 K 线无有效数据"}

    start_ts = pd.to_datetime(start_date)
    target_end = pd.to_datetime(end_date or dt.date.today().isoformat())
    future_rows = df[df["date"] >= start_ts]
    if future_rows.empty:
        return {"error": f"没有不早于 {start_date} 的本地 K 线"}
    available_end_rows = df[df["date"] <= target_end]
    if available_end_rows.empty:
        return {"error": f"没有不晚于 {target_end.strftime('%Y-%m-%d')} 的本地 K 线"}
    start_row = future_rows.iloc[0]
    latest_row = available_end_rows.iloc[-1]
    start_close = float(start_row["close"])
    latest_close = float(latest_row["close"])
    if start_close <= 0:
        return {"error": "起算收盘价无效"}
    return_pct = (latest_close / start_close - 1.0) * 100.0
    target_end_date = target_end.strftime("%Y-%m-%d")
    latest_date = pd.Timestamp(latest_row["date"]).strftime("%Y-%m-%d")
    return {
        "start_date": pd.Timestamp(start_row["date"]).strftime("%Y-%m-%d"),
        "target_end_date": target_end_date,
        "latest_date": latest_date,
        "start_close": start_close,
        "latest_close": latest_close,
        "return_pct": return_pct,
        "data_stale": latest_date < target_end_date,
    }


def render_return_action_table(df: pd.DataFrame, pick_date: str) -> None:
    if df.empty:
        return
    st.subheader("涨跌幅计算")
    today = dt.date.today().isoformat()
    st.caption(f"按本地前复权日线收盘价计算：起算交易日收盘价 → 当前日期（{today}）本地可用收盘价。")

    page_size = 25
    total_pages = max((len(df) - 1) // page_size + 1, 1)
    if total_pages > 1:
        page = st.number_input("页码", min_value=1, max_value=total_pages, value=1, step=1)
    else:
        page = 1
    start = (int(page) - 1) * page_size
    page_df = df.iloc[start : start + page_size]

    header_cols = st.columns([0.08, 0.14, 0.12, 0.12, 0.10, 0.12, 0.32])
    for col, text in zip(header_cols, ["序号", "代码", "策略", "收盘价", "总分", "推荐", "涨跌幅"], strict=True):
        col.caption(text)

    state_key = "result_return_calc"
    st.session_state.setdefault(state_key, {})
    calc_state: dict[str, Any] = st.session_state[state_key]

    for idx, row in page_df.iterrows():
        row_key = f"{pick_date}:{row['代码']}:{row.get('策略', '')}:{idx}"
        cols = st.columns([0.08, 0.14, 0.12, 0.12, 0.10, 0.12, 0.32])
        cols[0].write(row.get("序号", idx + 1))
        cols[1].write(row["代码"])
        cols[2].write(row.get("策略", ""))
        close = row.get("收盘价")
        cols[3].write(f"{close:.2f}" if isinstance(close, (int, float)) else "")
        score = row.get("总分")
        cols[4].write(f"{score:.1f}" if isinstance(score, (int, float)) else "")
        cols[5].write(row.get("推荐", ""))
        if cols[6].button("计算", key=f"calc_return_{row_key}"):
            calc_state[row_key] = qfq_return_since(str(row["代码"]), pick_date, today)
        result = calc_state.get(row_key)
        if result:
            if result.get("error"):
                cols[6].error(result["error"])
            else:
                value_text = (
                    f"{result['return_pct']:.2f}% "
                    f"({result['start_date']} {result['start_close']:.2f} → "
                    f"{result['latest_date']} {result['latest_close']:.2f})"
                )
                if result.get("data_stale"):
                    value_text += f"；当前日期 {result['target_end_date']}，本地数据截止 {result['latest_date']}"
                cols[6].write(value_text)


def strategy_summary_rows_from_result_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if df.empty or "策略" not in df:
        return rows
    for strategy, group in df.groupby("策略", dropna=False):
        strategy_name = str(strategy or "unknown")
        reviewed_count = int((group["结论"].fillna("") != "").sum())
        recommended_count = int((group["推荐"] == "是").sum())
        total_count = int(len(group))
        rows.append(
            {
                "策略": strategy_name,
                "候选": total_count,
                "已复评": reviewed_count,
                "推荐": recommended_count,
                "待处理": total_count - reviewed_count,
            }
        )
    return sorted(rows, key=lambda item: item["策略"])


def strategy_summary_rows_from_counts(strategy_counts: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy, counts in sorted(strategy_counts.items()):
        if not isinstance(counts, dict):
            continue
        total_count = int(counts.get("total", 0))
        recommended_count = int(counts.get("recommended", 0))
        if "pending" in counts or "excluded" in counts:
            reviewed_count = int(counts.get("reviewed", 0))
            pending_count = int(counts.get("pending", max(total_count - reviewed_count, 0)))
        else:
            non_recommended_reviewed = int(counts.get("reviewed", 0))
            reviewed_count = recommended_count + non_recommended_reviewed
            pending_count = int(counts.get("unreviewed", max(total_count - reviewed_count, 0)))
        rows.append(
            {
                "策略": str(strategy or "unknown"),
                "候选": total_count,
                "已复评": reviewed_count,
                "推荐": recommended_count,
                "待处理": pending_count,
            }
        )
    return rows


def render_result_center() -> None:
    st.title("结果中心")
    dates = result_center_dates()
    if not dates:
        st.warning("还没有候选结果。请先运行初选。")
        return

    selected_date = st.selectbox("选股日期", dates)
    rows = result_rows_for_date(selected_date)
    if not rows:
        st.warning("当前日期没有候选结果。")
        return
    render_result_metrics(selected_date, rows)
    df = pd.DataFrame(rows)
    summary_rows = strategy_summary_rows_from_result_df(df)
    if summary_rows:
        st.subheader("按策略汇总")
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)

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
    df = df.reset_index(drop=True)
    df.insert(0, "序号", range(1, len(df) + 1))
    st.caption(f"当前筛选结果：{len(df)} 条")
    st.dataframe(df, width="stretch", hide_index=True)
    render_return_action_table(df, selected_date)


def history_rows(pick_date: str, strategy: str) -> list[dict[str, Any]]:
    payload = load_history_results(pick_date, "all")
    rows = payload.get("results", [])
    if strategy != "全部":
        rows = [row for row in rows if str(row.get("strategy") or "") == strategy]
    return rows


def history_table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for row in rows:
        review = row.get("review") or {}
        close = row.get("close")
        brick_growth = row.get("brick_growth")
        total_score = review.get("total_score")
        table.append(
            {
                "排名": row.get("rank"),
                "代码": row.get("code") or "",
                "策略": row.get("strategy") or "",
                "状态": row.get("status") or "",
                "收盘价": float(close) if close is not None else None,
                "brick_growth": float(brick_growth) if brick_growth is not None else None,
                "结论": review.get("verdict") or "",
                "总分": float(total_score) if total_score is not None else None,
                "信号": review.get("signal_type") or "",
                "评论": review.get("comment") or "",
            }
        )
    return table


def render_history_metrics(summary: dict[str, Any]) -> None:
    strategy_counts = summary.get("strategy_counts", {})
    b1_rec_count = strategy_counts.get("b1", {}).get("recommended", 0)
    brick_rec_count = strategy_counts.get("brick", {}).get("recommended", 0)
    st.markdown(
        f"""
        <div class="metric-row">
          <div class="metric-card"><div class="metric-label">归档日期</div><div class="metric-value">{summary.get("date", "无")}</div></div>
          <div class="metric-card"><div class="metric-label">候选 / 已复评</div><div class="metric-value">{summary.get("candidate_count", 0)} / {summary.get("reviewed_count", 0)}</div></div>
          <div class="metric-card"><div class="metric-label">推荐数量</div><div class="metric-value">{summary.get("recommended_count", 0)}</div></div>
          <div class="metric-card"><div class="metric-label">B1 / 砖型图推荐</div><div class="metric-value">{b1_rec_count} / {brick_rec_count}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    summary_rows = strategy_summary_rows_from_counts(strategy_counts)
    if summary_rows:
        st.subheader("按策略汇总")
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)


def render_history_center() -> None:
    st.title("历史结果")
    dates = history_dates()
    if not dates:
        st.warning("还没有历史归档。完整流程或只跑复评成功后，会自动生成 data/history。")
        st.caption("也可以手动执行：python -m pipeline.archive_results")
        return

    h1, h2 = st.columns([0.28, 0.22])
    with h1:
        selected_date = st.selectbox("归档日期", dates)
    all_payload = load_history_results(selected_date, "all")
    all_rows = all_payload.get("results", [])
    strategies = sorted({str(row.get("strategy") or "") for row in all_rows if row.get("strategy")})
    with h2:
        selected_strategy = st.selectbox("策略", ["全部"] + strategies)

    summary = load_history_summary(selected_date)
    if summary:
        render_history_metrics(summary)
        st.caption(f"关联运行：{summary.get('run_id') or '无'} · 归档时间：{summary.get('archived_at') or '未知'}")

    rows = history_rows(selected_date, selected_strategy)
    if not rows:
        st.info("当前筛选条件下没有结果。")
        return

    table_df = pd.DataFrame(history_table_rows(rows))
    st.dataframe(table_df, width="stretch", hide_index=True)

    codes = [str(row.get("code")) for row in rows if row.get("code")]
    selected_code = st.selectbox("查看单票详情", codes)
    selected_row = next((row for row in rows if str(row.get("code")) == selected_code), {})
    if not selected_row:
        return

    left, right = st.columns([0.46, 0.54], gap="large")
    review = selected_row.get("review") or {}
    with left:
        st.subheader(f"{selected_code} · {selected_row.get('strategy', '')}")
        c1, c2, c3 = st.columns(3)
        c1.metric("状态", selected_row.get("status", ""))
        c2.metric("总分", review.get("total_score", ""))
        c3.metric("排名", selected_row.get("rank") or "-")
        chart_path = Path(str(selected_row.get("chart") or ""))
        if chart_path.exists():
            st.image(str(chart_path), caption=chart_path.name, width="stretch")
        else:
            st.info("未找到归档关联图表。")
    with right:
        st.subheader("复评内容")
        if review:
            st.write(review.get("comment", ""))
            with st.expander("原始复评 JSON", expanded=False):
                st.json(review)
        else:
            st.info("这只股票暂无复评结果。")


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


def render_paper_metrics(cfg: dict[str, Any]) -> None:
    value = portfolio_value(cfg, dt.date.today().isoformat())
    st.markdown(
        f"""
        <div class="metric-row">
          <div class="metric-card"><div class="metric-label">总资产</div><div class="metric-value">{value['total_value']:.2f}</div></div>
          <div class="metric-card"><div class="metric-label">可用现金</div><div class="metric-value">{value['cash']:.2f}</div></div>
          <div class="metric-card"><div class="metric-label">持仓市值</div><div class="metric-value">{value['market_value']:.2f}</div></div>
          <div class="metric-card"><div class="metric-label">持仓数量</div><div class="metric-value">{len(value['positions'])}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sync_initial_cash_runtime(cfg: dict[str, Any]) -> bool:
    global paper_trading_core
    func = getattr(paper_trading_core, "sync_initial_cash_if_pristine", None)
    if func is None:
        paper_trading_core = importlib.reload(paper_trading_core)
        func = getattr(paper_trading_core, "sync_initial_cash_if_pristine")
    return bool(func(cfg))


def render_paper_config(cfg: dict[str, Any]) -> dict[str, Any]:
    with st.expander("模拟交易参数", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            cfg["initial_cash"] = st.number_input("初始资金", min_value=1000, value=int(float(cfg.get("initial_cash", 20000))), step=1000)
            cfg["max_positions"] = st.number_input("最多持仓数", min_value=1, value=int(cfg.get("max_positions", 5)))
        with c2:
            cfg["max_new_buys_per_day"] = st.number_input("每日最多新买", min_value=0, value=int(cfg.get("max_new_buys_per_day", 2)))
            cfg["target_position_weight"] = st.number_input("单票目标仓位", min_value=0.01, max_value=1.0, value=float(cfg.get("target_position_weight", 0.2)), step=0.01)
        with c3:
            cfg["stop_loss_pct"] = st.number_input("止损比例", min_value=0.0, max_value=1.0, value=float(cfg.get("stop_loss_pct", 0.08)), step=0.01)
            cfg["take_profit_pct"] = st.number_input("止盈比例", min_value=0.0, max_value=3.0, value=float(cfg.get("take_profit_pct", 0.18)), step=0.01)
        with c4:
            cfg["min_hold_days"] = st.number_input("最短持仓天数", min_value=0, value=int(cfg.get("min_hold_days", 3)))
            cfg["max_hold_days"] = st.number_input("最长持仓天数", min_value=1, value=int(cfg.get("max_hold_days", 20)))
        c5, c6, c7, c8 = st.columns(4)
        with c5:
            cfg["buy_slippage_pct"] = st.number_input("买入滑点", min_value=0.0, max_value=0.1, value=float(cfg.get("buy_slippage_pct", 0.001)), step=0.001, format="%.3f")
        with c6:
            cfg["sell_slippage_pct"] = st.number_input("卖出滑点", min_value=0.0, max_value=0.1, value=float(cfg.get("sell_slippage_pct", 0.001)), step=0.001, format="%.3f")
        with c7:
            cfg["commission_rate"] = st.number_input("佣金率", min_value=0.0, max_value=0.01, value=float(cfg.get("commission_rate", 0.0003)), step=0.0001, format="%.4f")
        with c8:
            cfg["stamp_tax_rate"] = st.number_input("印花税率", min_value=0.0, max_value=0.01, value=float(cfg.get("stamp_tax_rate", 0.0005)), step=0.0001, format="%.4f")
        cfg["auto_confirm_generated_plan"] = st.toggle("生成计划后自动确认", value=bool(cfg.get("auto_confirm_generated_plan", False)))
        cfg["auto_execute_confirmed_plan"] = False
        cfg["signal_refresh_after_time"] = st.text_input(
            "今日信号刷新时间",
            value=str(cfg.get("signal_refresh_after_time", "16:00")),
            help="默认 16:00。该时间前如果今日没有完整归档，会沿用最近完整信号日。",
        )
        cfg["trade_calendar_provider"] = st.selectbox(
            "交易日历来源",
            ["tushare", "local_raw"],
            index=0 if str(cfg.get("trade_calendar_provider", "tushare")) == "tushare" else 1,
            help="tushare 会在需要未来交易日时尝试刷新并缓存；local_raw 只用本地 K 线日期推导。",
        )
        cfg["trade_calendar_lookahead_days"] = st.number_input(
            "交易日历前瞻天数",
            min_value=30,
            max_value=365,
            value=int(cfg.get("trade_calendar_lookahead_days", 120)),
            step=30,
        )
        cfg["trade_calendar_raw_sample_files"] = st.number_input(
            "本地日历采样 CSV 数",
            min_value=10,
            max_value=1000,
            value=int(cfg.get("trade_calendar_raw_sample_files", 120)),
            step=10,
        )
        cfg["trade_calendar_path"] = st.text_input(
            "交易日历缓存文件",
            value=str(cfg.get("trade_calendar_path", "data/trading/trading_calendar.json")),
        )
        if not sync_initial_cash_runtime(cfg):
            st.warning("模拟账户已有持仓或成交记录，初始资金不会自动覆盖现有账户。")
    return cfg


def plan_table(plan: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for idx, order in enumerate(plan.get("orders", []), 1):
        rows.append(
            {
                "序号": idx,
                "代码": order.get("code", ""),
                "方向": order.get("side", ""),
                "策略": order.get("strategy", ""),
                "数量": order.get("quantity", 0),
                "参考价": order.get("reference_price", 0),
                "估算金额": order.get("estimated_amount", 0),
                "状态": order.get("status", ""),
                "原因": order.get("reason", ""),
            }
        )
    return pd.DataFrame(rows)


def render_paper_flow_tab(cfg: dict[str, Any]) -> None:
    running = active_run_dir()
    target_date = clean_text(st.session_state.run_cfg.get("pick_date")) or dt.date.today().isoformat()
    complete = complete_history_result(target_date)
    c1, c2 = st.columns([0.35, 0.65], gap="large")
    with c1:
        st.subheader("今日模拟流程")
        st.caption(f"目标信号日：{target_date}；当日完整归档：{'已存在' if complete else '未发现'}")
        if running:
            state = run_state(running)
            st.info(f"{active_run_label(running)}任务运行中：{state.get('current_step') or '启动中'}")
        if st.button("运行今日模拟流程", type="primary", width="stretch", disabled=running is not None):
            try:
                run_dir = start_paper_trading_run()
                st.session_state.last_run_dir = str(run_dir)
                st.success(f"模拟交易流程已启动：{run_dir}")
            except RuntimeError as exc:
                st.warning(str(exc))
            st.rerun()
        if st.button("停止当前任务", width="stretch", disabled=running is None):
            if running:
                stop_background_run(running)
            st.rerun()
    with c2:
        current_run = latest_run_dir()
        render_run_log_panel(str(current_run) if current_run else "")


def render_paper_plan_tab(cfg: dict[str, Any]) -> None:
    files = plan_files(cfg)
    if not files:
        st.info("还没有交易计划。运行一次模拟交易流程后会生成下一交易日计划。")
        return
    labels = [path.stem.replace("plan_", "") for path in files]
    selected_execute_date = st.selectbox("计划执行日", labels)
    path = plan_path(cfg, selected_execute_date)
    plan = load_json(path)
    if not plan:
        st.warning("计划文件为空或无法读取。")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("计划状态", plan.get("status", ""))
    c2.metric("信号日", plan.get("signal_date", ""))
    c3.metric("执行日", plan.get("execute_date", ""))
    c4.metric("订单数", len(plan.get("orders", [])))

    a1, a2, a3, a4 = st.columns(4)
    if a1.button("确认计划", disabled=plan.get("status") not in {"draft"}, width="stretch"):
        update_plan_status(selected_execute_date, "confirmed", cfg)
        st.rerun()
    if a2.button("取消计划", disabled=plan.get("status") in {"canceled", "executed"}, width="stretch"):
        update_plan_status(selected_execute_date, "canceled", cfg)
        st.rerun()
    if a3.button("手动执行今日计划", disabled=selected_execute_date != dt.date.today().isoformat() or plan.get("status") not in {"confirmed"}, width="stretch"):
        result = execute_plan(selected_execute_date, cfg, allow_draft=False)
        st.info(result.get("message") or f"执行结果：{result.get('status')}")
        st.rerun()
    if a4.button("重新生成明日计划", width="stretch"):
        signal_date = clean_text(plan.get("signal_date")) or latest_pick_date()
        if signal_date:
            generate_plan(signal_date, cfg)
            st.rerun()

    df = plan_table(plan)
    if not df.empty:
        st.dataframe(df, width="stretch", hide_index=True)
    skipped = plan.get("skipped", [])
    if skipped:
        with st.expander("跳过记录", expanded=False):
            st.dataframe(pd.DataFrame(skipped), width="stretch", hide_index=True)
    with st.expander("原始计划 JSON", expanded=False):
        st.json(plan)


def render_paper_positions_tab(cfg: dict[str, Any]) -> None:
    value = portfolio_value(cfg, dt.date.today().isoformat())
    positions = value.get("positions", [])
    if not positions:
        st.info("当前没有模拟持仓。")
        return
    rows = []
    for pos in positions:
        rows.append(
            {
                "代码": pos.get("code", ""),
                "策略": pos.get("strategy", ""),
                "数量": pos.get("quantity", 0),
                "成本价": pos.get("avg_cost", 0),
                "估值价": pos.get("market_price", 0),
                "市值": pos.get("market_value", 0),
                "浮动盈亏": pos.get("unrealized_pnl", 0),
                "持仓天数": pos.get("hold_days", 0),
                "入场日期": pos.get("entry_date", ""),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    if st.button("按今天保存账户快照"):
        save_snapshot(dt.date.today().isoformat(), cfg)
        st.rerun()


def render_paper_equity_tab(cfg: dict[str, Any]) -> None:
    path = trading_dir(cfg) / "equity_curve.csv"
    if not path.exists():
        st.info("还没有收益曲线。执行计划或保存账户快照后会生成。")
        return
    df = pd.read_csv(path)
    if df.empty:
        st.info("收益曲线为空。")
        return
    st.line_chart(df.set_index("date")[["total_value", "return_pct"]])
    st.dataframe(df, width="stretch", hide_index=True)


def render_paper_trading() -> None:
    st.title("模拟交易")
    cfg = st.session_state.trading_cfg
    cfg = render_paper_config(cfg)
    st.session_state.trading_cfg = cfg
    render_paper_metrics(cfg)
    tab_flow, tab_plan, tab_positions, tab_equity = st.tabs(["流程", "交易计划", "持仓", "收益"])
    with tab_flow:
        render_paper_flow_tab(cfg)
    with tab_plan:
        render_paper_plan_tab(cfg)
    with tab_positions:
        render_paper_positions_tab(cfg)
    with tab_equity:
        render_paper_equity_tab(cfg)


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
            ["运行中心", "数据配置", "策略配置", "复评配置", "结果中心", "历史结果", "单票复盘", "模拟交易"],
            label_visibility="collapsed",
        )

    render_status_bar()
    if page == "运行中心":
        render_run_center()
    elif page == "数据配置":
        render_data_config()
    elif page == "策略配置":
        render_strategy_config()
    elif page == "复评配置":
        render_review_config()
    elif page == "结果中心":
        render_result_center()
    elif page == "历史结果":
        render_history_center()
    elif page == "模拟交易":
        render_paper_trading()
    else:
        render_stock_view()


if __name__ == "__main__":
    main()
