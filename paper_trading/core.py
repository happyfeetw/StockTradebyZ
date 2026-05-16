from __future__ import annotations

import csv
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "paper_trading.yaml"
DEFAULT_TRADING_DIR = ROOT / "data" / "trading"


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def atomic_write_json(path: Path, data: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def resolve_path(path_text: str | None, default: Path) -> Path:
    if not path_text:
        return default
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def today_iso() -> str:
    return dt.date.today().isoformat()


def _compact_date(date_text: str) -> str:
    return date_text.replace("-", "")


def _iso_date(date_text: str) -> str:
    if "-" in date_text:
        return dt.date.fromisoformat(date_text).isoformat()
    return dt.datetime.strptime(date_text, "%Y%m%d").date().isoformat()


def calendar_path(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or trading_config()
    default = trading_dir(cfg) / "trading_calendar.json"
    return resolve_path(str(cfg.get("trade_calendar_path") or ""), default)


def _load_calendar_cache(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return load_json(calendar_path(cfg))


def _write_calendar_cache(cfg: dict[str, Any], dates: list[str], source: str) -> None:
    normalized = sorted({_iso_date(str(date)) for date in dates if date})
    if not normalized:
        return
    atomic_write_json(
        calendar_path(cfg),
        {
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "start_date": normalized[0],
            "end_date": normalized[-1],
            "dates": normalized,
        },
    )


def _trading_days_from_cache(cfg: dict[str, Any] | None = None) -> list[str]:
    data = _load_calendar_cache(cfg)
    return sorted({_iso_date(str(date)) for date in data.get("dates", []) if date})


def _derive_trading_days_from_raw(raw_dir: Path | None = None, *, max_files: int = 120) -> list[str]:
    raw_dir = raw_dir or (ROOT / "data" / "raw")
    if not raw_dir.exists():
        return []
    dates: set[str] = set()
    for index, path in enumerate(sorted(raw_dir.glob("*.csv"))):
        if index >= max_files:
            break
        try:
            df = pd.read_csv(path, usecols=["date"])
        except (OSError, ValueError):
            continue
        if df.empty:
            continue
        parsed = pd.to_datetime(df["date"], errors="coerce").dropna()
        dates.update(item.strftime("%Y-%m-%d") for item in parsed)
    return sorted(dates)


def _fetch_tushare_trade_calendar(start_date: str, end_date: str) -> list[str]:
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN")
    if not token:
        return []
    try:
        import tushare as ts  # type: ignore

        pro = ts.pro_api(token)
        df = pro.trade_cal(
            exchange="SSE",
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
            is_open="1",
        )
    except Exception:
        return []
    if df is None or df.empty or "cal_date" not in df:
        return []
    return sorted({_iso_date(str(value)) for value in df["cal_date"].dropna().tolist()})


def ensure_trading_calendar(
    cfg: dict[str, Any] | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    cfg = cfg or trading_config()
    cached = _trading_days_from_cache(cfg)
    if start_date and end_date and cached:
        if cached[0] <= start_date and cached[-1] >= end_date:
            return cached

    derived = _derive_trading_days_from_raw(max_files=int(cfg.get("trade_calendar_raw_sample_files", 120)))
    merged = sorted(set(cached) | set(derived))
    provider = str(cfg.get("trade_calendar_provider") or "tushare").lower()
    if provider == "tushare":
        start = start_date or (merged[0] if merged else "2019-01-01")
        end = end_date or (
            dt.date.today() + dt.timedelta(days=int(cfg.get("trade_calendar_lookahead_days", 120)))
        ).isoformat()
        fetched = _fetch_tushare_trade_calendar(start, end)
        if fetched:
            merged = sorted(set(merged) | set(fetched))
            _write_calendar_cache(cfg, merged, "tushare+local_raw")
            return merged

    if merged:
        _write_calendar_cache(cfg, merged, "local_raw")
    return merged


def is_trading_day(date_text: str, cfg: dict[str, Any] | None = None) -> bool:
    date_iso = _iso_date(date_text)
    days = ensure_trading_calendar(cfg, start_date=date_iso, end_date=date_iso)
    if days and days[0] <= date_iso <= days[-1]:
        return date_iso in set(days)
    return dt.date.fromisoformat(date_iso).weekday() < 5


def next_business_day(date_text: str, cfg: dict[str, Any] | None = None) -> str:
    base = dt.date.fromisoformat(_iso_date(date_text))
    lookahead = int((cfg or {}).get("trade_calendar_lookahead_days", 120)) if cfg else 120
    end = (base + dt.timedelta(days=lookahead)).isoformat()
    days = ensure_trading_calendar(cfg, start_date=base.isoformat(), end_date=end)
    for day in days:
        if day > base.isoformat():
            return day
    day = base
    day += dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day.isoformat()


def previous_business_day(date_text: str, cfg: dict[str, Any] | None = None) -> str:
    base = dt.date.fromisoformat(_iso_date(date_text))
    start = (base - dt.timedelta(days=365)).isoformat()
    days = ensure_trading_calendar(cfg, start_date=start, end_date=base.isoformat())
    previous = [day for day in days if day < base.isoformat()]
    if previous:
        return previous[-1]
    day = base
    day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day.isoformat()


def trading_config(path: Path | None = None) -> dict[str, Any]:
    cfg = load_yaml(path or DEFAULT_CONFIG_PATH)
    defaults = {
        "initial_cash": 20000,
        "max_positions": 5,
        "max_new_buys_per_day": 2,
        "target_position_weight": 0.2,
        "min_hold_days": 3,
        "max_hold_days": 20,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.18,
        "buy_slippage_pct": 0.001,
        "sell_slippage_pct": 0.001,
        "commission_rate": 0.0003,
        "min_commission": 5,
        "stamp_tax_rate": 0.0005,
        "suggest_min_score": 4.0,
        "auto_confirm_generated_plan": False,
        "auto_execute_confirmed_plan": False,
        "skip_today_signal_before_refresh_time": True,
        "signal_refresh_after_time": "16:00",
        "trade_calendar_provider": "tushare",
        "trade_calendar_path": "data/trading/trading_calendar.json",
        "trade_calendar_lookahead_days": 120,
        "trade_calendar_raw_sample_files": 120,
        "trading_dir": "data/trading",
    }
    return {**defaults, **cfg}


def plan_config_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "initial_cash",
        "max_positions",
        "max_new_buys_per_day",
        "target_position_weight",
        "min_hold_days",
        "max_hold_days",
        "stop_loss_pct",
        "take_profit_pct",
        "buy_slippage_pct",
        "sell_slippage_pct",
        "commission_rate",
        "min_commission",
        "stamp_tax_rate",
        "suggest_min_score",
        "auto_confirm_generated_plan",
        "auto_execute_confirmed_plan",
        "skip_today_signal_before_refresh_time",
        "signal_refresh_after_time",
        "trade_calendar_provider",
        "trade_calendar_path",
        "trade_calendar_lookahead_days",
        "trade_calendar_raw_sample_files",
    ]
    return {key: cfg.get(key) for key in keys}


def trading_dir(cfg: dict[str, Any]) -> Path:
    return resolve_path(str(cfg.get("trading_dir") or ""), DEFAULT_TRADING_DIR)


def ensure_layout(cfg: dict[str, Any]) -> Path:
    root = trading_dir(cfg)
    for child in ("plans", "orders", "fills", "snapshots", "logs"):
        (root / child).mkdir(parents=True, exist_ok=True)
    ensure_account(cfg)
    return root


def account_path(cfg: dict[str, Any]) -> Path:
    return trading_dir(cfg) / "account.json"


def positions_path(cfg: dict[str, Any]) -> Path:
    return trading_dir(cfg) / "positions.json"


def equity_curve_path(cfg: dict[str, Any]) -> Path:
    return trading_dir(cfg) / "equity_curve.csv"


def plan_path(cfg: dict[str, Any], execute_date: str) -> Path:
    return trading_dir(cfg) / "plans" / f"plan_{execute_date}.json"


def ensure_account(cfg: dict[str, Any]) -> dict[str, Any]:
    root = trading_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)
    account_file = account_path(cfg)
    positions_file = positions_path(cfg)
    if not account_file.exists():
        initial_cash = float(cfg.get("initial_cash", 20000))
        atomic_write_json(
            account_file,
            {
                "initial_cash": initial_cash,
                "cash": initial_cash,
                "realized_pnl": 0.0,
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            },
        )
    if not positions_file.exists():
        atomic_write_json(positions_file, [])
    return load_json(account_file)


def has_trade_records(cfg: dict[str, Any]) -> bool:
    root = trading_dir(cfg)
    positions = load_positions(cfg)
    if positions:
        return True
    for child in ("fills", "orders"):
        path = root / child
        if path.exists() and any(path.glob("*.jsonl")):
            return True
    plans_dir = root / "plans"
    if plans_dir.exists():
        for path in plans_dir.glob("plan_*.json"):
            plan = load_json(path)
            if plan.get("status") in {"executed", "skipped"} or plan.get("fills"):
                return True
    return False


def sync_initial_cash_if_pristine(cfg: dict[str, Any]) -> bool:
    """Apply initial_cash changes while the simulated account is still unused."""
    account = ensure_account(cfg)
    desired_cash = float(cfg.get("initial_cash", 20000))
    current_initial = float(account.get("initial_cash") or 0)
    if abs(current_initial - desired_cash) < 0.0001:
        return True
    if has_trade_records(cfg):
        return False
    account["initial_cash"] = desired_cash
    account["cash"] = desired_cash
    account["realized_pnl"] = 0.0
    save_account(cfg, account)
    return True


def load_positions(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    path = positions_path(cfg)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_account(cfg: dict[str, Any], account: dict[str, Any]) -> None:
    account["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    atomic_write_json(account_path(cfg), account)


def save_positions(cfg: dict[str, Any], positions: list[dict[str, Any]]) -> None:
    atomic_write_json(positions_path(cfg), positions)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_price_frame(code: str) -> pd.DataFrame:
    path = ROOT / "data" / "raw" / f"{code}.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df.columns = [str(col).lower() for col in df.columns]
    if "date" not in df:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("open", "close", "high", "low"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def price_on_or_before(code: str, date_text: str) -> dict[str, Any]:
    df = load_price_frame(code)
    if df.empty:
        return {}
    date_ts = pd.Timestamp(date_text)
    rows = df[df["date"] <= date_ts]
    if rows.empty:
        return {}
    row = rows.iloc[-1]
    return {
        "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
        "open": float(row["open"]) if "open" in row and pd.notna(row["open"]) else None,
        "close": float(row["close"]) if "close" in row and pd.notna(row["close"]) else None,
    }


def price_on_date(code: str, date_text: str) -> dict[str, Any]:
    df = load_price_frame(code)
    if df.empty:
        return {}
    date_ts = pd.Timestamp(date_text)
    rows = df[df["date"] == date_ts]
    if rows.empty:
        return {}
    row = rows.iloc[-1]
    return {
        "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
        "open": float(row["open"]) if "open" in row and pd.notna(row["open"]) else None,
        "close": float(row["close"]) if "close" in row and pd.notna(row["close"]) else None,
    }


def holding_days(entry_date: str, as_of_date: str) -> int:
    try:
        return max((dt.date.fromisoformat(as_of_date) - dt.date.fromisoformat(entry_date)).days, 0)
    except ValueError:
        return 0


def commission(amount: float, cfg: dict[str, Any]) -> float:
    return max(amount * float(cfg.get("commission_rate", 0.0003)), float(cfg.get("min_commission", 5)))


def stamp_tax(amount: float, cfg: dict[str, Any]) -> float:
    return amount * float(cfg.get("stamp_tax_rate", 0.0005))


def portfolio_value(cfg: dict[str, Any], as_of_date: str) -> dict[str, Any]:
    account = ensure_account(cfg)
    positions = load_positions(cfg)
    market_value = 0.0
    enriched: list[dict[str, Any]] = []
    for pos in positions:
        code = str(pos.get("code") or "")
        row = price_on_or_before(code, as_of_date)
        close = row.get("close") or pos.get("market_price") or pos.get("avg_cost") or 0
        quantity = int(pos.get("quantity") or 0)
        value = quantity * float(close)
        cost = quantity * float(pos.get("avg_cost") or 0)
        market_value += value
        enriched.append(
            {
                **pos,
                "market_price": float(close),
                "market_date": row.get("date", ""),
                "market_value": value,
                "unrealized_pnl": value - cost,
                "hold_days": holding_days(str(pos.get("entry_date") or as_of_date), as_of_date),
            }
        )
    return {
        "cash": float(account.get("cash") or 0),
        "market_value": market_value,
        "total_value": float(account.get("cash") or 0) + market_value,
        "positions": enriched,
        "realized_pnl": float(account.get("realized_pnl") or 0),
    }


def complete_history_result(signal_date: str) -> bool:
    summary = load_json(ROOT / "data" / "history" / signal_date / "summary.json")
    if not summary:
        return False
    counts = summary.get("strategy_counts") or {}
    if not all((counts.get(strategy) or {}).get("total", 0) > 0 for strategy in ("b1", "brick")):
        return False
    return int(summary.get("candidate_count") or 0) > 0 and int(summary.get("reviewed_count") or 0) >= int(
        summary.get("candidate_count") or 0
    )


def latest_complete_signal_date(on_or_before: str, *, strict_before: bool = False) -> str:
    history_dir = ROOT / "data" / "history"
    if not history_dir.exists():
        return ""
    cutoff = dt.date.fromisoformat(on_or_before)
    dates: list[str] = []
    for path in history_dir.iterdir():
        if not path.is_dir():
            continue
        try:
            current = dt.date.fromisoformat(path.name)
        except ValueError:
            continue
        if strict_before and current >= cutoff:
            continue
        if not strict_before and current > cutoff:
            continue
        if complete_history_result(path.name):
            dates.append(path.name)
    return max(dates) if dates else ""


def recommended_rows(signal_date: str, min_score: float) -> list[dict[str, Any]]:
    payload = load_json(ROOT / "data" / "history" / signal_date / "all.json")
    rows = payload.get("results", []) if payload else []
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row.get("code") or "")
        review = row.get("review") or {}
        score = review.get("total_score")
        if not code or code in seen:
            continue
        if row.get("status") != "recommended":
            continue
        if score is not None and float(score) < min_score:
            continue
        selected.append(row)
        seen.add(code)
    selected.sort(key=lambda item: item.get("rank") if item.get("rank") is not None else 999999)
    return selected


def order_quantity(target_amount: float, reference_price: float) -> int:
    if reference_price <= 0:
        return 0
    shares = int(target_amount // reference_price)
    return shares - shares % 100


def build_sell_orders(
    *,
    cfg: dict[str, Any],
    signal_date: str,
    positions: list[dict[str, Any]],
    recommended_codes: set[str],
) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    min_hold_days = int(cfg.get("min_hold_days", 3))
    max_hold_days = int(cfg.get("max_hold_days", 20))
    stop_loss_pct = float(cfg.get("stop_loss_pct", 0.08))
    take_profit_pct = float(cfg.get("take_profit_pct", 0.18))
    for pos in positions:
        code = str(pos.get("code") or "")
        quantity = int(pos.get("quantity") or 0)
        if not code or quantity <= 0:
            continue
        price = price_on_or_before(code, signal_date)
        close = float(price.get("close") or pos.get("market_price") or pos.get("avg_cost") or 0)
        avg_cost = float(pos.get("avg_cost") or 0)
        days = holding_days(str(pos.get("entry_date") or signal_date), signal_date)
        reasons: list[str] = []
        if avg_cost > 0 and close <= avg_cost * (1 - stop_loss_pct):
            reasons.append("触发止损")
        if avg_cost > 0 and close >= avg_cost * (1 + take_profit_pct):
            reasons.append("触发止盈")
        if days >= max_hold_days:
            reasons.append("达到最长持仓天数")
        if days >= min_hold_days and code not in recommended_codes:
            reasons.append("持仓满最短周期且不在最新推荐")
        if not reasons:
            continue
        orders.append(
            {
                "code": code,
                "strategy": pos.get("strategy") or "",
                "side": "SELL",
                "quantity": quantity,
                "reference_price": close,
                "estimated_amount": close * quantity,
                "reason": "; ".join(reasons),
                "status": "draft",
                "risk_checks": {"position_exists": True, "quantity_ok": True},
            }
        )
    return orders


def generate_plan(signal_date: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or trading_config()
    ensure_layout(cfg)
    min_score = float(cfg.get("suggest_min_score", 4.0))
    execute_date = next_business_day(signal_date, cfg)
    account = ensure_account(cfg)
    current_positions = load_positions(cfg)
    value = portfolio_value(cfg, signal_date)
    rows = recommended_rows(signal_date, min_score)
    recommended_codes = {str(row.get("code")) for row in rows if row.get("code")}
    sell_orders = build_sell_orders(
        cfg=cfg,
        signal_date=signal_date,
        positions=current_positions,
        recommended_codes=recommended_codes,
    )

    held_codes = {str(pos.get("code")) for pos in current_positions if pos.get("code")}
    slots_after_sells = int(cfg.get("max_positions", 5)) - (len(held_codes) - len(sell_orders))
    available_new_buys = max(0, min(int(cfg.get("max_new_buys_per_day", 2)), slots_after_sells))
    target_amount = value["total_value"] * float(cfg.get("target_position_weight", 0.2))
    reserved_cash = 0.0
    buy_orders: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in rows:
        if len(buy_orders) >= available_new_buys:
            break
        code = str(row.get("code") or "")
        if not code:
            continue
        if code in held_codes:
            skipped.append({"code": code, "reason": "already_held"})
            continue
        close = float(row.get("close") or 0)
        if close <= 0:
            skipped.append({"code": code, "reason": "missing_reference_price"})
            continue
        quantity = order_quantity(target_amount, close)
        estimated_amount = quantity * close
        estimated_fee = commission(estimated_amount, cfg)
        cash_ok = quantity >= 100 and float(account.get("cash") or 0) - reserved_cash >= estimated_amount + estimated_fee
        if not cash_ok:
            skipped.append({"code": code, "reason": "cash_not_enough_for_100_shares"})
            continue
        review = row.get("review") or {}
        buy_orders.append(
            {
                "code": code,
                "strategy": row.get("strategy") or review.get("strategy") or "",
                "side": "BUY",
                "quantity": quantity,
                "reference_price": close,
                "estimated_amount": estimated_amount,
                "target_weight": float(cfg.get("target_position_weight", 0.2)),
                "reason": f"rank={row.get('rank')}; score={review.get('total_score')}; {review.get('comment', '')}",
                "status": "draft",
                "risk_checks": {
                    "lot_size_ok": quantity % 100 == 0 and quantity >= 100,
                    "cash_ok": True,
                    "position_limit_ok": True,
                },
            }
        )
        reserved_cash += estimated_amount + estimated_fee

    status = "confirmed" if bool(cfg.get("auto_confirm_generated_plan", False)) else "draft"
    orders = sell_orders + buy_orders
    for order in orders:
        order["status"] = status
    plan = {
        "plan_date": signal_date,
        "execute_date": execute_date,
        "signal_date": signal_date,
        "status": status,
        "cash_before": float(account.get("cash") or 0),
        "portfolio_value_before": value["total_value"],
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "orders": orders,
        "skipped": skipped,
        "source": {
            "history": f"data/history/{signal_date}/all.json",
            "min_score": min_score,
        },
        "config_snapshot": plan_config_snapshot(cfg),
    }
    atomic_write_json(plan_path(cfg, execute_date), plan)
    return plan


def update_plan_status(execute_date: str, status: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or trading_config()
    path = plan_path(cfg, execute_date)
    plan = load_json(path)
    if not plan:
        raise FileNotFoundError(f"交易计划不存在: {path}")
    plan["status"] = status
    for order in plan.get("orders", []):
        if order.get("status") not in {"filled", "canceled"}:
            order["status"] = status
    plan["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    atomic_write_json(path, plan)
    return plan


def execute_plan(execute_date: str, cfg: dict[str, Any] | None = None, *, allow_draft: bool = False) -> dict[str, Any]:
    cfg = cfg or trading_config()
    ensure_layout(cfg)
    path = plan_path(cfg, execute_date)
    plan = load_json(path)
    if not plan:
        return {"status": "missing_plan", "message": f"没有 {execute_date} 的交易计划"}
    if plan.get("status") == "executed":
        return {"status": "already_executed", "message": "计划已经执行", "plan": plan}
    if plan.get("status") == "canceled":
        return {"status": "canceled", "message": "计划已取消", "plan": plan}
    if plan.get("status") == "draft" and not allow_draft:
        return {"status": "draft", "message": "计划仍是草稿，需要先确认", "plan": plan}

    account = ensure_account(cfg)
    positions = load_positions(cfg)
    by_code = {str(pos.get("code")): dict(pos) for pos in positions if pos.get("code")}
    fills: list[dict[str, Any]] = []

    for order in plan.get("orders", []):
        if order.get("status") in {"filled", "canceled"}:
            continue
        code = str(order.get("code") or "")
        side = str(order.get("side") or "")
        quantity = int(order.get("quantity") or 0)
        price_row = price_on_date(code, execute_date)
        open_price = price_row.get("open")
        close_price = price_row.get("close")
        if not code or quantity <= 0 or open_price is None:
            order["status"] = "skipped"
            order["message"] = "执行日缺少开盘价"
            continue
        if side == "BUY":
            fill_price = float(open_price) * (1 + float(cfg.get("buy_slippage_pct", 0.001)))
            amount = fill_price * quantity
            fee = commission(amount, cfg)
            if float(account.get("cash") or 0) < amount + fee:
                order["status"] = "skipped"
                order["message"] = "现金不足"
                continue
            account["cash"] = float(account.get("cash") or 0) - amount - fee
            existing = by_code.get(code)
            if existing:
                old_qty = int(existing.get("quantity") or 0)
                old_cost = float(existing.get("avg_cost") or 0) * old_qty
                new_qty = old_qty + quantity
                existing["quantity"] = new_qty
                existing["avg_cost"] = (old_cost + amount + fee) / new_qty
                existing["market_price"] = float(close_price or fill_price)
                existing["market_value"] = new_qty * float(existing["market_price"])
                by_code[code] = existing
            else:
                by_code[code] = {
                    "code": code,
                    "strategy": order.get("strategy") or "",
                    "quantity": quantity,
                    "avg_cost": (amount + fee) / quantity,
                    "entry_date": execute_date,
                    "entry_price": fill_price,
                    "market_price": float(close_price or fill_price),
                    "market_value": quantity * float(close_price or fill_price),
                }
            fill = {
                "execute_date": execute_date,
                "code": code,
                "side": side,
                "quantity": quantity,
                "price": fill_price,
                "amount": amount,
                "commission": fee,
                "stamp_tax": 0.0,
            }
        elif side == "SELL":
            existing = by_code.get(code)
            if not existing:
                order["status"] = "skipped"
                order["message"] = "无持仓"
                continue
            sell_qty = min(quantity, int(existing.get("quantity") or 0))
            fill_price = float(open_price) * (1 - float(cfg.get("sell_slippage_pct", 0.001)))
            amount = fill_price * sell_qty
            fee = commission(amount, cfg)
            tax = stamp_tax(amount, cfg)
            account["cash"] = float(account.get("cash") or 0) + amount - fee - tax
            cost = float(existing.get("avg_cost") or 0) * sell_qty
            account["realized_pnl"] = float(account.get("realized_pnl") or 0) + amount - fee - tax - cost
            remaining = int(existing.get("quantity") or 0) - sell_qty
            if remaining <= 0:
                by_code.pop(code, None)
            else:
                existing["quantity"] = remaining
                existing["market_price"] = float(close_price or fill_price)
                existing["market_value"] = remaining * float(existing["market_price"])
                by_code[code] = existing
            fill = {
                "execute_date": execute_date,
                "code": code,
                "side": side,
                "quantity": sell_qty,
                "price": fill_price,
                "amount": amount,
                "commission": fee,
                "stamp_tax": tax,
            }
        else:
            order["status"] = "skipped"
            order["message"] = f"未知方向: {side}"
            continue

        order["status"] = "filled"
        order["fill"] = fill
        fills.append(fill)
        append_jsonl(trading_dir(cfg) / "fills" / f"fills_{execute_date}.jsonl", fill)
        append_jsonl(trading_dir(cfg) / "orders" / f"orders_{execute_date}.jsonl", {**order, "execute_date": execute_date})

    plan["status"] = "executed" if fills else "skipped"
    plan["executed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    plan["fills"] = fills
    atomic_write_json(path, plan)
    save_account(cfg, account)
    save_positions(cfg, list(by_code.values()))
    snapshot = save_snapshot(execute_date, cfg)
    return {"status": plan["status"], "fills": fills, "plan": plan, "snapshot": snapshot}


def save_snapshot(as_of_date: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or trading_config()
    ensure_layout(cfg)
    value = portfolio_value(cfg, as_of_date)
    account = ensure_account(cfg)
    snapshot = {
        "date": as_of_date,
        "cash": value["cash"],
        "market_value": value["market_value"],
        "total_value": value["total_value"],
        "realized_pnl": value["realized_pnl"],
        "position_count": len(value["positions"]),
        "positions": value["positions"],
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    atomic_write_json(trading_dir(cfg) / "snapshots" / f"portfolio_{as_of_date}.json", snapshot)
    save_positions(cfg, value["positions"])
    save_account(cfg, account)
    upsert_equity_curve(equity_curve_path(cfg), snapshot, float(account.get("initial_cash") or cfg.get("initial_cash", 20000)))
    return snapshot


def upsert_equity_curve(path: Path, snapshot: dict[str, Any], initial_cash: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    by_date = {row["date"]: row for row in rows if row.get("date")}
    total_value = float(snapshot.get("total_value") or 0)
    by_date[str(snapshot["date"])] = {
        "date": str(snapshot["date"]),
        "cash": f"{float(snapshot.get('cash') or 0):.4f}",
        "market_value": f"{float(snapshot.get('market_value') or 0):.4f}",
        "total_value": f"{total_value:.4f}",
        "return_pct": f"{((total_value / initial_cash - 1) * 100) if initial_cash else 0:.4f}",
        "position_count": str(int(snapshot.get("position_count") or 0)),
    }
    ordered = [by_date[key] for key in sorted(by_date)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "cash", "market_value", "total_value", "return_pct", "position_count"],
        )
        writer.writeheader()
        writer.writerows(ordered)


def plan_files(cfg: dict[str, Any] | None = None) -> list[Path]:
    cfg = cfg or trading_config()
    plans_dir = trading_dir(cfg) / "plans"
    if not plans_dir.exists():
        return []
    return sorted(plans_dir.glob("plan_*.json"), reverse=True)
