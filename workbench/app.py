"""
Local AgentTrader workbench.

This is an independent Streamlit entrypoint. It intentionally does not modify
or import dashboard/app.py.
"""
from __future__ import annotations

import base64
import datetime as dt
import importlib
import json
import logging
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yaml

logger = logging.getLogger(__name__)

try:
    from tornado.iostream import StreamClosedError as _TornadoStreamClosedError
    from tornado.websocket import WebSocketClosedError as _TornadoWebSocketClosedError
except Exception:  # noqa: BLE001 - Streamlit may be stubbed in unit tests.
    _TornadoStreamClosedError = None
    _TornadoWebSocketClosedError = None


class _ClosedWorkbenchWebSocketFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if "Task exception was never retrieved" not in record.getMessage():
            return True
        exc = record.exc_info[1] if record.exc_info else None
        if _TornadoWebSocketClosedError is not None and isinstance(exc, _TornadoWebSocketClosedError):
            return False
        if _TornadoStreamClosedError is not None and isinstance(exc, _TornadoStreamClosedError):
            return False
        return True


def install_workbench_log_filters() -> None:
    asyncio_logger = logging.getLogger("asyncio")
    if not any(isinstance(item, _ClosedWorkbenchWebSocketFilter) for item in asyncio_logger.filters):
        asyncio_logger.addFilter(_ClosedWorkbenchWebSocketFilter())


install_workbench_log_filters()

ROOT = Path(__file__).resolve().parent.parent
WORKBENCH_DIR = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "data" / "runs"
HISTORY_DIR = ROOT / "data" / "history"
CONSENSUS_DIR = ROOT / "data" / "review_consensus"
Z_QUALITY_DIR = ROOT / "data" / "z_quality"
RUN_MODES = ["完整流程", "跳过抓取", "初选+导出图表", "只抓取数据", "只跑初选", "只导出图表", "只跑复评"]
DEFAULT_CLASSIC_PATTERN_STRATEGIES = ("b1", "b2", "brick")
FORMAL_REVIEW_SOURCE = "formal"
AGY_REVIEW_SOURCE = "agy-cli"
LEGACY_AGY_REVIEW_SOURCE = "agy-cli-experimental"
CODEX_REVIEW_SOURCE = "codex-cli"
GEMINI_35_FLASH_HIGH = "gemini-3.5-flash-high"
GEMINI_31_PRO_HIGH = "gemini-3.1-pro-high"
GPT_55_HIGH = "gpt-5.5-high"
REVIEW_MODEL_SPECS = {
    GEMINI_35_FLASH_HIGH: {
        "label": "Gemini 3.5 Flash High",
        "backend": AGY_REVIEW_SOURCE,
        "backend_model": "Gemini 3.5 Flash (High)",
        "output_dir": f"data/review_models/{GEMINI_35_FLASH_HIGH}",
    },
    GEMINI_31_PRO_HIGH: {
        "label": "Gemini 3.1 Pro High",
        "backend": AGY_REVIEW_SOURCE,
        "backend_model": "Gemini 3.1 Pro (High)",
        "output_dir": f"data/review_models/{GEMINI_31_PRO_HIGH}",
    },
    GPT_55_HIGH: {
        "label": "GPT-5.5 High",
        "backend": CODEX_REVIEW_SOURCE,
        "backend_model": "gpt-5.5",
        "output_dir": f"data/review_models/{GPT_55_HIGH}",
    },
}
REVIEW_SOURCE_LABELS = {
    FORMAL_REVIEW_SOURCE: "历史正式复评",
    AGY_REVIEW_SOURCE: "AGY 旧结果",
    LEGACY_AGY_REVIEW_SOURCE: "AGY 旧结果",
    CODEX_REVIEW_SOURCE: "Codex GPT-5.5",
    **{key: str(spec["label"]) for key, spec in REVIEW_MODEL_SPECS.items()},
}
REVIEWER_OPTIONS = {
    GEMINI_35_FLASH_HIGH: REVIEW_MODEL_SPECS[GEMINI_35_FLASH_HIGH]["label"],
    GEMINI_31_PRO_HIGH: REVIEW_MODEL_SPECS[GEMINI_31_PRO_HIGH]["label"],
    GPT_55_HIGH: REVIEW_MODEL_SPECS[GPT_55_HIGH]["label"],
    "multi-model": "三模型共识",
}
REVIEWER_WIDGET_KEY = "reviewer_choice"
CODEX_AUTH_MODE_LOCAL_OAUTH = "local_oauth"
CODEX_AUTH_MODE_ENV_PROVIDER = "env_provider"
CODEX_AUTH_MODE_OPTIONS = {
    CODEX_AUTH_MODE_LOCAL_OAUTH: "本机 Codex OAuth（默认）",
    CODEX_AUTH_MODE_ENV_PROVIDER: "OpenAI-compatible 本地代理/API key",
}
AGY_MODELS_CACHE_KEY = "agy_models_cache"
MODEL_PROGRESS_ROW_RE = re.compile(
    r"^\s*-\s+(?P<key>[^:]+):\s+(?P<status>[^,]+)"
    r"(?:,\s+exit=(?P<exit>-?\d+))?,\s+elapsed=(?P<elapsed>[^,]+),\s+"
    r"progress=(?P<progress>.*?),\s+latest=(?P<latest>.*)$"
)
MODEL_CONFIG_ROW_RE = re.compile(r"\[(?:CONFIG|INFO)\]\s+reviewer config:\s+(?P<key>\S+)\s+->")
MODEL_RUNTIME_CONFIG_ROW_RE = re.compile(r"\[CONFIG\]\s+(?P<key>\S+)\s+->")
MODEL_START_ROW_RE = re.compile(r"\[(?:START|INFO)\].*?启动\s+(?P<key>\S+?)(?:\s+attempt=\d+|:|\s|$)")
MODEL_DONE_ROW_RE = re.compile(
    r"\[(?:DONE|INFO)\].*?(?P<key>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)\s+结束"
    r"(?:[，,]\s*exit=(?P<exit>-?\d+))?"
)
MODEL_OLD_RUNNING_ROW_RE = re.compile(r"多模型复评仍在运行：(?P<keys>.+)$")
MODEL_PROGRESS_TEXT_RE = re.compile(r"(?P<completed>\d+)\s*/\s*(?P<total>\d+)\s*\((?P<pct>\d+)%\)")
MODEL_SUCCESS_PROGRESS_TEXT_RE = re.compile(
    r"成功\s*(?P<success>\d+)\s*/\s*(?P<total>\d+)[，,]\s*失败/跳过\s*(?P<failed>\d+)\s*\((?P<pct>\d+)%\)"
)

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
    required_signal_strategies,
    save_snapshot,
    trading_config,
    trading_dir,
    update_plan_status,
)
import paper_trading.core as paper_trading_core  # noqa: E402
from pipeline import tdx_export  # noqa: E402


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


def review_key(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


def review_matches_strategy(review: dict[str, Any], code: str, strategy: str = "") -> bool:
    if not review:
        return False
    if not strategy:
        return True

    expected_key = review_key(code, strategy)
    stored_key = str(review.get("review_key") or "")
    if stored_key:
        return stored_key == expected_key

    stored_strategy = str(review.get("strategy") or "")
    return stored_strategy == strategy


def load_review_result(review_dir: Path, code: str, strategy: str = "") -> dict[str, Any]:
    keyed = load_json(review_dir / f"{review_key(code, strategy)}.json")
    if keyed:
        return keyed
    legacy = load_json(review_dir / f"{code}.json")
    if review_matches_strategy(legacy, code, strategy):
        return legacy
    return {}


def review_status_label(review: dict[str, Any], recommended: bool = False) -> str:
    if recommended:
        return "推荐"
    return "已复评" if review else "未复评"


def classic_pattern_match_score(review: dict[str, Any]) -> float | None:
    scores = review.get("scores") or {}
    if not isinstance(scores, dict):
        return None
    value = scores.get("classic_pattern_match")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def default_run_cfg() -> dict[str, Any]:
    return {
        "pick_date": dt.date.today().isoformat(),
        "end_date": "",
        "preselect_log_dir": "./data/logs",
        "reviewer": GEMINI_35_FLASH_HIGH,
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


def project_path(value: Any, fallback: str) -> Path:
    text = str(value or "").strip()
    path = Path(os.path.expanduser(text or fallback))
    return path if path.is_absolute() else ROOT / path


def model_output_dir(model_key: str) -> str:
    spec = REVIEW_MODEL_SPECS.get(model_key) or {}
    return str(spec.get("output_dir") or f"data/review_models/{model_key}")


def model_backend(model_key: str) -> str:
    spec = REVIEW_MODEL_SPECS.get(model_key) or {}
    return str(spec.get("backend") or "")


def model_backend_name(model_key: str) -> str:
    spec = REVIEW_MODEL_SPECS.get(model_key) or {}
    return str(spec.get("backend_model") or "")


def model_label(model_key: str) -> str:
    spec = REVIEW_MODEL_SPECS.get(model_key) or {}
    return str(spec.get("label") or model_key)


def should_use_model_default_output_dir(current: Any) -> bool:
    text = clean_text(current)
    if not text:
        return True
    known_defaults = {
        "data/review",
        "./data/review",
        "data/review/agy_cli",
        "./data/review/agy_cli",
        "data/review/agy_cli_experimental",
        "./data/review/agy_cli_experimental",
        "data/review/codex_cli",
        "./data/review/codex_cli",
        *[str(spec.get("output_dir") or "") for spec in REVIEW_MODEL_SPECS.values()],
    }
    return text in known_defaults


def apply_model_defaults_to_config(cfg: dict[str, Any], reviewer: str) -> dict[str, Any]:
    cfg["model_key"] = reviewer
    cfg["model"] = model_backend_name(reviewer)
    if should_use_model_default_output_dir(cfg.get("output_dir")):
        cfg["output_dir"] = model_output_dir(reviewer)
    return cfg


def agy_review_base_dir() -> Path:
    cfg: dict[str, Any] = {}
    try:
        session_cfg = st.session_state.get("agy_review_cfg", {})
        if isinstance(session_cfg, dict):
            cfg = session_cfg
    except Exception:
        cfg = {}
    if not cfg:
        cfg = load_yaml(ROOT / "config" / "agy_cli_review.yaml")
    return project_path(cfg.get("output_dir"), "data/review/agy_cli")


def codex_review_base_dir() -> Path:
    cfg: dict[str, Any] = {}
    try:
        session_cfg = st.session_state.get("codex_review_cfg", {})
        if isinstance(session_cfg, dict):
            cfg = session_cfg
    except Exception:
        cfg = {}
    if not cfg:
        cfg = load_yaml(ROOT / "config" / "codex_cli_review.yaml")
    return project_path(cfg.get("output_dir"), "data/review/codex_cli")


def review_base_dir(review_source: str = FORMAL_REVIEW_SOURCE) -> Path:
    if review_source in REVIEW_MODEL_SPECS:
        return project_path(model_output_dir(review_source), model_output_dir(review_source))
    if review_source == AGY_REVIEW_SOURCE:
        return agy_review_base_dir()
    if review_source == LEGACY_AGY_REVIEW_SOURCE:
        return ROOT / "data" / "review" / "agy_cli_experimental"
    if review_source == CODEX_REVIEW_SOURCE:
        return codex_review_base_dir()
    return ROOT / "data" / "review"


def review_dir_for_date(pick_date: str, review_source: str = FORMAL_REVIEW_SOURCE) -> Path:
    return review_base_dir(review_source) / pick_date


def load_review_suggestion(pick_date: str, review_source: str = FORMAL_REVIEW_SOURCE) -> dict[str, Any]:
    return load_json(review_dir_for_date(pick_date, review_source) / "suggestion.json")


def review_source_label(review_source: str) -> str:
    return REVIEW_SOURCE_LABELS.get(review_source, review_source)


def review_source_has_data(pick_date: str, review_source: str = FORMAL_REVIEW_SOURCE) -> bool:
    if review_source == FORMAL_REVIEW_SOURCE:
        return bool(load_history_results(pick_date, "all")) or pick_date == latest_pick_date()
    review_dir = review_dir_for_date(pick_date, review_source)
    if not review_dir.exists():
        return False
    return (review_dir / "suggestion.json").exists() or any(review_dir.glob("*.json"))


def review_sources_for_date(pick_date: str) -> list[str]:
    sources = [
        source
        for source in (
            GEMINI_35_FLASH_HIGH,
            GEMINI_31_PRO_HIGH,
            GPT_55_HIGH,
            AGY_REVIEW_SOURCE,
            LEGACY_AGY_REVIEW_SOURCE,
            CODEX_REVIEW_SOURCE,
            FORMAL_REVIEW_SOURCE,
        )
        if review_source_has_data(pick_date, source)
    ]
    return sources or [GEMINI_35_FLASH_HIGH]


def render_review_source_selectbox(label: str, pick_date: str, key: str) -> str:
    sources = review_sources_for_date(pick_date)
    labels = [review_source_label(source) for source in sources]
    selected_label = st.selectbox(label, labels, key=key)
    return sources[labels.index(selected_label)]


def latest_suggestion(review_source: str | None = None) -> dict[str, Any]:
    pick_date = latest_pick_date()
    if not pick_date:
        return {}
    if not review_source:
        review_source = review_sources_for_date(pick_date)[0]
    return load_review_suggestion(pick_date, review_source)


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
        ("AGY CLI", "ok" if shutil.which("agy") else "warn", "已安装" if shutil.which("agy") else "未找到"),
        ("Codex CLI", "ok" if shutil.which("codex") else "warn", "已安装" if shutil.which("codex") else "未找到"),
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
    if "agy_review_cfg" not in st.session_state:
        st.session_state.agy_review_cfg = load_yaml(ROOT / "config" / "agy_cli_review.yaml")
    if "codex_review_cfg" not in st.session_state:
        st.session_state.codex_review_cfg = load_yaml(ROOT / "config" / "codex_cli_review.yaml")
    if "multi_model_review_cfg" not in st.session_state:
        st.session_state.multi_model_review_cfg = load_yaml(ROOT / "config" / "multi_model_review.yaml")
    if "trading_cfg" not in st.session_state:
        st.session_state.trading_cfg = trading_config(ROOT / "config" / "paper_trading.yaml")
    if "run_cfg" not in st.session_state:
        st.session_state.run_cfg = default_run_cfg()
    apply_workbench_defaults()
    if "last_run_log" not in st.session_state:
        st.session_state.last_run_log = "[System] 工作台已启动，等待执行指令..."
    if "last_run_dir" not in st.session_state:
        st.session_state.last_run_dir = ""


def has_run_artifacts(path: Path) -> bool:
    return (
        (path / "run_state.json").exists()
        or (path / "run.log").exists()
        or (path / "multi_model_logs").is_dir()
    )


def run_dir_updated_at(path: Path) -> float:
    candidates = [path]
    for name in ("run_state.json", "run.log"):
        item = path / name
        if item.exists():
            candidates.append(item)
    logs_dir = path / "multi_model_logs"
    if logs_dir.is_dir():
        candidates.extend(item for item in logs_dir.iterdir() if item.is_file())
    return max(item.stat().st_mtime for item in candidates)


def list_run_dirs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []

    return sorted(
        [p for p in RUNS_DIR.iterdir() if p.is_dir() and has_run_artifacts(p)],
        key=run_dir_updated_at,
        reverse=True,
    )


def latest_run_dir() -> Path | None:
    for run_dir in list_run_dirs():
        if is_run_active(run_dir):
            st.session_state.last_run_dir = str(run_dir)
            return run_dir
    runs = list_run_dirs()
    if st.session_state.get("last_run_dir"):
        p = Path(str(st.session_state.last_run_dir))
        if p.exists() and has_run_artifacts(p):
            if not runs or p == runs[0] or run_dir_updated_at(p) >= run_dir_updated_at(runs[0]):
                return p
    latest = runs[0] if runs else None
    if latest is not None:
        st.session_state.last_run_dir = str(latest)
    return latest


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


def _process_table() -> list[dict[str, int]]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid="],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 - cleanup still falls back to the root process group.
        return []

    rows: list[dict[str, int]] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "pgid": int(parts[2])})
        except ValueError:
            continue
    return rows


