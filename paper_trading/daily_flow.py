from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from paper_trading.core import (
    ROOT,
    complete_history_result,
    generate_plan,
    is_trading_day,
    latest_complete_signal_date,
    load_json,
    load_yaml,
    next_business_day,
    plan_path,
    required_signal_strategies,
    save_snapshot,
    today_iso,
    trading_config,
)


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def resolved_env() -> dict[str, str]:
    env = os.environ.copy()
    if env.get("TS_TOKEN") and not env.get("TUSHARE_TOKEN"):
        env["TUSHARE_TOKEN"] = env["TS_TOKEN"]
    env.setdefault("NO_COLOR", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def run_command(name: str, cmd: list[str]) -> None:
    print(f"\n[Step] {name}", flush=True)
    print(f"[Command] {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=resolved_env(),
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{name} 失败，退出码 {code}")
    print(f"\n[OK] {name} 完成", flush=True)


def strategy_config(base: dict[str, Any], strategy: str) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base))
    cfg.setdefault("b1", {})
    cfg.setdefault("b2", {})
    cfg.setdefault("brick", {})
    for name in ("b1", "b2", "brick"):
        cfg[name]["enabled"] = strategy == name
    return cfg


def strategy_label(strategy: str) -> str:
    labels = {"b1": "B1", "b2": "B2", "brick": "砖型图"}
    return labels.get(strategy, strategy)


def strategy_labels(strategies: list[str]) -> str:
    return " + ".join(strategy_label(strategy) for strategy in strategies)


def review_step(run_dir: Path, reviewer: str) -> tuple[str, list[str]]:
    python = sys.executable
    if reviewer == "gemini-api":
        return "Gemini API 复评", [python, "agent/gemini_review.py", "--config", str(run_dir / "gemini_review.yaml")]
    return "Gemini CLI 复评", [python, "agent/gemini_cli_review.py", "--config", str(run_dir / "gemini_cli_review.yaml")]


def latest_candidate_pick_date() -> str:
    candidates = load_json(ROOT / "data" / "candidates" / "candidates_latest.json")
    return str(candidates.get("pick_date") or "")


def preselect_cmd(run_dir: Path, rules_path: Path, run_options: dict[str, Any], pick_date: str | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "pipeline.cli", "preselect", "--config", str(rules_path), "--merge-same-date"]
    date_text = pick_date or str(run_options.get("pick_date") or "").strip()
    if date_text:
        cmd += ["--date", date_text]
    end_date = str(run_options.get("end_date") or "").strip()
    if end_date:
        cmd += ["--end-date", end_date]
    log_dir = str(run_options.get("preselect_log_dir") or "./data/logs").strip()
    if log_dir:
        cmd += ["--log-dir", log_dir]
    return cmd


def parse_time(value: Any, default: dt.time) -> dt.time:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        hour, minute = text.split(":", 1)
        return dt.time(hour=int(hour), minute=int(minute[:2]))
    except (TypeError, ValueError):
        return default


def is_after_signal_refresh_time(cfg: dict[str, Any]) -> bool:
    refresh_after = parse_time(cfg.get("signal_refresh_after_time"), dt.time(hour=16, minute=0))
    return dt.datetime.now().time() >= refresh_after


def flow_signal_decision(requested_signal_date: str, cfg: dict[str, Any]) -> dict[str, Any]:
    today = today_iso()
    if not is_trading_day(today, cfg):
        signal_date = latest_complete_signal_date(today, cfg=cfg)
        return {
            "mode": "non_trading_day",
            "signal_date": signal_date,
            "allow_refresh": False,
            "message": f"今日 {today} 非交易日，沿用最近完整信号日 {signal_date or '无'}。",
        }

    if requested_signal_date != today:
        return {
            "mode": "manual_signal_date",
            "signal_date": requested_signal_date,
            "allow_refresh": True,
            "message": f"使用手动指定信号日 {requested_signal_date}。",
        }

    if not is_after_signal_refresh_time(cfg):
        signal_date = latest_complete_signal_date(today, strict_before=True, cfg=cfg)
        return {
            "mode": "trading_day_before_close",
            "signal_date": signal_date,
            "allow_refresh": False,
            "message": (
                f"交易日 {today} 尚未到信号刷新时间 {cfg.get('signal_refresh_after_time', '16:00')}，"
                f"使用上一完整信号日 {signal_date or '无'}。"
            ),
        }

    return {
        "mode": "trading_day_after_close",
        "signal_date": today,
        "allow_refresh": True,
        "message": f"交易日 {today} 已到信号刷新时间，使用今日收盘数据生成下一交易日计划。",
    }