def descendant_process_groups(root_pid: int) -> set[int]:
    rows = _process_table()
    children_by_parent: dict[int, list[dict[str, int]]] = {}
    for row in rows:
        children_by_parent.setdefault(row["ppid"], []).append(row)

    pgids: set[int] = set()
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        parent = stack.pop()
        if parent in seen:
            continue
        seen.add(parent)
        for child in children_by_parent.get(parent, []):
            pgids.add(child["pgid"])
            stack.append(child["pid"])

    root_row = next((row for row in rows if row["pid"] == root_pid), None)
    if root_row:
        pgids.add(root_row["pgid"])
    return {pgid for pgid in pgids if pgid > 0 and pgid != os.getpgrp()}


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
    updated.setdefault("b2", {})
    updated.setdefault("brick", {})
    if preset == "B1 策略":
        updated["b1"]["enabled"] = True
        updated["b2"]["enabled"] = False
        updated["brick"]["enabled"] = False
    elif preset == "B2 策略":
        updated["b1"]["enabled"] = False
        updated["b2"]["enabled"] = True
        updated["brick"]["enabled"] = False
    elif preset == "B1 + B2":
        updated["b1"]["enabled"] = True
        updated["b2"]["enabled"] = True
        updated["brick"]["enabled"] = False
    elif preset == "砖型图策略":
        updated["b1"]["enabled"] = False
        updated["b2"]["enabled"] = False
        updated["brick"]["enabled"] = True
    elif preset == "B1 + 砖型图":
        updated["b1"]["enabled"] = True
        updated["b2"]["enabled"] = False
        updated["brick"]["enabled"] = True
    elif preset == "B1 + B2 + 砖型图":
        updated["b1"]["enabled"] = True
        updated["b2"]["enabled"] = True
        updated["brick"]["enabled"] = True
    return updated


def make_run_id() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def config_bool(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def normalize_codex_auth_mode(cfg: dict[str, Any] | None) -> str:
    cfg = cfg or {}
    raw = clean_text(cfg.get("auth_mode") or cfg.get("codex_auth_mode")).lower().replace("-", "_")
    aliases = {
        "oauth": CODEX_AUTH_MODE_LOCAL_OAUTH,
        "local": CODEX_AUTH_MODE_LOCAL_OAUTH,
        "local_oauth": CODEX_AUTH_MODE_LOCAL_OAUTH,
        "codex_oauth": CODEX_AUTH_MODE_LOCAL_OAUTH,
        "native": CODEX_AUTH_MODE_LOCAL_OAUTH,
        "env": CODEX_AUTH_MODE_ENV_PROVIDER,
        "env_provider": CODEX_AUTH_MODE_ENV_PROVIDER,
        "local_proxy": CODEX_AUTH_MODE_ENV_PROVIDER,
        "proxy": CODEX_AUTH_MODE_ENV_PROVIDER,
        "apikey": CODEX_AUTH_MODE_ENV_PROVIDER,
        "api_key": CODEX_AUTH_MODE_ENV_PROVIDER,
    }
    if raw:
        return aliases.get(raw, CODEX_AUTH_MODE_LOCAL_OAUTH)
    if config_bool(cfg.get("env_provider_enabled"), default=False):
        return CODEX_AUTH_MODE_ENV_PROVIDER
    return CODEX_AUTH_MODE_LOCAL_OAUTH


def apply_codex_auth_mode(cfg: dict[str, Any], mode: str) -> dict[str, Any]:
    previous_mode = normalize_codex_auth_mode(cfg)
    mode = normalize_codex_auth_mode({"auth_mode": mode})
    cfg["auth_mode"] = mode
    if mode == CODEX_AUTH_MODE_ENV_PROVIDER:
        cfg["env_provider_enabled"] = True
        cfg["ignore_user_config"] = (
            config_bool(cfg.get("ignore_user_config"), default=True)
            if previous_mode == CODEX_AUTH_MODE_ENV_PROVIDER
            else True
        )
    else:
        cfg["env_provider_enabled"] = False
        cfg["ignore_user_config"] = False
    return cfg


def normalize_reviewer(value: Any) -> str:
    reviewer = clean_text(value) or GEMINI_35_FLASH_HIGH
    aliases = {
        "gemini-cli": GEMINI_35_FLASH_HIGH,
        "agy-cli": GEMINI_35_FLASH_HIGH,
        "agy-cli-experimental": GEMINI_35_FLASH_HIGH,
        "codex-cli": GPT_55_HIGH,
    }
    reviewer = aliases.get(reviewer, reviewer)
    return reviewer if reviewer in REVIEWER_OPTIONS else GEMINI_35_FLASH_HIGH


def ensure_reviewer_widget_state() -> None:
    current = normalize_reviewer(st.session_state.run_cfg.get("reviewer"))
    if st.session_state.get(REVIEWER_WIDGET_KEY) not in REVIEWER_OPTIONS:
        st.session_state[REVIEWER_WIDGET_KEY] = current


def sync_reviewer_from_widget() -> None:
    run_cfg = st.session_state.get("run_cfg", default_run_cfg())
    run_cfg["reviewer"] = normalize_reviewer(st.session_state.get(REVIEWER_WIDGET_KEY))
    st.session_state.run_cfg = run_cfg


def snapshot_reviewer_configs(reviewer: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    agy_cfg = dict(st.session_state.get("agy_review_cfg", {}) or {})
    codex_cfg = dict(st.session_state.get("codex_review_cfg", {}) or {})
    multi_cfg = dict(st.session_state.get("multi_model_review_cfg", {}) or {})

    if reviewer not in REVIEW_MODEL_SPECS:
        return agy_cfg, codex_cfg, multi_cfg

    backend = model_backend(reviewer)
    if backend == AGY_REVIEW_SOURCE:
        agy_cfg = apply_model_defaults_to_config(agy_cfg, reviewer)
    elif backend == CODEX_REVIEW_SOURCE:
        codex_cfg = apply_model_defaults_to_config(codex_cfg, reviewer)
    return agy_cfg, codex_cfg, multi_cfg


def parse_agy_models_output(output: str) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        model = line.strip()
        if not model or model in seen:
            continue
        models.append(model)
        seen.add(model)
    return models


def agy_models_cache() -> dict[str, dict[str, Any]]:
    cache = st.session_state.get(AGY_MODELS_CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
        st.session_state[AGY_MODELS_CACHE_KEY] = cache
    return cache


def clear_agy_models_cache(agy_bin: str | None = None) -> None:
    cache = agy_models_cache()
    if agy_bin:
        cache.pop(clean_text(agy_bin) or "agy", None)
    else:
        cache.clear()


def cached_agy_model_options(agy_bin: str) -> tuple[list[str], str, str]:
    command = clean_text(agy_bin) or "agy"
    entry = agy_models_cache().get(command)
    if not isinstance(entry, dict):
        return [], "", ""
    models = entry.get("models")
    if not isinstance(models, list):
        models = []
    return (
        [str(model) for model in models if str(model).strip()],
        str(entry.get("error") or ""),
        str(entry.get("fetched_at") or ""),
    )


def store_agy_model_options(agy_bin: str, models: list[str], error: str = "") -> None:
    command = clean_text(agy_bin) or "agy"
    agy_models_cache()[command] = {
        "models": models,
        "error": error,
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def fetch_agy_model_options(agy_bin: str) -> tuple[list[str], str]:
    command = clean_text(agy_bin) or "agy"
    try:
        result = subprocess.run(
            [command, "models"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - UI should surface the CLI failure.
        return [], str(exc)

    models = parse_agy_models_output(result.stdout or "")
    if result.returncode != 0:
        error = (result.stderr or result.stdout or f"agy models exited with {result.returncode}").strip()
        return [], error
    if not models:
        return [], "agy models 没有返回模型名称"

    return models, ""


def classic_pattern_switch_enabled(cfg: dict[str, Any]) -> bool:
    if "classic_pattern_enabled" in cfg:
        return bool(cfg.get("classic_pattern_enabled"))
    if "classic_pattern_strategies" in cfg:
        return bool(cfg.get("classic_pattern_strategies"))
    return True


def render_classic_pattern_config(cfg: dict[str, Any], key_prefix: str) -> dict[str, Any]:
    enabled = st.toggle(
        "启用经典图形匹配环节",
        value=classic_pattern_switch_enabled(cfg),
        key=f"{key_prefix}_classic_pattern_enabled",
        help="开启后按候选票来源策略自动匹配对应经典图形；复合策略或没有经典图形定义的策略仍按基础四维评分。",
    )
    cfg["classic_pattern_enabled"] = enabled
    cfg.pop("classic_pattern_strategies", None)
    st.caption(
        f"当前已有经典图形定义：{', '.join(DEFAULT_CLASSIC_PATTERN_STRATEGIES)}。"
        "开启后由复评器按候选策略自动判断，页面不需要逐个选择策略。"
    )
    return cfg


def render_codex_auth_mode_config(cfg: dict[str, Any], key_prefix: str) -> dict[str, Any]:
    mode = normalize_codex_auth_mode(cfg)
    mode_keys = list(CODEX_AUTH_MODE_OPTIONS)
    if mode not in mode_keys:
        mode = CODEX_AUTH_MODE_LOCAL_OAUTH

    selected_mode = st.radio(
        "Codex 调用模式",
        mode_keys,
        index=mode_keys.index(mode),
        format_func=lambda item: CODEX_AUTH_MODE_OPTIONS[item],
        key=f"{key_prefix}_auth_mode",
        help="默认使用本机 Codex CLI/App OAuth 登录态；只有明确需要本地 OpenAI-compatible 代理时才切换到 API key 模式。",
    )
    cfg = apply_codex_auth_mode(cfg, selected_mode)

    if selected_mode == CODEX_AUTH_MODE_LOCAL_OAUTH:
        st.info("默认模式：读取本机 Codex CLI OAuth 登录态和用户配置，不向子进程传递 API key 或本地 base URL 环境变量。")
        return cfg

    st.warning("本地代理/API key 是兼容模式；只有明确需要 CCSwitch 或其他 OpenAI-compatible 代理时使用。")
    p1, p2 = st.columns(2)
    with p1:
        cfg["codex_provider_name"] = st.text_input(
            "Provider 名称",
            value=str(cfg.get("codex_provider_name", "env_custom")),
            key=f"{key_prefix}_provider_name",
        )
        cfg["codex_base_url"] = st.text_input(
            "固定 base URL",
            value=str(cfg.get("codex_base_url", "")),
            key=f"{key_prefix}_base_url",
            help="留空时按下面的 base URL 环境变量顺序读取。",
        )
        cfg["ignore_user_config"] = st.toggle(
            "忽略 ~/.codex/config.toml",
            value=config_bool(cfg.get("ignore_user_config"), default=True),
            key=f"{key_prefix}_ignore_user_config",
            help="代理模式默认隔离用户配置，只使用本次命令注入的 provider。",
        )
    with p2:
        base_vars = cfg.get("base_url_env_vars") or ["CODEX_OPENAI_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"]
        api_vars = cfg.get("api_key_env_vars") or ["CODEX_OPENAI_API_KEY", "OPENAI_API_KEY"]
        base_vars_text = ",".join(str(item) for item in base_vars)
        api_vars_text = ",".join(str(item) for item in api_vars)
        cfg["base_url_env_vars"] = [
            item.strip()
            for item in st.text_input(
                "base URL 环境变量",
                value=base_vars_text,
                key=f"{key_prefix}_base_url_env_vars",
            ).split(",")
            if item.strip()
        ]
        cfg["api_key_env_vars"] = [
            item.strip()
            for item in st.text_input(
                "API key 环境变量",
                value=api_vars_text,
                key=f"{key_prefix}_api_key_env_vars",
            ).split(",")
            if item.strip()
        ]
    st.caption("代理模式会把 base URL 注入为 Codex CLI 的 model_provider，并把 API key 环境变量仅转发到子进程，不写入命令行。")
    return cfg


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
    reviewer = normalize_reviewer(st.session_state.run_cfg.get("reviewer"))
    st.session_state.run_cfg["reviewer"] = reviewer
    agy_cfg, codex_cfg, multi_cfg = snapshot_reviewer_configs(reviewer)
    write_yaml(run_dir / "fetch_kline.yaml", st.session_state.fetch_cfg)
    write_yaml(run_dir / "rules_preselect.yaml", st.session_state.rules_cfg)
    write_yaml(run_dir / "gemini_cli_review.yaml", st.session_state.review_cfg)
    write_yaml(run_dir / "gemini_review.yaml", st.session_state.api_review_cfg)
    write_yaml(run_dir / "agy_cli_review.yaml", agy_cfg)
    write_yaml(run_dir / "codex_cli_review.yaml", codex_cfg)
    write_yaml(run_dir / "multi_model_review.yaml", multi_cfg)
    write_json(run_dir / "run_options.json", st.session_state.run_cfg)
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
            "agy_cli_config": str(run_dir / "agy_cli_review.yaml"),
            "codex_cli_config": str(run_dir / "codex_cli_review.yaml"),
            "multi_model_config": str(run_dir / "multi_model_review.yaml"),
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
    reviewer = normalize_reviewer(st.session_state.run_cfg.get("reviewer"))
    st.session_state.run_cfg["reviewer"] = reviewer
    agy_cfg, codex_cfg, multi_cfg = snapshot_reviewer_configs(reviewer)
    write_yaml(run_dir / "fetch_kline.yaml", st.session_state.fetch_cfg)
    write_yaml(run_dir / "rules_preselect.yaml", st.session_state.rules_cfg)
    write_yaml(run_dir / "gemini_cli_review.yaml", st.session_state.review_cfg)
    write_yaml(run_dir / "gemini_review.yaml", st.session_state.api_review_cfg)
    write_yaml(run_dir / "agy_cli_review.yaml", agy_cfg)
    write_yaml(run_dir / "codex_cli_review.yaml", codex_cfg)
    write_yaml(run_dir / "multi_model_review.yaml", multi_cfg)
    write_yaml(run_dir / "paper_trading.yaml", st.session_state.trading_cfg)
    write_json(run_dir / "run_options.json", st.session_state.run_cfg)
    command = [sys.executable, "-m", "paper_trading.daily_flow", "--run-dir", str(run_dir)]
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
            "agy_cli_config": str(run_dir / "agy_cli_review.yaml"),
            "codex_cli_config": str(run_dir / "codex_cli_review.yaml"),
            "multi_model_config": str(run_dir / "multi_model_review.yaml"),
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
    reviewer = normalize_reviewer(run_cfg.get("reviewer"))
    backend = model_backend(reviewer)
    if reviewer == "gemini-api":
        review_step = ("Gemini API 复评", [python, "agent/gemini_review.py", "--config", str(run_dir / "gemini_review.yaml")])
    elif reviewer == "multi-model":
        review_step = (
            "多模型复评与共识汇总",
            [python, "agent/multi_model_review.py", "--run-dir", str(run_dir)],
        )
    elif backend == CODEX_REVIEW_SOURCE:
        review_step = (
            f"{model_label(reviewer)} 复评",
            [python, "agent/codex_cli_review.py", "--config", str(run_dir / "codex_cli_review.yaml")],
        )
    elif backend == AGY_REVIEW_SOURCE:
        review_step = (
            f"{model_label(reviewer)} 复评",
            [python, "agent/agy_cli_review.py", "--config", str(run_dir / "agy_cli_review.yaml")],
        )
    else:
        review_step = (
            f"{model_label(GEMINI_35_FLASH_HIGH)} 复评",
            [python, "agent/agy_cli_review.py", "--config", str(run_dir / "agy_cli_review.yaml")],
        )
    agy_cfg, codex_cfg, _ = snapshot_reviewer_configs(reviewer)
    output_dir = clean_text((codex_cfg if backend == CODEX_REVIEW_SOURCE else agy_cfg).get("output_dir"))
    isolated_reviewer = reviewer == "multi-model" or (
        reviewer in REVIEW_MODEL_SPECS and output_dir not in {"data/review", "./data/review"}
    )
    archive_step = [] if isolated_reviewer else [("归档当日结果", [python, "-m", "pipeline.archive_results", "--run-id", run_id])]
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
            *archive_step,
        ],
        "跳过抓取": [
            ("量化初选", preselect_cmd),
            ("导出候选图表", [python, "dashboard/export_kline_charts.py"]),
            review_step,
            *archive_step,
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
            *archive_step,
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
        pgids = descendant_process_groups(pid) or {pid}
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            os.kill(pid, signal.SIGTERM)
        for pgid in sorted(pgids):
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                try:
                    os.kill(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        time.sleep(1)
        live_pgids = {row["pgid"] for row in _process_table() if row["pgid"] in pgids}
        for pgid in sorted(live_pgids):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                try:
                    os.kill(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
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


def _progress_status_label(status: str, exit_code: str | None = None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "finished":
        return "完成" if exit_code in {None, "", "0"} else "失败"
    if normalized == "running":
        return "运行中"
    if normalized in {"starting", "configured", "waiting"}:
        return "等待"
    return str(status or "等待")


def progress_model_key(raw_key: Any) -> str:
    key = clean_text(raw_key)
    aliases = {
        "agy-cli/gemini-3.5-flash-high": GEMINI_35_FLASH_HIGH,
        "agy-cli-experimental/gemini-3.5-flash-high": GEMINI_35_FLASH_HIGH,
        "agy-cli/gemini-3.1-pro-high": GEMINI_31_PRO_HIGH,
        "agy-cli-experimental/gemini-3.1-pro-high": GEMINI_31_PRO_HIGH,
        "codex-cli/gpt-5.5-high": GPT_55_HIGH,
        "codex-cli/gpt-5.5-high-standard": GPT_55_HIGH,
    }
    if key in aliases:
        return aliases[key]
    if "/" in key:
        return key.rsplit("/", 1)[-1].strip() or key
    return key or "unknown"


def progress_model_display_name(model_key_value: Any) -> str:
    key = progress_model_key(model_key_value)
    return model_label(key) if key in REVIEW_MODEL_SPECS else key


def progress_model_sort_index(model_key_value: Any, fallback: int) -> tuple[int, int, str]:
    key = progress_model_key(model_key_value)
    known_order = list(REVIEW_MODEL_SPECS)
    if key in known_order:
        return (0, known_order.index(key), key)
    return (1, fallback, key)


def _progress_row_defaults(key: str) -> dict[str, Any]:
    model_key_value = progress_model_key(key)
    return {
        "key": model_key_value,
        "display_key": progress_model_display_name(model_key_value),
        "raw_keys": [key] if key and key != model_key_value else [],
        "status": "waiting",
        "status_label": "等待",
        "exit_code": "",
        "elapsed": "",
        "completed": None,
        "total": None,
        "percent": 0,
        "count_text": "等待输出",
        "progress_text": "等待输出",
        "latest": "",
    }


def _parse_progress_text(progress_text: str) -> tuple[int | None, int | None, int, str]:
    text = str(progress_text or "")
    success_match = MODEL_SUCCESS_PROGRESS_TEXT_RE.search(text)
    if success_match:
        success = int(success_match.group("success"))
        total = int(success_match.group("total"))
        failed = int(success_match.group("failed"))
        percent = int(success_match.group("pct"))
        completed = min(total, success + failed)
        return completed, total, max(0, min(100, percent)), f"成功 {success}/{total}，失败/跳过 {failed}"

    match = MODEL_PROGRESS_TEXT_RE.search(text)
    if not match:
        text = text.strip() or "等待输出"
        return None, None, 0, text
    completed = int(match.group("completed"))
    total = int(match.group("total"))
    percent = int(match.group("pct"))
    return completed, total, max(0, min(100, percent)), f"处理到 {completed}/{total}"


def multi_model_progress_rows(log_text: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def ensure_row(key: str) -> dict[str, Any]:
        raw_key = str(key or "").strip()
        model_key_value = progress_model_key(raw_key)
        if model_key_value not in rows:
            rows[model_key_value] = _progress_row_defaults(raw_key or model_key_value)
            order.append(model_key_value)
        row = rows[model_key_value]
        if raw_key and raw_key != model_key_value and raw_key not in row.get("raw_keys", []):
            row.setdefault("raw_keys", []).append(raw_key)
        return row

    for raw_line in str(log_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        for pattern in (MODEL_CONFIG_ROW_RE, MODEL_RUNTIME_CONFIG_ROW_RE, MODEL_START_ROW_RE):
            match = pattern.search(line)
            if match:
                row = ensure_row(match.group("key"))
                if pattern is MODEL_START_ROW_RE:
                    row["status"] = "running"
                    row["status_label"] = "运行中"
                break

        running_match = MODEL_OLD_RUNNING_ROW_RE.search(line)
        if running_match:
            for key in running_match.group("keys").split(","):
                row = ensure_row(key.strip())
                if row.get("status") == "waiting":
                    row["status"] = "running"
                    row["status_label"] = "运行中"

        done_match = MODEL_DONE_ROW_RE.search(line)
        if done_match:
            row = ensure_row(done_match.group("key"))
            exit_code = (done_match.group("exit") or row.get("exit_code") or "").strip()
            row["status"] = "finished"
            row["exit_code"] = exit_code
            row["status_label"] = _progress_status_label("finished", exit_code)

        progress_match = MODEL_PROGRESS_ROW_RE.match(raw_line)
        if not progress_match:
            continue

        key = progress_match.group("key").strip()
        status = progress_match.group("status").strip()
        exit_code = (progress_match.group("exit") or "").strip()
        progress_text = progress_match.group("progress").strip()
        completed, total, percent, count_text = _parse_progress_text(progress_text)
        row = ensure_row(key)
        row.update(
            {
                "status": status,
                "status_label": _progress_status_label(status, exit_code),
                "exit_code": exit_code,
                "elapsed": progress_match.group("elapsed").strip(),
                "completed": completed,
                "total": total,
                "percent": percent,
                "count_text": count_text,
                "progress_text": progress_text,
                "latest": progress_match.group("latest").strip(),
            }
        )

    ordered_keys = sorted(
        order,
        key=lambda key: progress_model_sort_index(key, order.index(key)),
    )
    return [rows[key] for key in ordered_keys]


def compact_run_log_for_display(log_text: str) -> str:
    if not multi_model_progress_rows(log_text):
        return log_text

    compacted: list[str] = []
    for raw_line in str(log_text or "").splitlines():
        line = raw_line.strip()
        if "多模型复评进度 attempt=" in line:
            continue
        if MODEL_PROGRESS_ROW_RE.match(raw_line):
            continue
        if re.match(r"^\s+\[[^\]]+\]\s*$", raw_line):
            continue
        if MODEL_OLD_RUNNING_ROW_RE.search(line):
            continue
        compacted.append(raw_line)
    return "\n".join(compacted)


def multi_model_progress_html(rows: list[dict[str, Any]]) -> str:
    rendered_rows: list[str] = []
    for row in rows:
        status = str(row.get("status_label") or "等待")
        status_class = "ok" if status == "完成" else "error" if status == "失败" else "running"
        percent = int(row.get("percent") or 0)
        key = escape_log(str(row.get("display_key") or row.get("key") or ""))
        count_text = escape_log(str(row.get("count_text") or row.get("progress_text") or "等待输出"))
        elapsed = escape_log(str(row.get("elapsed") or ""))
        rendered_rows.append(
            "".join(
                [
                    '<div class="review-progress-row">',
                    f'<div class="review-progress-model">{key}</div>',
                    f'<div class="review-progress-status {status_class}">{escape_log(status)}</div>',
                    f'<div class="review-progress-count">{count_text}</div>',
                    f'<div class="review-progress-bar"><span style="width:{percent}%"></span></div>',
                    f'<div class="review-progress-percent">{percent}%</div>',
                    f'<div class="review-progress-elapsed">{elapsed}</div>',
                    "</div>",
                ]
            )
        )

    return "\n".join(
        [
            "<style>",
            ".review-progress-wrap { border: 1px solid #d9dee7; border-radius: 6px; margin: 0 0 10px; overflow: hidden; }",
            ".review-progress-title { background: #f5f7fb; border-bottom: 1px solid #d9dee7; color: #273142; font-size: 13px; font-weight: 600; padding: 8px 10px; }",
            ".review-progress-row { align-items: center; border-bottom: 1px solid #edf0f5; display: grid; gap: 10px; grid-template-columns: minmax(128px, 180px) 64px minmax(168px, max-content) minmax(120px, 1fr) 48px 60px; min-height: 34px; padding: 7px 10px; }",
            ".review-progress-row:last-child { border-bottom: 0; }",
            '.review-progress-model, .review-progress-count, .review-progress-percent, .review-progress-elapsed { color: #2f3848; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
            ".review-progress-status { border-radius: 4px; font-size: 12px; line-height: 20px; text-align: center; }",
            ".review-progress-status.running { background: #e8f1ff; color: #1f5da8; }",
            ".review-progress-status.ok { background: #e8f6ef; color: #167044; }",
            ".review-progress-status.error { background: #fdecec; color: #b42318; }",
            ".review-progress-bar { background: #e6eaf0; border-radius: 999px; height: 7px; overflow: hidden; }",
            ".review-progress-bar span { background: #2f6fec; display: block; height: 100%; }",
            "@media (max-width: 760px) { .review-progress-row { grid-template-columns: minmax(0, 1fr) 58px 72px 42px; } .review-progress-bar, .review-progress-elapsed { display: none; } }",
            "</style>",
            '<div class="review-progress-wrap">',
            '<div class="review-progress-title">多模型复评进度</div>',
            *rendered_rows,
            "</div>",
        ]
    )


def render_multi_model_progress(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    st.markdown(multi_model_progress_html(rows), unsafe_allow_html=True)


def render_log_console(log_text: str, *, storage_key: str) -> None:
    escaped_key = json.dumps(storage_key)
    components.html(
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
    log_text = read_run_log(run_dir)
    progress_rows = multi_model_progress_rows(log_text)
    st.subheader(f"运行日志 · {status_label}")
    render_multi_model_progress(progress_rows)
    render_log_console(compact_run_log_for_display(log_text), storage_key=str(run_dir or "default"))
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
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        fetch_cfg["start"] = st.text_input("下载开始日期", value=str(fetch_cfg.get("start", "20190101")), placeholder="YYYYMMDD 或 today")
    with c2:
        fetch_cfg["end"] = st.text_input("下载结束日期", value=str(fetch_cfg.get("end", "today")), placeholder="YYYYMMDD 或 today")
    with c3:
        fetch_cfg["workers"] = st.number_input("并发下载线程 workers", min_value=1, max_value=16, value=int(fetch_cfg.get("workers", 4)))
    with c4:
        fetch_cfg["tushare_requests_per_minute"] = st.number_input(
            "Tushare 限速/分钟",
            min_value=30,
            max_value=200,
            value=int(fetch_cfg.get("tushare_requests_per_minute", 180)),
            help="qfq 抓取会隐式调用 adj_factor；建议低于 Tushare 200次/分钟配额。",
        )

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
        fetch_cfg["tushare_rate_cooldown_seconds"] = st.number_input(
            "限流冷却秒数",
            min_value=30,
            max_value=600,
            value=int(fetch_cfg.get("tushare_rate_cooldown_seconds", 70)),
        )

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
    cfg.setdefault("b2", {})
    cfg.setdefault("brick", {})

    preset = st.selectbox(
        "策略预设",
        ["B1 策略", "B2 策略", "B1 + B2", "砖型图策略", "B1 + 砖型图", "B1 + B2 + 砖型图", "自定义"],
        index=0,
    )
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

    left, middle, right = st.columns(3, gap="large")
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

    with middle:
        st.subheader("B2 策略参数")
        b2 = cfg["b2"]
        b2["enabled"] = st.toggle("启用 B2", value=bool(b2.get("enabled", False)))
        b2["b1_lookback"] = st.number_input("B1 回看窗口", min_value=1, max_value=5, value=int(b2.get("b1_lookback", 2)))
        b2["min_return"] = st.number_input("最低收盘涨幅", value=float(b2.get("min_return", 0.04)), step=0.001, format="%.4f")
        b2["min_today_body_pct"] = st.number_input("当日实体阳线阈值", value=float(b2.get("min_today_body_pct", 0.003)), step=0.001, format="%.3f")
        b2["j_ceiling"] = st.number_input("J 安全上限", value=float(b2.get("j_ceiling", 55.0)), step=1.0)
        b2["require_j_turn_up"] = st.toggle("要求 J 相对 B1 日拐头", value=bool(b2.get("require_j_turn_up", True)))
        b2["volume_ratio_min"] = st.number_input("放量阈值", value=float(b2.get("volume_ratio_min", 1.0)), step=0.01, format="%.2f")
        b2["flat_volume_ratio"] = st.number_input("平量阈值", value=float(b2.get("flat_volume_ratio", 0.98)), step=0.01, format="%.2f")
        b2["min_yang_bao_yin_body_pct"] = st.number_input(
            "阳包阴实体阈值",
            value=float(b2.get("min_yang_bao_yin_body_pct", 0.003)),
            step=0.001,
            format="%.3f",
        )
        b2["upper_shadow_soft_limit"] = st.number_input("上影线软阈值", value=float(b2.get("upper_shadow_soft_limit", 0.15)), step=0.01, format="%.2f")

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
    ensure_reviewer_widget_state()
    st.radio(
        "复评模型",
        list(REVIEWER_OPTIONS),
        format_func=lambda key: REVIEWER_OPTIONS[key],
        horizontal=True,
        key=REVIEWER_WIDGET_KEY,
        on_change=sync_reviewer_from_widget,
    )
    reviewer = normalize_reviewer(st.session_state.get(REVIEWER_WIDGET_KEY))
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

        with st.expander("经典图形匹配", expanded=True):
            cfg = render_classic_pattern_config(cfg, "api_review")

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

    if reviewer in REVIEW_MODEL_SPECS and model_backend(reviewer) == AGY_REVIEW_SOURCE:
        cfg = st.session_state.agy_review_cfg
        cfg = apply_model_defaults_to_config(cfg, reviewer)
        left, right = st.columns(2, gap="large")
        with left:
            cfg["agy_bin"] = st.text_input("AGY CLI 路径", value=str(cfg.get("agy_bin", "agy")))
            agy_bin = str(cfg.get("agy_bin", "agy"))
            model_options, model_error, model_fetched_at = cached_agy_model_options(agy_bin)
            st.caption(f"复评模型：{model_label(reviewer)}")
            st.caption(f"底层模型名：{cfg['model']}")
            if model_options and cfg["model"] not in model_options:
                st.warning(f"当前模型 `{cfg['model']}` 不在 `agy models` 列表中，请确认本机 AGY 版本和账号可用。")
            elif model_fetched_at:
                st.caption(f"AGY 模型列表缓存于 {model_fetched_at}。")
            elif model_error:
                st.warning(f"上次读取 AGY 模型列表失败：{model_error}")
            else:
                st.info("页面不会自动执行 `agy models`；需要校验本机可用模型时可手动刷新。")
            if st.button(
                "校验/刷新 AGY 模型列表",
                key="agy_model_refresh",
                help="手动执行 `agy models`；本机实测约 4 秒。",
            ):
                with st.spinner("正在执行 `agy models` ..."):
                    refreshed_models, refresh_error = fetch_agy_model_options(agy_bin)
                store_agy_model_options(agy_bin, refreshed_models, refresh_error)
                if refreshed_models:
                    st.session_state.agy_review_cfg = cfg
                    st.rerun()
                st.warning(f"读取 AGY 模型列表失败：{refresh_error or '未知错误'}")
            cfg["print_timeout"] = st.text_input("print timeout", value=str(cfg.get("print_timeout", "3m")))
            cfg["timeout_seconds"] = st.number_input("单次超时秒数", min_value=30, value=int(cfg.get("timeout_seconds", 180)), step=30)
            cfg["request_delay"] = st.number_input("请求间隔 request_delay", min_value=0.0, value=float(cfg.get("request_delay", 10)), step=1.0)
            max_items_value = cfg.get("max_items")
            limit_enabled = max_items_value not in {"", None, 0, "0"}
            limit_enabled = st.toggle(
                "限制复评数量",
                value=limit_enabled,
                key="agy_limit_enabled",
                help="关闭时完整复评全部候选；开启后只复评前 N 个候选，适合 smoke test。",
            )
            if limit_enabled:
                cfg["max_items"] = st.number_input(
                    "复评上限 max_items",
                    min_value=1,
                    value=int(max_items_value or 1),
                )
            else:
                cfg["max_items"] = None
            st.caption("复评上限只限制处理数量，不影响评分口径；候选顺序沿用复评器的策略优先级排序。")
        with right:
            cfg["suggest_min_score"] = st.number_input("推荐分数门槛", min_value=0.0, max_value=5.0, value=float(cfg.get("suggest_min_score", 4.0)), step=0.1)
            cfg["skip_existing"] = st.toggle("断点续跑 skip_existing", value=bool(cfg.get("skip_existing", True)))
            cfg["save_raw_cli_io"] = st.toggle("保存 AGY 原始调用日志", value=bool(cfg.get("save_raw_cli_io", True)))
            cfg["json_repair_enabled"] = st.toggle("JSON repair", value=bool(cfg.get("json_repair_enabled", True)))
            cfg["json_repair_prompt_max_chars"] = st.number_input("repair 原文字符上限", min_value=1000, value=int(cfg.get("json_repair_prompt_max_chars", 12000)), step=1000)
            agy_path = shutil.which(str(cfg.get("agy_bin", "agy")))
            st.caption(f"AGY CLI: {agy_path or '未找到'}")

        with st.expander("经典图形匹配", expanded=True):
            cfg = render_classic_pattern_config(cfg, "agy_review")

        with st.expander("路径配置"):
            p1, p2 = st.columns(2)
            with p1:
                cfg["candidates"] = st.text_input("候选列表 JSON", value=str(cfg.get("candidates", "data/candidates/candidates_latest.json")))
                cfg["kline_dir"] = st.text_input("候选图表目录", value=str(cfg.get("kline_dir", "data/kline")))
                cfg["prompt_path"] = st.text_input("提示词文件", value=str(cfg.get("prompt_path", "agent/prompt.md")))
            with p2:
                cfg["output_dir"] = st.text_input("模型结果输出目录", value=str(cfg.get("output_dir", model_output_dir(reviewer))))
                cfg["raw_log_dir"] = st.text_input("AGY 原始日志目录", value=str(cfg.get("raw_log_dir", "")), help="留空时使用 output_dir/{pick_date}/agy_cli_runs。")
                cfg["settings_path"] = st.text_input("AGY settings 路径", value=str(cfg.get("settings_path", "~/.gemini/antigravity-cli/settings.json")))

        st.markdown(
            "<div class='panel-note'>当前选择的是 Google 模型，底层固定通过 AGY 执行。模型结果默认按模型 ID 隔离输出；若输出目录不是 <code>data/review</code>，运行计划会跳过归档步骤。</div>",
            unsafe_allow_html=True,
        )
        st.session_state.agy_review_cfg = cfg
        return

    if reviewer in REVIEW_MODEL_SPECS and model_backend(reviewer) == CODEX_REVIEW_SOURCE:
        cfg = st.session_state.codex_review_cfg
        cfg = apply_model_defaults_to_config(cfg, reviewer)
        left, right = st.columns(2, gap="large")
        with left:
            cfg["codex_bin"] = st.text_input("Codex CLI 路径", value=str(cfg.get("codex_bin", "codex")))
            cfg["batch_size"] = st.number_input("批处理大小 batch_size", min_value=1, max_value=20, value=int(cfg.get("batch_size", 5)))
            cfg["timeout_seconds"] = st.number_input("单次超时秒数", min_value=60, value=int(cfg.get("timeout_seconds", 900)), step=30)
            cfg["request_delay"] = st.number_input("请求间隔 request_delay", min_value=0.0, value=float(cfg.get("request_delay", 1)), step=1.0)
            cfg["max_items"] = st.number_input(
                "复评上限 max_items",
                min_value=1,
                value=int(cfg.get("max_items", 1)),
                help="单独使用 Codex reviewer 时最多复评前 N 个候选；多模型复评会覆盖为完整候选集。",
            )
        with right:
            st.caption(f"复评模型：{model_label(reviewer)}")
            st.caption(f"底层模型名：{cfg['model']}")
            st.caption("固定思考强度：high")
            st.caption("速度路径：standard（禁用 fast 默认路径）")
            cfg["suggest_min_score"] = st.number_input("推荐分数门槛", min_value=0.0, max_value=5.0, value=float(cfg.get("suggest_min_score", 4.0)), step=0.1)
            cfg["skip_existing"] = st.toggle("断点续跑 skip_existing", value=bool(cfg.get("skip_existing", True)))
            cfg["save_raw_cli_io"] = st.toggle("保存 Codex 原始调用日志", value=bool(cfg.get("save_raw_cli_io", True)))
            cfg["fallback_to_single_on_batch_error"] = st.toggle(
                "批量失败后拆小批/逐只复评（同一模型）",
                value=bool(cfg.get("fallback_to_single_on_batch_error", True)),
            )
            codex_path = shutil.which(str(cfg.get("codex_bin", "codex")))
            st.caption(f"Codex CLI: {codex_path or '未找到'}")

        with st.expander("Codex 调用模式", expanded=True):
            cfg = render_codex_auth_mode_config(cfg, "codex_review")

        with st.expander("经典图形匹配", expanded=True):
            cfg = render_classic_pattern_config(cfg, "codex_review")

        with st.expander("路径配置"):
            p1, p2 = st.columns(2)
            with p1:
                cfg["candidates"] = st.text_input("候选列表 JSON", value=str(cfg.get("candidates", "data/candidates/candidates_latest.json")))
                cfg["kline_dir"] = st.text_input("候选图表目录", value=str(cfg.get("kline_dir", "data/kline")))
                cfg["prompt_path"] = st.text_input("提示词文件", value=str(cfg.get("prompt_path", "agent/prompt.md")))
            with p2:
                cfg["output_dir"] = st.text_input("模型结果输出目录", value=str(cfg.get("output_dir", model_output_dir(reviewer))))
                cfg["raw_log_dir"] = st.text_input("Codex 原始日志目录", value=str(cfg.get("raw_log_dir", "")), help="留空时使用 output_dir/{pick_date}/codex_cli_runs。")

        st.markdown(
            "<div class='panel-note'>当前选择的是 GPT 模型，底层固定通过 Codex CLI 执行。默认走本机 Codex OAuth，并通过 <code>--output-schema</code> 返回 JSON。</div>",
            unsafe_allow_html=True,
        )
        st.session_state.codex_review_cfg = cfg
        return

    if reviewer == "multi-model":
        cfg = st.session_state.multi_model_review_cfg
        left, right = st.columns(2, gap="large")
        with left:
            expected = cfg.get("expected_strategies", list(DEFAULT_CLASSIC_PATTERN_STRATEGIES))
            expected_text = ",".join(str(item) for item in expected)
            cfg["expected_strategies"] = [item.strip() for item in st.text_input("必需策略", value=expected_text).split(",") if item.strip()]
            cfg["suggest_min_score"] = st.number_input("统一推荐分数门槛", min_value=0.0, max_value=5.0, value=float(cfg.get("suggest_min_score", 4.0)), step=0.1)
            cfg["batch_size"] = st.number_input("默认批处理大小", min_value=1, max_value=20, value=int(cfg.get("batch_size", 5)))
            cfg["strict_batch"] = st.toggle("批次完整性严格校验", value=bool(cfg.get("strict_batch", True)))
            cfg["skip_existing"] = st.toggle("各模型断点续跑", value=bool(cfg.get("skip_existing", True)))
            cfg["no_model_substitution"] = st.toggle(
                "禁止模型替换/降级",
                value=bool(cfg.get("no_model_substitution", True)),
                help="模型不可用或失败时只记录原因，并按同一模型重跑；不会自动替换成其它模型。",
            )
            cfg["rerun_failed_models_once"] = st.toggle(
                "失败模型结束后重跑一次",
                value=bool(cfg.get("rerun_failed_models_once", True)),
                help="多模型首轮完成后，仅对失败模型按原模型再跑一次；已完成结果通过 skip_existing 跳过。",
            )
            cfg["classic_pattern_enabled"] = st.toggle(
                "统一启用经典图形匹配",
                value=bool(cfg.get("classic_pattern_enabled", True)),
                help="多模型复评会把该开关强制传给每个 reviewer，保证同一批横向比较使用相同评分环节。",
            )
        with right:
            cfg["candidates"] = st.text_input("候选列表 JSON", value=str(cfg.get("candidates", "data/candidates/candidates_latest.json")))
            cfg["kline_dir"] = st.text_input("候选图表目录", value=str(cfg.get("kline_dir", "data/kline")))
            cfg["prompt_path"] = st.text_input("提示词文件", value=str(cfg.get("prompt_path", "agent/prompt.md")))
            cfg["batch_root"] = st.text_input("冻结批次目录", value=str(cfg.get("batch_root", "data/review_batches")))
            cfg["review_runs_dir"] = st.text_input("模型独立输出根目录", value=str(cfg.get("review_runs_dir", "data/review_runs")))
            cfg["consensus_dir"] = st.text_input("共识汇总目录", value=str(cfg.get("consensus_dir", "data/review_consensus")))

        st.subheader("参与复评的模型")
        st.caption("Google 模型通过 AGY 执行；GPT 模型通过 Codex CLI 执行。共识、进度、筛选和导出都以模型 ID 为准。")
        reviewers = cfg.get("reviewers") or []
        for index, spec in enumerate(reviewers):
            label = str(spec.get("label") or spec.get("model_key") or spec.get("reviewer_key") or f"model-{index + 1}")
            c1, c2, c3, c4 = st.columns([0.24, 0.30, 0.30, 0.12])
            with c1:
                spec["enabled"] = st.toggle(label, value=bool(spec.get("enabled", True)), key=f"multi_model_enabled_{index}")
            with c2:
                spec["model_key"] = st.text_input(
                    "模型 ID",
                    value=str(spec.get("model_key") or spec.get("model_profile") or ""),
                    key=f"multi_model_model_key_{index}",
                    help="共识汇总、进度日志、筛选和导出都使用这个模型 ID。",
                )
            with c3:
                spec["model"] = st.text_input("后端模型名", value=str(spec.get("model", "")), key=f"multi_model_model_{index}")
            with c4:
                spec["batch_size"] = st.number_input(
                    "batch",
                    min_value=1,
                    max_value=20,
                    value=int(spec.get("batch_size") or cfg.get("batch_size", 5)),
                    key=f"multi_model_batch_{index}",
                )
        cfg["reviewers"] = reviewers
        with st.expander("Codex 子模型调用模式", expanded=True):
            st.caption("多模型复评会把这里的 Codex 配置写入本次 run snapshot；默认走本机 Codex OAuth。")
            st.session_state.codex_review_cfg = render_codex_auth_mode_config(
                st.session_state.codex_review_cfg,
                "multi_model_codex",
            )
        st.markdown(
            "<div class='panel-note'>多模型复评会先冻结候选批次，再按模型 ID 并行启动。两个 Google 模型默认通过 AGY 执行，GPT 模型默认通过 Codex CLI 执行；共识、进度、筛选和导出以模型 ID 为维度。失败模型会记录日志和失败原因，可按原模型断点重跑，不会自动替换模型。</div>",
            unsafe_allow_html=True,
        )
        st.session_state.multi_model_review_cfg = cfg
        return

    st.warning("未知复评模型，已切换到 Gemini 3.5 Flash High。")
    run_cfg["reviewer"] = GEMINI_35_FLASH_HIGH
    st.session_state.run_cfg = run_cfg


def result_rows_from_candidates(
    candidates_data: dict[str, Any],
    pick_date: str,
    review_source: str = FORMAL_REVIEW_SOURCE,
) -> list[dict[str, Any]]:
    suggestion = load_review_suggestion(pick_date, review_source)
    rows: list[dict[str, Any]] = []
    review_dir = review_dir_for_date(pick_date, review_source)
    for candidate in candidates_data.get("candidates", []):
        code = str(candidate.get("code") or "")
        if not code:
            continue
        strategy = str(candidate.get("strategy") or "")
        item_key = review_key(code, strategy)
        review = load_review_result(review_dir, code, strategy)
        close = candidate.get("close")
        brick_growth = candidate.get("brick_growth")
        total_score = review.get("total_score")
        has_review = bool(review)
        rows.append(
            {
                "代码": code,
                "策略": strategy,
                "review_key": item_key,
                "收盘价": float(close) if close is not None else None,
                "brick_growth": float(brick_growth) if brick_growth is not None else None,
                "复评状态": review_status_label(review),
                "结论": review.get("verdict") or ("未复评" if not has_review else ""),
                "总分": float(total_score) if total_score is not None else None,
                "信号": review.get("signal_type") or "",
                "经典图形": review.get("classic_pattern_type") or "",
                "经典匹配分": classic_pattern_match_score(review),
                "评论": review.get("comment") or ("暂无复评结果" if not has_review else ""),
            }
        )
    recommendation_keys = {
        item.get("review_key") or review_key(str(item.get("code") or ""), str(item.get("strategy") or ""))
        for item in suggestion.get("recommendations", [])
    }
    for row in rows:
        recommended = row["review_key"] in recommendation_keys
        row["推荐"] = "是" if recommended else "否"
        if recommended:
            row["复评状态"] = "推荐"
        row.pop("review_key", None)
    return rows


def result_rows(review_source: str = FORMAL_REVIEW_SOURCE) -> list[dict[str, Any]]:
    candidates_data = load_candidates()
    pick_date = str(candidates_data.get("pick_date") or "")
    if not pick_date:
        return []
    return result_rows_from_candidates(candidates_data, pick_date, review_source)


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
        has_review = bool(review)
        recommended = row.get("status") == "recommended"
        rows.append(
            {
                "代码": code,
                "策略": row.get("strategy") or "",
                "收盘价": float(close) if close is not None else None,
                "brick_growth": float(brick_growth) if brick_growth is not None else None,
                "复评状态": review_status_label(review, recommended),
                "结论": review.get("verdict") or ("未复评" if not has_review else ""),
                "总分": float(total_score) if total_score is not None else None,
                "信号": review.get("signal_type") or "",
                "经典图形": review.get("classic_pattern_type") or "",
                "经典匹配分": classic_pattern_match_score(review),
                "评论": review.get("comment") or ("暂无复评结果" if not has_review else ""),
                "推荐": "是" if recommended else "否",
            }
        )
    return rows


def result_center_dates() -> list[str]:
    dates = set(history_dates())
    latest = latest_pick_date()
    if latest:
        dates.add(latest)
    for source in (
        GEMINI_35_FLASH_HIGH,
        GEMINI_31_PRO_HIGH,
        GPT_55_HIGH,
        AGY_REVIEW_SOURCE,
        LEGACY_AGY_REVIEW_SOURCE,
        CODEX_REVIEW_SOURCE,
    ):
        base = review_base_dir(source)
        if base.exists():
            dates.update(
                p.name
                for p in base.iterdir()
                if p.is_dir() and review_source_has_data(p.name, source)
            )
    return sorted(dates, reverse=True)


def result_rows_for_date(pick_date: str, review_source: str = FORMAL_REVIEW_SOURCE) -> list[dict[str, Any]]:
    history_payload = load_history_results(pick_date, "all") if review_source == FORMAL_REVIEW_SOURCE else {}
    if history_payload and review_source == FORMAL_REVIEW_SOURCE:
        return result_rows_from_history(pick_date)
    if pick_date == latest_pick_date():
        return result_rows(review_source)
    candidates_data = load_candidates_for_date(pick_date)
    if candidates_data:
        return result_rows_from_candidates(candidates_data, pick_date, review_source)
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
        if "复评状态" in group:
            reviewed_count = int(group["复评状态"].isin(["已复评", "推荐"]).sum())
        else:
            reviewed_count = int(group["总分"].notna().sum())
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


def build_tdx_import_page(blocks: list[dict[str, Any]], pick_date: str, mode_label: str) -> str:
    template_path = WORKBENCH_DIR / "assets" / "tdx_importer.html"
    template = _read_text(template_path)
    if not template:
        return ""
    return (
        template
        .replace("__BLOCKS_JSON__", json.dumps(blocks, ensure_ascii=False))
        .replace("__PICK_DATE_JSON__", json.dumps(pick_date, ensure_ascii=False))
        .replace("__MODE_LABEL_JSON__", json.dumps(mode_label, ensure_ascii=False))
    )


def render_tdx_browser_import(blocks: list[dict[str, Any]], pick_date: str, mode_label: str) -> None:
    html = build_tdx_import_page(blocks, pick_date, mode_label)
    if not html:
        st.error("未找到浏览器导入页面模板：workbench/assets/tdx_importer.html")
        return

    filename = tdx_export.import_html_filename(pick_date, mode_label)
    st.download_button(
        label="下载独立导入页 (.html)",
        data=html.encode("utf-8"),
        file_name=filename,
        mime="text/html",
        width="stretch",
        key=f"tdx_html_download_{tdx_export.date_suffix(pick_date)}_{mode_label}",
    )

    html_b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    launcher = f"""
    <style>
      body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
      .wrap {{ padding: 10px 0; }}
      button {{ padding: 8px 16px; border: none; border-radius: 6px; background: #1f6feb; color: #fff; font-weight: 600; cursor: pointer; }}
      p {{ margin: 8px 0 0; color: #667788; font-size: 12px; }}
    </style>
    <div class="wrap">
      <button id="open">在新窗口打开独立导入页</button>
      <p>如果新窗口打不开，请使用上方下载按钮，把 HTML 文件保存到 Windows 后用 Chrome/Edge 打开。</p>
    </div>
    <script>
      const htmlB64 = "{html_b64}";
      function b64ToUtf8(b64) {{
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return new TextDecoder("utf-8").decode(bytes);
      }}
      document.getElementById("open").addEventListener("click", () => {{
        const blob = new Blob([b64ToUtf8(htmlB64)], {{ type: "text/html;charset=utf-8" }});
        const url = URL.createObjectURL(blob);
        window.open(url, "_blank", "noopener,noreferrer");
      }});
    </script>
    """
    components.html(launcher, height=96)


def render_tdx_blocks_preview(blocks: list[dict[str, Any]]) -> None:
    total = sum(b["count"] for b in blocks)
    st.caption(f"共 {len(blocks)} 个板块，{total} 只股票 — 板块名：{'、'.join(b['name'] for b in blocks)}")
    st.table(
        [
            {
                "板块名称": b["name"],
                "股票数量": f"{b['count']}只",
                "示例代码": "、".join(b.get("samples") or []),
            }
            for b in blocks
        ]
    )


def render_tdx_import_tabs(
    blocks: list[dict[str, Any]],
    pick_date: str,
    mode_label: str,
    *,
    key_suffix: str,
) -> None:
    tab_download, tab_browser, tab_local = st.tabs([
        "下载一键导入脚本（推荐）",
        "独立页面写入 .blk（诊断/备用）",
        "直接写入本地路径（服务与软件在同台电脑）",
    ])
    safe_key = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(key_suffix or mode_label or "tdx"))

    with tab_download:
        st.write("##### **使用说明：**")
        st.markdown(
            "1. 点击下方按钮下载 `.bat` 脚本文件。\n"
            "2. 将文件保存到 Windows 电脑上的**任意位置**。\n"
            "3. **双击运行**该脚本，它会自动检测通达信目录，写入 `.blk` 板块数据并更新 `blocknew.cfg` 索引。\n"
            "4. 运行完成后**重启通达信**即可在自定义板块中看到新板块。\n\n"
            "> 说明：Chrome/Edge 会限制网页创建或按名称访问 `.cfg` 文件，`blocknew.cfg` 注册必须优先使用 Windows 本地脚本完成。"
        )
        try:
            bat_content = tdx_export.generate_import_bat(blocks)
            bat_bytes = bat_content.encode("ascii", errors="ignore")
            bat_filename = tdx_export.import_bat_filename(pick_date)
            st.caption(f"将下载：`{bat_filename}`")
            st.download_button(
                label="下载一键导入脚本 (.bat)",
                data=bat_bytes,
                file_name=bat_filename,
                mime="application/octet-stream",
                width="stretch",
                key=f"tdx_bat_download_{tdx_export.date_suffix(pick_date)}_{safe_key}",
            )
        except Exception as e:
            st.error(f"生成脚本失败: {e}")

    with tab_browser:
        st.markdown(
            "这个页面只适合验证浏览器是否能写入 Windows 本机 `T0002\\blocknew` 目录下的 `.blk` 文件。"
            "由于 Chrome/Edge 会限制 `.cfg` 文件访问，它不再作为完整导入路径。"
        )
        st.caption(
            "如果这里写入成功但通达信不显示板块，仍需使用上方 `.bat` 脚本完成 blocknew.cfg 注册。"
        )
        render_tdx_browser_import(blocks, pick_date, mode_label)

    with tab_local:
        settings_path = ROOT / "config" / "tdx_settings.json"
        settings = load_json(settings_path)
        saved_path = settings.get("blocknew_dir", "")
        blocknew_dir = st.text_input(
            "通达信 blocknew 目录绝对路径",
            value=saved_path,
            key=f"blocknew_dir_input_{safe_key}",
            help="请输入您通达信安装目录下的 T0002/blocknew 目录。例如：\n"
                 "Windows: C:\\new_tdx\\T0002\\blocknew\n"
                 "macOS (Wine/CrossOver): /Users/用户名/Library/Application Support/CrossOver/Bottles/.../drive_c/new_tdx/T0002/blocknew\n"
                 "macOS (原生版): /Users/用户名/Library/Application Support/通达信/T0002/blocknew"
        )
        if not blocknew_dir:
            st.info("💡 请先输入通达信 `blocknew` 目录的绝对路径。")

        if st.button("🚀 一键写入", type="primary", disabled=not blocknew_dir, width="stretch", key=f"tdx_write_{safe_key}"):
            path_obj = Path(blocknew_dir.strip())
            if not path_obj.exists():
                st.error("❌ 输入的路径不存在，请检查是否输入正确。")
            elif not path_obj.is_dir():
                st.error("❌ 输入的路径不是一个文件夹，请输入 blocknew 目录本身。")
            else:
                settings["blocknew_dir"] = str(path_obj.resolve())
                try:
                    settings_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(settings_path, "w", encoding="utf-8") as f:
                        json.dump(settings, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.error("保存 tdx_settings.json 失败: %s", e)

                with st.spinner("正在写入文件..."):
                    res = tdx_export.export_to_tdx(path_obj, blocks)

                if res["succeeded"] > 0:
                    st.success(f"✅ 成功导入 {res['succeeded']} 个板块的文件！")
                    if res["cfg_ok"]:
                        st.success("✅ 成功在 blocknew.cfg 索引文件中完成板块注册！")
                    else:
                        st.warning(f"⚠️ 板块文件已写入，但在 blocknew.cfg 注册失败: {res['error']}")
                    st.info("📌 **重要提示**：如果您的通达信软件已经打开，请**重新启动通达信软件**，以便重新加载自定义板块列表。")
                else:
                    st.error(f"❌ 导入失败: {res.get('error', '未知错误')}")


@st.dialog("导入通达信")
def render_tdx_import_dialog(pick_date: str) -> None:
    suggestion = load_json(ROOT / "data" / "review" / pick_date / "suggestion.json")
    min_score = suggestion.get("min_score_threshold", 4.0) if suggestion else 4.0

    st.caption(f"选股日期：**{pick_date}**")

    mode = st.radio("导入范围", ["仅推荐", "全部候选"], horizontal=True)
    mode_key = "recommended" if mode == "仅推荐" else "all"

    blocks = tdx_export.build_blocks(pick_date, mode=mode_key, min_score=min_score)

    if not blocks:
        st.warning("没有可导入的板块数据")
        return

    render_tdx_blocks_preview(blocks)
    render_tdx_import_tabs(blocks, pick_date, mode, key_suffix=f"formal_{pick_date}_{mode_key}")

def render_result_center() -> None:
    st.title("结果中心")
    dates = result_center_dates()
    if not dates:
        st.warning("还没有候选结果。请先运行初选。")
        return

    selected_date = st.selectbox("选股日期", dates)
    review_source = render_review_source_selectbox(
        "复评结果源",
        selected_date,
        key=f"result_review_source_{selected_date}",
    )
    rows = result_rows_for_date(selected_date, review_source)
    if not rows:
        st.warning("当前日期没有候选结果。")
        return
    st.caption(f"复评结果源：{review_source_label(review_source)}")
    render_result_metrics(selected_date, rows)

    # ── 导入通达信按钮 ────────────────────────────────────────────────
    suggestion_for_date = load_review_suggestion(selected_date, review_source)
    has_recommendations = bool(suggestion_for_date.get("recommendations"))
    tdx_disabled = review_source != FORMAL_REVIEW_SOURCE or not has_recommendations
    if review_source != FORMAL_REVIEW_SOURCE:
        tdx_help = "隔离复评结果仅用于查看；通达信导入仍使用正式 Gemini 推荐"
    elif tdx_disabled:
        tdx_help = "没有可导入的推荐股票"
    else:
        tdx_help = "将正式推荐股票导出为通达信自定义板块"
    with st.columns([0.7, 0.3])[1]:
        if st.button(
            "📊 导入通达信",
            width="stretch",
            disabled=tdx_disabled,
            help=tdx_help,
            key=f"tdx_import_open_{selected_date}_{review_source}",
        ):
            render_tdx_import_dialog(selected_date)

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


DECISION_BUCKET_LABELS = {
    "all_models_recommended": "所有模型推荐",
    "majority_recommended": "多数模型推荐",
    "single_model_recommended": "单模型推荐",
    "partial_recommended": "部分模型推荐",
    "none_recommended": "无模型推荐",
    "incomplete": "评分不完整",
}

Z_QUALITY_VERDICT_LABELS = {
    "A_SELECT": "A精选",
    "B_WATCH": "B观察",
    "C_REVIEW_ONLY": "C复盘",
    "REJECT": "剔除",
}

CONSENSUS_TDX_PRESETS = {
    "共同推荐": {"prefix": "CA"},
    "多模型推荐": {"prefix": "CM"},
    "单模型推荐": {"prefix": "CS"},
    "共同观察": {"prefix": "CWA"},
    "多模型观察": {"prefix": "CW"},
    "单模型观察": {"prefix": "CSW"},
    "Z精选": {"prefix": "ZA"},
    "Z观察": {"prefix": "ZW"},
    "Z精选+观察": {"prefix": "ZQ"},
    "Z复盘样本": {"prefix": "ZR"},
    "分歧样本": {"prefix": "CD"},
    "全部自定义": {"prefix": "C"},
}

MODEL_STATE_OPTIONS = ["推荐", "观察", "不推荐", "缺失"]
CONSENSUS_VERDICT_OPTIONS = ["PASS", "WATCH", "FAIL", "INCOMPLETE"]


def consensus_summary_files() -> list[Path]:
    if not CONSENSUS_DIR.exists():
        return []
    files = [p / "summary.json" for p in CONSENSUS_DIR.iterdir() if p.is_dir() and (p / "summary.json").exists()]
    files.sort(key=lambda path: (load_json(path).get("generated_at") or "", path.parent.name), reverse=True)
    return files


def consensus_label(summary_path: Path) -> str:
    summary = load_json(summary_path)
    batch_id = summary.get("batch_id") or summary_path.parent.name
    pick_date = summary.get("pick_date") or ""
    all_count = summary.get("all_models_recommended_count", 0)
    complete = "完整" if summary.get("complete") else "不完整"
    return f"{pick_date} · {batch_id} · 全票 {all_count} · {complete}"


def load_consensus_payload(summary_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    summary = load_json(summary_path)
    base = summary_path.parent
    decisions = load_json(base / "decisions.json")
    details = load_json(base / "details.json")
    return summary, decisions if isinstance(decisions, list) else [], details if isinstance(details, list) else []


def load_z_quality_decisions(summary: dict[str, Any]) -> list[dict[str, Any]]:
    z_quality = summary.get("z_quality") or {}
    path_text = str(z_quality.get("decisions") or "")
    if not path_text:
        batch_id = str(summary.get("batch_id") or "")
        if batch_id:
            path_text = str(Z_QUALITY_DIR / batch_id / "decisions.json")
    if not path_text:
        return []
    payload = load_json(Path(path_text))
    return payload if isinstance(payload, list) else []


def z_quality_by_key(z_decisions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for item in z_decisions:
        key = str(item.get("review_key") or review_key(str(item.get("code") or ""), str(item.get("strategy") or "")))
        if key:
            by_key[key] = item
    return by_key


def z_quality_label(verdict: str) -> str:
    verdict = str(verdict or "")
    return Z_QUALITY_VERDICT_LABELS.get(verdict, verdict)


def _join_short(values: Any, *, limit: int = 3) -> str:
    if not isinstance(values, list):
        return ""
    return "；".join(str(item) for item in values[:limit] if str(item))


def consensus_decision_table_rows(
    decisions: list[dict[str, Any]],
    models: list[str],
    z_by_key: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    z_by_key = z_by_key or {}
    for decision in decisions:
        item_key = str(decision.get("review_key") or review_key(str(decision.get("code") or ""), str(decision.get("strategy") or "")))
        z_item = z_by_key.get(item_key, {})
        z_score = z_item.get("z_quality_score")
        row: dict[str, Any] = {
            "排名": decision.get("rank"),
            "代码": decision.get("code"),
            "策略": decision.get("strategy"),
            "决策分组": DECISION_BUCKET_LABELS.get(str(decision.get("decision_bucket")), decision.get("decision_bucket")),
            "Z裁决": z_quality_label(str(z_item.get("z_quality_verdict") or "")),
            "Z分": float(z_score) if z_score not in {"", None} else None,
            "Z硬否决": "、".join(str(item) for item in (z_item.get("hard_vetoes") or [])),
            "Z观察限制": "、".join(str(item) for item in (z_item.get("watch_caps") or [])),
            "Z理由": _join_short(z_item.get("quality_reasons")),
            "Z风险": _join_short(z_item.get("quality_risks")),
            "推荐模型数": decision.get("recommended_count"),
            "完成模型数": decision.get("completed_count"),
            "模型总数": decision.get("total_models"),
            "缺失模型": ", ".join(decision.get("missing_models") or []),
        }
        scores = decision.get("scores_by_model") or {}
        verdicts = decision.get("verdicts_by_model") or {}
        recommended = decision.get("recommended_by_model") or {}
        for model in models:
            score = scores.get(model)
            row[f"{model} 分"] = float(score) if score is not None else None
            row[f"{model} 结论"] = verdicts.get(model, "")
            row[f"{model} 推荐"] = "是" if recommended.get(model) else "否"
        rows.append(row)
    return rows


def z_quality_table_rows(z_decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in z_decisions:
        score = item.get("z_quality_score")
        rows.append(
            {
                "排名": item.get("rank"),
                "代码": item.get("code"),
                "策略": item.get("strategy"),
                "Z裁决": z_quality_label(str(item.get("z_quality_verdict") or "")),
                "Z裁决代码": item.get("z_quality_verdict") or "",
                "Z分": float(score) if score not in {"", None} else None,
                "共识分组": DECISION_BUCKET_LABELS.get(str(item.get("decision_bucket")), item.get("decision_bucket")),
                "共识结论": item.get("consensus_verdict") or "",
                "共识分": item.get("consensus_score"),
                "推荐模型数": item.get("recommended_count"),
                "硬否决": "、".join(str(value) for value in (item.get("hard_vetoes") or [])),
                "观察限制": "、".join(str(value) for value in (item.get("watch_caps") or [])),
                "理由": _join_short(item.get("quality_reasons")),
                "风险": _join_short(item.get("quality_risks")),
            }
        )
    return rows


def consensus_detail_table_rows(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in details:
        score = item.get("total_score")
        rows.append(
            {
                "代码": item.get("code"),
                "策略": item.get("strategy"),
                "模型": item.get("model_key"),
                "状态": "已复评" if item.get("status") == "reviewed" else "缺失",
                "总分": float(score) if score not in {"", None} else None,
                "结论": item.get("verdict") or "",
                "推荐": "是" if item.get("recommended") else "否",
                "评论": item.get("comment") or "",
            }
        )
    return rows


def consensus_model_state(decision: dict[str, Any], model: str) -> str:
    recommended = decision.get("recommended_by_model") or {}
    verdicts = decision.get("verdicts_by_model") or {}
    missing_models = set(decision.get("missing_models") or [])
    verdict = str(verdicts.get(model) or "").upper()
    if model in missing_models or not verdict:
        return "缺失"
    if bool(recommended.get(model)) and verdict == "PASS":
        return "推荐"
    if verdict == "WATCH":
        return "观察"
    return "不推荐"


def consensus_export_rows(
    decisions: list[dict[str, Any]],
    models: list[str],
    z_by_key: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    z_by_key = z_by_key or {}
    for decision in decisions:
        item_key = str(decision.get("review_key") or review_key(str(decision.get("code") or ""), str(decision.get("strategy") or "")))
        z_item = z_by_key.get(item_key, {})
        model_states = {model: consensus_model_state(decision, model) for model in models}
        scores = decision.get("scores_by_model") or {}
        score_values = [
            float(scores.get(model))
            for model in models
            if scores.get(model) not in {"", None}
        ]
        pass_count = sum(1 for state in model_states.values() if state == "推荐")
        watch_count = sum(1 for state in model_states.values() if state == "观察")
        fail_count = sum(1 for state in model_states.values() if state == "不推荐")
        missing_count = sum(1 for state in model_states.values() if state == "缺失")
        consensus_score = decision.get("consensus_score")
        z_score = z_item.get("z_quality_score")
        z_hard_vetoes = [str(item) for item in (z_item.get("hard_vetoes") or []) if str(item)]
        z_watch_caps = [str(item) for item in (z_item.get("watch_caps") or []) if str(item)]
        rows.append(
            {
                "code": str(decision.get("code") or ""),
                "strategy": str(decision.get("strategy") or ""),
                "rank": decision.get("rank"),
                "review_key": item_key,
                "decision_bucket": str(decision.get("decision_bucket") or ""),
                "decision_bucket_label": DECISION_BUCKET_LABELS.get(
                    str(decision.get("decision_bucket") or ""),
                    decision.get("decision_bucket"),
                ),
                "consensus_verdict": str(decision.get("consensus_verdict") or ""),
                "consensus_score": float(consensus_score) if consensus_score not in {"", None} else None,
                "agreement_score": float(decision.get("agreement_score") or 0),
                "pass_count": pass_count,
                "watch_count": watch_count,
                "fail_count": fail_count,
                "missing_count": missing_count,
                "completed_count": int(decision.get("completed_count") or 0),
                "model_count": int(decision.get("total_models") or len(models)),
                "score_spread": round(max(score_values) - min(score_values), 3) if len(score_values) >= 2 else 0.0,
                "model_states": model_states,
                "z_quality_verdict": str(z_item.get("z_quality_verdict") or ""),
                "z_quality_label": z_quality_label(str(z_item.get("z_quality_verdict") or "")),
                "z_quality_score": float(z_score) if z_score not in {"", None} else None,
                "z_hard_vetoes": z_hard_vetoes,
                "z_watch_caps": z_watch_caps,
                "z_quality_reasons": z_item.get("quality_reasons") or [],
                "z_quality_risks": z_item.get("quality_risks") or [],
            }
        )
    return rows


def apply_consensus_tdx_preset(rows: list[dict[str, Any]], preset: str) -> list[dict[str, Any]]:
    if preset == "共同推荐":
        return [row for row in rows if int(row["pass_count"]) == int(row["model_count"])]
    if preset == "多模型推荐":
        return [row for row in rows if int(row["pass_count"]) >= max(1, int(row["model_count"]) // 2 + 1)]
    if preset == "单模型推荐":
        return [row for row in rows if int(row["pass_count"]) == 1]
    if preset == "共同观察":
        return [
            row
            for row in rows
            if int(row["pass_count"]) == 0 and int(row["watch_count"]) == int(row["model_count"])
        ]
    if preset == "多模型观察":
        return [row for row in rows if int(row["pass_count"]) == 0 and int(row["watch_count"]) >= 2]
    if preset == "单模型观察":
        return [row for row in rows if int(row["pass_count"]) == 0 and int(row["watch_count"]) == 1]
    if preset == "Z精选":
        return [row for row in rows if row.get("z_quality_verdict") == "A_SELECT"]
    if preset == "Z观察":
        return [row for row in rows if row.get("z_quality_verdict") == "B_WATCH"]
    if preset == "Z精选+观察":
        return [row for row in rows if row.get("z_quality_verdict") in {"A_SELECT", "B_WATCH"}]
    if preset == "Z复盘样本":
        return [row for row in rows if row.get("z_quality_verdict") == "C_REVIEW_ONLY"]
    if preset == "分歧样本":
        return [row for row in rows if int(row["pass_count"]) >= 1 and int(row["fail_count"]) >= 1]
    return rows


def filter_consensus_tdx_rows(
    rows: list[dict[str, Any]],
    *,
    strategies: list[str],
    verdicts: list[str],
    bucket_labels: list[str],
    selected_models: list[str],
    selected_model_states: list[str],
    model_match: str,
    pass_range: tuple[int, int],
    watch_range: tuple[int, int],
    fail_range: tuple[int, int],
    score_range: tuple[float, float],
    z_verdicts: list[str],
    z_score_range: tuple[float, float],
    exclude_z_hard_veto: bool,
    exclude_z_watch_cap: bool,
    complete_only: bool,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if strategies and row["strategy"] not in strategies:
            continue
        if verdicts and row["consensus_verdict"] not in verdicts:
            continue
        if bucket_labels and row["decision_bucket_label"] not in bucket_labels:
            continue
        if complete_only and int(row["missing_count"]) > 0:
            continue
        if not (pass_range[0] <= int(row["pass_count"]) <= pass_range[1]):
            continue
        if not (watch_range[0] <= int(row["watch_count"]) <= watch_range[1]):
            continue
        if not (fail_range[0] <= int(row["fail_count"]) <= fail_range[1]):
            continue
        score = row["consensus_score"]
        if score is not None and not (score_range[0] <= float(score) <= score_range[1]):
            continue
        z_verdict = str(row.get("z_quality_verdict") or "")
        if z_verdicts and z_verdict not in z_verdicts:
            continue
        z_score = row.get("z_quality_score")
        if z_score is not None and not (z_score_range[0] <= float(z_score) <= z_score_range[1]):
            continue
        if exclude_z_hard_veto and row.get("z_hard_vetoes"):
            continue
        if exclude_z_watch_cap and row.get("z_watch_caps"):
            continue
        if selected_models and selected_model_states:
            states = [row["model_states"].get(model, "缺失") for model in selected_models]
            if model_match == "所有选中模型满足":
                if any(state not in selected_model_states for state in states):
                    continue
            elif not any(state in selected_model_states for state in states):
                continue
        filtered.append(row)
    return filtered


def sort_consensus_tdx_rows(rows: list[dict[str, Any]], sort_by: str, limit: int) -> list[dict[str, Any]]:
    sorters = {
        "共识分": lambda row: float(row["consensus_score"] or 0),
        "Z质量分": lambda row: float(row.get("z_quality_score") or 0),
        "推荐模型数": lambda row: int(row["pass_count"]),
        "观察模型数": lambda row: int(row["watch_count"]),
        "最高分歧": lambda row: float(row["score_spread"] or 0),
        "原始排名": lambda row: -int(row["rank"] or 10**9),
    }
    key_fn = sorters.get(sort_by, sorters["共识分"])
    sorted_rows = sorted(rows, key=key_fn, reverse=True)
    return sorted_rows[:limit] if limit > 0 else sorted_rows


@st.dialog("导入通达信 - 共识结果")
def render_consensus_tdx_import_dialog(
    summary: dict[str, Any],
    decisions: list[dict[str, Any]],
    models: list[str],
    z_decisions: list[dict[str, Any]] | None = None,
) -> None:
    pick_date = str(summary.get("pick_date") or "")
    batch_id = str(summary.get("batch_id") or "")
    model_count = max(1, int(summary.get("model_count") or len(models) or 1))
    z_by_key = z_quality_by_key(z_decisions or [])
    all_rows = consensus_export_rows(decisions, models, z_by_key)

    st.caption(f"复评批次：**{batch_id}**；选股日期：**{pick_date}**")
    if not summary.get("complete"):
        st.warning("当前批次存在缺失模型结果，导入前建议先补跑缺失项。")

    preset = st.selectbox(
        "快捷方案",
        list(CONSENSUS_TDX_PRESETS.keys()),
        key=f"consensus_tdx_preset_{batch_id}",
    )
    preset_rows = apply_consensus_tdx_preset(all_rows, preset)

    f1, f2, f3 = st.columns(3)
    with f1:
        strategy_options = sorted({row["strategy"] for row in all_rows if row["strategy"]})
        strategies = st.multiselect(
            "策略",
            strategy_options,
            default=strategy_options,
            key=f"consensus_tdx_strategy_{batch_id}",
        )
    with f2:
        verdicts = st.multiselect(
            "共识结论",
            CONSENSUS_VERDICT_OPTIONS,
            default=CONSENSUS_VERDICT_OPTIONS,
            key=f"consensus_tdx_verdict_{batch_id}",
        )
    with f3:
        complete_only = st.toggle(
            "排除缺失模型",
            value=True,
            key=f"consensus_tdx_complete_{batch_id}",
        )

    bucket_options = sorted({row["decision_bucket_label"] for row in all_rows if row["decision_bucket_label"]})
    bucket_labels = st.multiselect(
        "决策分组",
        bucket_options,
        default=bucket_options,
        key=f"consensus_tdx_bucket_{batch_id}",
    )

    z_options = ["A_SELECT", "B_WATCH", "C_REVIEW_ONLY", "REJECT"]
    present_z_options = [item for item in z_options if any(row.get("z_quality_verdict") == item for row in all_rows)]
    z1, z2, z3 = st.columns(3)
    with z1:
        z_verdicts = st.multiselect(
            "Z裁决",
            present_z_options,
            default=present_z_options,
            format_func=z_quality_label,
            key=f"consensus_tdx_z_verdict_{batch_id}",
            disabled=not present_z_options,
        )
    with z2:
        z_score_range = st.slider(
            "Z质量分",
            0.0,
            5.0,
            (0.0, 5.0),
            step=0.05,
            key=f"consensus_tdx_z_score_range_{batch_id}",
            disabled=not present_z_options,
        )
    with z3:
        exclude_z_hard_veto = st.toggle(
            "排除Z硬否决",
            value=False,
            key=f"consensus_tdx_z_hard_veto_{batch_id}",
            disabled=not present_z_options,
        )
        exclude_z_watch_cap = st.toggle(
            "排除Z观察限制",
            value=False,
            key=f"consensus_tdx_z_watch_cap_{batch_id}",
            disabled=not present_z_options,
        )

    m1, m2, m3 = st.columns(3)
    with m1:
        selected_models = st.multiselect(
            "指定模型",
            models,
            default=models,
            key=f"consensus_tdx_models_{batch_id}",
        )
    with m2:
        selected_model_states = st.multiselect(
            "模型状态",
            MODEL_STATE_OPTIONS,
            default=MODEL_STATE_OPTIONS,
            key=f"consensus_tdx_model_states_{batch_id}",
        )
    with m3:
        model_match = st.radio(
            "模型条件",
            ["任一选中模型满足", "所有选中模型满足"],
            horizontal=True,
            key=f"consensus_tdx_model_match_{batch_id}",
        )

    c1, c2, c3 = st.columns(3)
    with c1:
        pass_range = st.slider(
            "推荐模型数",
            0,
            model_count,
            (0, model_count),
            key=f"consensus_tdx_pass_range_{batch_id}",
        )
    with c2:
        watch_range = st.slider(
            "观察模型数",
            0,
            model_count,
            (0, model_count),
            key=f"consensus_tdx_watch_range_{batch_id}",
        )
    with c3:
        fail_range = st.slider(
            "不推荐模型数",
            0,
            model_count,
            (0, model_count),
            key=f"consensus_tdx_fail_range_{batch_id}",
        )

    s1, s2, s3 = st.columns(3)
    with s1:
        score_range = st.slider(
            "共识分",
            0.0,
            5.0,
            (0.0, 5.0),
            step=0.05,
            key=f"consensus_tdx_score_range_{batch_id}",
        )
    with s2:
        sort_by = st.selectbox(
            "排序",
            ["共识分", "Z质量分", "推荐模型数", "观察模型数", "最高分歧", "原始排名"],
            key=f"consensus_tdx_sort_{batch_id}",
        )
    with s3:
        limit = st.number_input(
            "最多导入",
            min_value=0,
            max_value=max(1, len(all_rows)),
            value=0,
            step=1,
            help="0 表示不限制",
            key=f"consensus_tdx_limit_{batch_id}",
        )

    filtered_rows = filter_consensus_tdx_rows(
        preset_rows,
        strategies=strategies,
        verdicts=verdicts,
        bucket_labels=bucket_labels,
        selected_models=selected_models,
        selected_model_states=selected_model_states,
        model_match=model_match,
        pass_range=pass_range,
        watch_range=watch_range,
        fail_range=fail_range,
        score_range=score_range,
        z_verdicts=z_verdicts,
        z_score_range=z_score_range,
        exclude_z_hard_veto=exclude_z_hard_veto,
        exclude_z_watch_cap=exclude_z_watch_cap,
        complete_only=complete_only,
    )
    filtered_rows = sort_consensus_tdx_rows(filtered_rows, sort_by, int(limit))

    st.caption(f"当前筛选结果：{len(filtered_rows)} / {len(all_rows)} 条")
    if filtered_rows:
        preview = pd.DataFrame(
            [
                {
                    "代码": row["code"],
                    "策略": row["strategy"],
                    "共识结论": row["consensus_verdict"],
                    "共识分": row["consensus_score"],
                    "Z裁决": row["z_quality_label"],
                    "Z分": row["z_quality_score"],
                    "推荐": row["pass_count"],
                    "观察": row["watch_count"],
                    "不推荐": row["fail_count"],
                    "分歧": row["score_spread"],
                }
                for row in filtered_rows[:80]
            ]
        )
        st.dataframe(preview, width="stretch", hide_index=True)

    if not filtered_rows:
        st.warning("当前筛选条件没有可导入股票")
        return

    block_items = [
        {
            "code": row["code"],
            "strategy": row["strategy"],
            "score": row["z_quality_score"] if sort_by == "Z质量分" or preset.startswith("Z") else row["consensus_score"],
            "recommended": int(row["pass_count"]) > 0,
            "rank": row["rank"],
        }
        for row in filtered_rows
    ]
    prefix = CONSENSUS_TDX_PRESETS.get(preset, CONSENSUS_TDX_PRESETS["全部自定义"])["prefix"]
    blocks = tdx_export.build_blocks_from_items(pick_date, block_items, name_prefix=prefix)
    if not blocks:
        st.warning("筛选结果没有可转换为通达信代码的股票")
        return

    render_tdx_blocks_preview(blocks)
    render_tdx_import_tabs(
        blocks,
        pick_date,
        f"共识结果-{preset}",
        key_suffix=f"consensus_{batch_id}_{prefix}",
    )


def render_consensus_center() -> None:
    st.title("共识结果")
    summary_files = consensus_summary_files()
    if not summary_files:
        st.warning("还没有多模型共识结果。请先在运行中心选择「多模型复评」并执行。")
        return

    labels = [consensus_label(path) for path in summary_files]
    selected_label = st.selectbox("复评批次", labels)
    summary_path = summary_files[labels.index(selected_label)]
    summary, decisions, details = load_consensus_payload(summary_path)
    models = [str(model) for model in summary.get("models", [])]
    z_decisions = load_z_quality_decisions(summary)
    z_by_key = z_quality_by_key(z_decisions)

    bucket_counts = summary.get("decision_bucket_counts") or {}
    z_quality = summary.get("z_quality") or {}
    z_counts = z_quality.get("verdict_counts") or {}
    st.markdown(
        f"""
        <div class="metric-row">
          <div class="metric-card"><div class="metric-label">选股日期</div><div class="metric-value">{summary.get('pick_date', '')}</div></div>
          <div class="metric-card"><div class="metric-label">候选数量</div><div class="metric-value">{summary.get('candidate_count', 0)}</div></div>
          <div class="metric-card"><div class="metric-label">参与模型</div><div class="metric-value">{summary.get('model_count', 0)}</div></div>
          <div class="metric-card"><div class="metric-label">全票推荐 / 不完整</div><div class="metric-value">{bucket_counts.get('all_models_recommended', 0)} / {bucket_counts.get('incomplete', 0)}</div></div>
          <div class="metric-card"><div class="metric-label">Z精选 / Z观察</div><div class="metric-value">{z_counts.get('A_SELECT', 0)} / {z_counts.get('B_WATCH', 0)}</div></div>
          <div class="metric-card"><div class="metric-label">Z复盘 / 剔除</div><div class="metric-value">{z_counts.get('C_REVIEW_ONLY', 0)} / {z_counts.get('REJECT', 0)}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if summary.get("invariant_violations"):
        st.warning("存在 score>=阈值 但 verdict!=PASS 的历史结果，请查看 summary.json 的 invariant_violations。")
    if not summary.get("complete"):
        st.warning("当前批次存在模型缺失评分，不能作为最终全票推荐结果；可断点重跑多模型复评。")
    if not z_decisions:
        st.info("当前批次还没有 Z 质量裁决结果；可在配置中启用 z_quality 后重跑共识后处理。")

    with st.columns([0.7, 0.3])[1]:
        if st.button(
            "📊 导入通达信",
            width="stretch",
            disabled=not decisions,
            help="按共识结果筛选股票并导入通达信自定义板块",
            key=f"consensus_tdx_import_open_{summary.get('batch_id')}",
        ):
            render_consensus_tdx_import_dialog(summary, decisions, models, z_decisions)

    tab_decision, tab_z_quality, tab_detail = st.tabs(["决策结果集", "Z质量裁决", "模型评分明细"])
    with tab_decision:
        decision_df = pd.DataFrame(consensus_decision_table_rows(decisions, models, z_by_key))
        if decision_df.empty:
            st.info("没有决策结果。")
        else:
            f1, f2, f3 = st.columns(3)
            with f1:
                strategies = sorted([x for x in decision_df["策略"].dropna().unique() if x])
                strategy = st.selectbox("策略筛选", ["全部"] + strategies, key=f"consensus_strategy_{summary.get('batch_id')}")
            with f2:
                bucket_labels = ["全部"] + [DECISION_BUCKET_LABELS.get(key, key) for key in sorted(bucket_counts)]
                bucket_label = st.selectbox("决策分组", bucket_labels, key=f"consensus_bucket_{summary.get('batch_id')}")
            with f3:
                z_labels = ["全部"] + [label for label in [z_quality_label(key) for key in ("A_SELECT", "B_WATCH", "C_REVIEW_ONLY", "REJECT")] if label in set(decision_df["Z裁决"].dropna())]
                z_label = st.selectbox("Z裁决", z_labels, key=f"consensus_z_verdict_{summary.get('batch_id')}")
            filtered = decision_df
            if strategy != "全部":
                filtered = filtered[filtered["策略"] == strategy]
            if bucket_label != "全部":
                filtered = filtered[filtered["决策分组"] == bucket_label]
            if z_label != "全部":
                filtered = filtered[filtered["Z裁决"] == z_label]
            st.caption(f"当前筛选结果：{len(filtered)} 条")
            st.dataframe(filtered.reset_index(drop=True), width="stretch", hide_index=True)

    with tab_z_quality:
        z_df = pd.DataFrame(z_quality_table_rows(z_decisions))
        if z_df.empty:
            st.info("没有 Z 质量裁决结果。")
        else:
            f1, f2, f3 = st.columns(3)
            with f1:
                strategies = sorted([x for x in z_df["策略"].dropna().unique() if x])
                strategy = st.selectbox("策略筛选", ["全部"] + strategies, key=f"z_quality_strategy_{summary.get('batch_id')}")
            with f2:
                z_labels = ["全部"] + [label for label in [z_quality_label(key) for key in ("A_SELECT", "B_WATCH", "C_REVIEW_ONLY", "REJECT")] if label in set(z_df["Z裁决"].dropna())]
                z_label = st.selectbox("Z裁决", z_labels, key=f"z_quality_verdict_{summary.get('batch_id')}")
            with f3:
                exclude_hard_veto = st.toggle("排除硬否决", value=False, key=f"z_quality_exclude_hard_veto_{summary.get('batch_id')}")
            filtered = z_df
            if strategy != "全部":
                filtered = filtered[filtered["策略"] == strategy]
            if z_label != "全部":
                filtered = filtered[filtered["Z裁决"] == z_label]
            if exclude_hard_veto:
                filtered = filtered[filtered["硬否决"] == ""]
            filtered = filtered.sort_values(["Z分", "排名"], ascending=[False, True], na_position="last")
            st.caption(f"当前筛选结果：{len(filtered)} 条；result_mode={z_quality.get('result_mode') or '-'}")
            st.dataframe(filtered.reset_index(drop=True), width="stretch", hide_index=True)

    with tab_detail:
        detail_df = pd.DataFrame(consensus_detail_table_rows(details))
        if detail_df.empty:
            st.info("没有模型明细。")
        else:
            f1, f2, f3 = st.columns(3)
            with f1:
                strategies = sorted([x for x in detail_df["策略"].dropna().unique() if x])
                strategy = st.selectbox("策略筛选", ["全部"] + strategies, key=f"consensus_detail_strategy_{summary.get('batch_id')}")
            with f2:
                model = st.selectbox("模型筛选", ["全部"] + models, key=f"consensus_detail_model_{summary.get('batch_id')}")
            with f3:
                recommendation = st.selectbox("推荐状态", ["全部", "仅推荐", "未推荐", "缺失"], key=f"consensus_detail_rec_{summary.get('batch_id')}")
            filtered = detail_df
            if strategy != "全部":
                filtered = filtered[filtered["策略"] == strategy]
            if model != "全部":
                filtered = filtered[filtered["模型"] == model]
            if recommendation == "仅推荐":
                filtered = filtered[filtered["推荐"] == "是"]
            elif recommendation == "未推荐":
                filtered = filtered[(filtered["推荐"] == "否") & (filtered["状态"] == "已复评")]
            elif recommendation == "缺失":
                filtered = filtered[filtered["状态"] == "缺失"]
            st.caption(f"当前筛选结果：{len(filtered)} 条")
            st.dataframe(filtered.reset_index(drop=True), width="stretch", hide_index=True)


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
        has_review = bool(review)
        status = str(row.get("status") or "")
        status_label = {"recommended": "推荐", "reviewed": "已复评", "unreviewed": "未复评"}.get(status, status)
        table.append(
            {
                "排名": row.get("rank"),
                "代码": row.get("code") or "",
                "策略": row.get("strategy") or "",
                "状态": status_label,
                "收盘价": float(close) if close is not None else None,
                "brick_growth": float(brick_growth) if brick_growth is not None else None,
                "结论": review.get("verdict") or ("未复评" if not has_review else ""),
                "总分": float(total_score) if total_score is not None else None,
                "信号": review.get("signal_type") or "",
                "经典图形": review.get("classic_pattern_type") or "",
                "经典匹配分": classic_pattern_match_score(review),
                "评论": review.get("comment") or ("暂无复评结果" if not has_review else ""),
            }
        )
    return table


def render_history_metrics(summary: dict[str, Any]) -> None:
    strategy_counts = summary.get("strategy_counts", {})
    b1_rec_count = strategy_counts.get("b1", {}).get("recommended", 0)
    b2_rec_count = strategy_counts.get("b2", {}).get("recommended", 0)
    brick_rec_count = strategy_counts.get("brick", {}).get("recommended", 0)
    st.markdown(
        f"""
        <div class="metric-row">
          <div class="metric-card"><div class="metric-label">归档日期</div><div class="metric-value">{summary.get("date", "无")}</div></div>
          <div class="metric-card"><div class="metric-label">候选 / 已复评</div><div class="metric-value">{summary.get("candidate_count", 0)} / {summary.get("reviewed_count", 0)}</div></div>
          <div class="metric-card"><div class="metric-label">推荐数量</div><div class="metric-value">{summary.get("recommended_count", 0)}</div></div>
          <div class="metric-card"><div class="metric-label">B1 / B2 / 砖型图推荐</div><div class="metric-value">{b1_rec_count} / {b2_rec_count} / {brick_rec_count}</div></div>
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

    options = [
        f"{row.get('code')} · {row.get('strategy') or 'unknown'}"
        for row in rows
        if row.get("code")
    ]
    selected = st.selectbox("查看单票详情", options)
    selected_row = next(
        (
            row
            for row in rows
            if f"{row.get('code')} · {row.get('strategy') or 'unknown'}" == selected
        ),
        {},
    )
    if not selected_row:
        return
    selected_code = str(selected_row.get("code") or "")

    left, right = st.columns([0.46, 0.54], gap="large")
    review = selected_row.get("review") or {}
    with left:
        st.subheader(f"{selected_code} · {selected_row.get('strategy', '')}")
        c1, c2, c3, c4, c5 = st.columns(5)
        match_score = classic_pattern_match_score(review)
        c1.metric("状态", selected_row.get("status", ""))
        c2.metric("总分", review.get("total_score", ""))
        c3.metric("经典图形", review.get("classic_pattern_type", "") or "-")
        c4.metric("匹配分", match_score if match_score is not None else "-")
        c5.metric("排名", selected_row.get("rank") or "-")
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


def load_candidates_for_date(pick_date: str) -> dict[str, Any]:
    dated = load_json(ROOT / "data" / "candidates" / f"candidates_{pick_date}.json")
    if dated:
        return dated
    latest = load_candidates()
    if pick_date == str(latest.get("pick_date") or ""):
        return latest
    return {}


def stock_row_score(row: dict[str, Any]) -> float | None:
    review = row.get("review") or {}
    value = review.get("total_score")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def stock_row_status(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    if status:
        return status
    return "reviewed" if row.get("review") else "unreviewed"


def stock_row_status_label(row: dict[str, Any]) -> str:
    labels = {"recommended": "推荐", "reviewed": "已复评未推荐", "unreviewed": "未复评"}
    return labels.get(stock_row_status(row), stock_row_status(row) or "未知")


def stock_view_rows_for_date(pick_date: str, review_source: str = FORMAL_REVIEW_SOURCE) -> list[dict[str, Any]]:
    payload = load_history_results(pick_date, "all") if review_source == FORMAL_REVIEW_SOURCE else {}
    if payload and review_source == FORMAL_REVIEW_SOURCE:
        return [dict(row) for row in payload.get("results", []) if row.get("code")]

    candidates_data = load_candidates_for_date(pick_date)
    candidates = candidates_data.get("candidates", [])
    if not candidates:
        return []

    suggestion = load_review_suggestion(pick_date, review_source)
    recommendations = {
        str(item.get("review_key") or review_key(str(item.get("code") or ""), str(item.get("strategy") or ""))): item
        for item in suggestion.get("recommendations", [])
    }
    review_dir = review_dir_for_date(pick_date, review_source)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        code = str(candidate.get("code") or "")
        strategy = str(candidate.get("strategy") or "")
        if not code:
            continue
        item_key = review_key(code, strategy)
        review = load_review_result(review_dir, code, strategy)
        recommendation = recommendations.get(item_key)
        row = dict(candidate)
        row.update(
            {
                "date": pick_date,
                "review_key": item_key,
                "review": review,
                "rank": recommendation.get("rank") if recommendation else None,
                "status": "recommended" if recommendation else ("reviewed" if review else "unreviewed"),
                "review_source": review_source,
                "chart": str(ROOT / "data" / "kline" / pick_date / f"{code}_day.jpg"),
            }
        )
        rows.append(row)
    return rows


def filter_stock_view_rows(
    rows: list[dict[str, Any]],
    strategy: str,
    recommendation_filter: str,
    score_range: tuple[float, float],
    include_unscored: bool,
) -> list[dict[str, Any]]:
    min_score, max_score = score_range
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if strategy != "全部" and str(row.get("strategy") or "") != strategy:
            continue

        status = stock_row_status(row)
        if recommendation_filter == "仅推荐" and status != "recommended":
            continue
        if recommendation_filter == "未推荐" and status == "recommended":
            continue
        if recommendation_filter == "已复评未推荐" and status != "reviewed":
            continue
        if recommendation_filter == "未复评" and status != "unreviewed":
            continue

        score = stock_row_score(row)
        if score is None:
            if recommendation_filter != "未复评" and not include_unscored:
                continue
        elif score < min_score or score > max_score:
            continue
        filtered.append(row)
    return filtered


def stock_view_table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for row in rows:
        rank = row.get("rank")
        table.append(
            {
                "排名": str(rank) if rank else "",
                "代码": row.get("code") or "",
                "策略": row.get("strategy") or "",
                "状态": stock_row_status_label(row),
                "总分": stock_row_score(row),
                "结论": (row.get("review") or {}).get("verdict") or "",
                "收盘价": row.get("close"),
            }
        )
    return table


def stock_view_option_label(row: dict[str, Any], index: int) -> str:
    score = stock_row_score(row)
    score_text = f"{score:.1f}" if score is not None else "未评分"
    rank = row.get("rank")
    rank_text = f" · #{rank}" if rank else ""
    return (
        f"{index + 1}. {row.get('code')} · {row.get('strategy') or 'unknown'} · "
        f"{stock_row_status_label(row)} · {score_text}{rank_text}"
    )


def dataframe_selected_rows(state: Any) -> list[int]:
    selection = getattr(state, "selection", None)
    if selection is None and isinstance(state, dict):
        selection = state.get("selection")
    rows = getattr(selection, "rows", None)
    if rows is None and isinstance(selection, dict):
        rows = selection.get("rows")
    if not rows:
        return []
    selected: list[int] = []
    for row in rows:
        try:
            selected.append(int(row))
        except (TypeError, ValueError):
            continue
    return selected


def stock_view_selected_index(state: Any, row_count: int) -> int:
    selected_rows = dataframe_selected_rows(state)
    if selected_rows and 0 <= selected_rows[0] < row_count:
        return selected_rows[0]
    return 0


def render_stock_view() -> None:
    st.title("单票复盘")
    dates = result_center_dates()
    if not dates:
        st.warning("还没有候选股票。")
        return

    f1, f2, f3, f4 = st.columns([0.22, 0.22, 0.22, 0.22])
    with f1:
        pick_date = st.selectbox("选股日期", dates, key="stock_pick_date")
    with f2:
        review_source = render_review_source_selectbox(
            "复评结果源",
            pick_date,
            key=f"stock_review_source_{pick_date}",
        )

    all_rows = stock_view_rows_for_date(pick_date, review_source)
    if not all_rows:
        st.info("当前选股日期没有可复盘的候选股票。")
        return

    strategies = sorted({str(row.get("strategy") or "") for row in all_rows if row.get("strategy")})
    with f3:
        selected_strategy = st.selectbox("策略", ["全部"] + strategies, key=f"stock_strategy_{pick_date}_{review_source}")
    with f4:
        recommendation_filter = st.selectbox(
            "推荐状态",
            ["全部", "仅推荐", "未推荐", "已复评未推荐", "未复评"],
            key=f"stock_recommendation_{pick_date}_{review_source}",
        )

    s1, s2 = st.columns([0.55, 0.2])
    with s1:
        score_range = st.slider(
            "评分范围",
            min_value=0.0,
            max_value=5.0,
            value=(0.0, 5.0),
            step=0.1,
            key=f"stock_score_range_{pick_date}_{review_source}",
        )
    with s2:
        include_unscored = st.checkbox(
            "包含未评分",
            value=True,
            key=f"stock_include_unscored_{pick_date}_{review_source}",
        )

    filtered_rows = filter_stock_view_rows(
        all_rows,
        selected_strategy,
        recommendation_filter,
        score_range,
        include_unscored,
    )
    st.caption(f"复评结果源：{review_source_label(review_source)}；当前筛选结果：{len(filtered_rows)} / {len(all_rows)} 条")
    if not filtered_rows:
        st.info("当前筛选条件下没有股票。")
        return

    table_state = st.dataframe(
        pd.DataFrame(stock_view_table_rows(filtered_rows)),
        width="stretch",
        hide_index=True,
        key=f"stock_table_{pick_date}_{review_source}_{selected_strategy}_{recommendation_filter}_{score_range}_{include_unscored}",
        on_select="rerun",
        selection_mode="single-row",
    )
    selected_index = stock_view_selected_index(table_state, len(filtered_rows))

    selected_row = filtered_rows[int(selected_index)]
    st.caption(f"当前复盘：{stock_view_option_label(selected_row, int(selected_index))}")
    selected_code = str(selected_row.get("code") or "")
    review = selected_row.get("review") or {}
    df = _load_raw(selected_code)

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("选股日期", pick_date)
    c2.metric("策略", selected_row.get("strategy", ""))
    c3.metric("状态", stock_row_status_label(selected_row))
    c4.metric("收盘价", selected_row.get("close", ""))
    c5.metric("复评结论", review.get("verdict", "未复评"))
    c6.metric("总分", review.get("total_score", ""))
    match_score = classic_pattern_match_score(review)
    c7.metric("匹配分", match_score if match_score is not None else "-")

    if df.empty:
        st.warning(f"未找到 data/raw/{selected_code}.csv，尝试显示归档图表。")
        chart_path = Path(str(selected_row.get("chart") or ""))
        if chart_path.exists():
            st.image(str(chart_path), caption=chart_path.name, width="stretch")
        else:
            st.info("未找到归档关联图表。")
    else:
        st.plotly_chart(make_daily_chart(df, selected_code, bars=120, height=620), width="stretch", config={"scrollZoom": True})
        st.plotly_chart(make_weekly_chart(df, selected_code, height=460), width="stretch", config={"scrollZoom": True})

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
    complete = complete_history_result(target_date, cfg)
    required = " + ".join(required_signal_strategies(cfg))
    c1, c2 = st.columns([0.35, 0.65], gap="large")
    with c1:
        st.subheader("今日模拟流程")
        st.caption(f"目标信号日：{target_date}；必需策略：{required}；当日完整归档：{'已存在' if complete else '未发现'}")
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
            ["运行中心", "数据配置", "策略配置", "复评配置", "结果中心", "共识结果", "历史结果", "单票复盘", "模拟交易"],
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
    elif page == "共识结果":
        render_consensus_center()
    elif page == "历史结果":
        render_history_center()
    elif page == "模拟交易":
        render_paper_trading()
    else:
        render_stock_view()


if __name__ == "__main__":
    main()