def run_stock_selection_if_needed(
    run_dir: Path,
    signal_date: str,
    cfg: dict[str, Any],
    run_options: dict[str, Any],
    *,
    allow_refresh: bool,
) -> str:
    required_strategies = required_signal_strategies(cfg) or ["b1", "brick"]
    required_label = strategy_labels(required_strategies)
    if not signal_date:
        print("[INFO] 没有可用信号日，跳过选股流程。", flush=True)
        return ""
    if complete_history_result(signal_date, cfg):
        print(f"[INFO] {signal_date} 已存在 {required_label} 完整归档，跳过选股和复评。", flush=True)
        return signal_date

    if not allow_refresh:
        print(f"[INFO] 当前场景不刷新信号，且 {signal_date} 没有完整归档，跳过选股流程。", flush=True)
        return signal_date

    print(f"[INFO] {signal_date} 暂无 {required_label} 完整归档，开始执行信号刷新流程。", flush=True)
    base_rules = load_yaml(run_dir / "rules_preselect.yaml")
    rules_paths: dict[str, Path] = {}
    for strategy in required_strategies:
        rules_path = run_dir / f"rules_preselect_{strategy}.yaml"
        write_yaml(rules_path, strategy_config(base_rules, strategy))
        rules_paths[strategy] = rules_path

    run_command("拉取 K 线数据", [sys.executable, "-m", "pipeline.fetch_kline", "--config", str(run_dir / "fetch_kline.yaml")])
    actual_signal_date = signal_date
    completed_strategies: list[str] = []
    for index, strategy in enumerate(required_strategies):
        label = strategy_label(strategy)
        pick_arg = None if index == 0 else actual_signal_date
        run_command(f"{label} 量化初选", preselect_cmd(run_dir, rules_paths[strategy], run_options, pick_date=pick_arg))
        actual_signal_date = latest_candidate_pick_date() or actual_signal_date
        if index == 0 and actual_signal_date != signal_date and complete_history_result(actual_signal_date, cfg):
            print(
                f"[INFO] 请求信号日 {signal_date} 暂无可用数据，初选回退到 {actual_signal_date}；"
                "该日期已有完整归档，停止重复复评。",
                flush=True,
            )
            return actual_signal_date

        run_command(f"导出 {label} 候选图表", [sys.executable, "dashboard/export_kline_charts.py"])
        review_name, review_cmd = review_step(run_dir, str(run_options.get("reviewer") or "gemini-cli"))
        run_command(review_name, review_cmd)
        completed_strategies.append(strategy)
        run_command(
            f"归档 {strategy_labels(completed_strategies)} 结果",
            [sys.executable, "-m", "pipeline.archive_results", "--run-id", run_dir.name],
        )
    return actual_signal_date


def ensure_plan_for_signal(signal_date: str, cfg: dict[str, Any], *, label: str) -> None:
    if not signal_date:
        print(f"[INFO] 没有可用信号日，无法生成{label}。", flush=True)
        return
    if not complete_history_result(signal_date, cfg):
        print(f"[INFO] {signal_date} 不是完整信号日，无法生成{label}。", flush=True)
        return
    execution_date = next_business_day(signal_date, cfg)
    path = plan_path(cfg, execution_date)
    if path.exists():
        print(f"[INFO] {label}已存在: {path.name}，不重复生成。", flush=True)
        return
    plan = generate_plan(signal_date, cfg)
    print(
        f"[INFO] 已用 {signal_date} 信号生成{label}: plan_{execution_date}.json，"
        f"status={plan.get('status')} orders={len(plan.get('orders', []))}",
        flush=True,
    )


def run_daily_flow(run_dir: Path) -> None:
    run_options = load_json(run_dir / "run_options.json")
    cfg = trading_config(run_dir / "paper_trading.yaml")
    requested_signal_date = str(run_options.get("pick_date") or dt.date.today().isoformat())
    decision = flow_signal_decision(requested_signal_date, cfg)
    signal_date = str(decision.get("signal_date") or "")
    print(
        f"[System] 模拟交易流程启动，目标信号日: {requested_signal_date}",
        flush=True,
    )
    print(f"[INFO] {decision.get('message')}", flush=True)

    if decision.get("allow_refresh"):
        signal_date = run_stock_selection_if_needed(
            run_dir,
            signal_date,
            cfg,
            run_options,
            allow_refresh=True,
        )
    else:
        print("\n[Step] 生成当前可用交易计划", flush=True)
        ensure_plan_for_signal(signal_date, cfg, label="当前可用交易计划")

    print("\n[Step] 保存账户快照", flush=True)
    snapshot_date = signal_date or today_iso()
    snapshot = save_snapshot(snapshot_date, cfg)
    print(
        f"[INFO] 账户快照: cash={snapshot['cash']:.2f} market_value={snapshot['market_value']:.2f} "
        f"total_value={snapshot['total_value']:.2f} positions={snapshot['position_count']}",
        flush=True,
    )

    if decision.get("allow_refresh"):
        print("\n[Step] 生成下一交易日计划", flush=True)
        ensure_plan_for_signal(signal_date, cfg, label="下一交易日计划")

    print("\n[SUCCESS] 模拟交易流程执行完毕", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行本地模拟交易每日流程")
    parser.add_argument("--run-dir", required=True, help="Workbench 运行快照目录")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_daily_flow(Path(args.run_dir).resolve())


if __name__ == "__main__":
    main()
