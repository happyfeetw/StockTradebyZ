import json
import pandas as pd
import numpy as np
from pathlib import Path

def main():
    workspace_dir = Path("/Users/wangxinduo/Development/Code/Personal/StockTradebyZ-pre-refactor-baseline")
    consensus_dir = workspace_dir / "data" / "review_consensus" / "2026-03-02_e7c29c1a"
    raw_dir = workspace_dir / "data" / "raw"

    decisions_path = consensus_dir / "decisions.json"
    if not decisions_path.exists():
        print(f"Error: decisions.json not found at {decisions_path}")
        return

    with open(decisions_path, "r", encoding="utf-8") as f:
        decisions = json.load(f)

    # Filter for brick strategy
    brick_decisions = [d for d in decisions if d.get("strategy") == "brick"]
    print(f"Found {len(brick_decisions)} brick decisions on 2026-03-02.")

    # Target pick date
    pick_date_str = "2026-03-02"
    pick_date = pd.to_datetime(pick_date_str)

    # Models list
    models = [
        "gemini-cli/gemini-3.1-pro-preview",
        "agy-cli-experimental/gemini-3.5-flash-high",
        "codex-cli/gpt-5.5-high-standard"
    ]

    results = []

    for d in brick_decisions:
        code = d["code"]
        
        # Load raw stock price data
        csv_path = raw_dir / f"{code}.csv"
        if not csv_path.exists():
            continue

        df = pd.read_csv(csv_path)
        df["date"] = pd.to_datetime(df["date"])
        
        matching = df[df["date"] == pick_date]
        if matching.empty:
            continue

        idx = matching.index[0]
        
        # Entry prices
        close_T = matching.iloc[0]["close"]
        
        # Subsequent prices (T+1 to T+5)
        subsequent = []
        for offset in range(1, 6):
            n_idx = idx + offset
            if n_idx < len(df):
                subsequent.append(df.iloc[n_idx])
            else:
                subsequent.append(None)

        if not subsequent[0] is not None:
            continue

        opens = [sub["open"] if sub is not None else None for sub in subsequent]
        closes = [sub["close"] if sub is not None else None for sub in subsequent]
        highs = [sub["high"] if sub is not None else None for sub in subsequent]
        lows = [sub["low"] if sub is not None else None for sub in subsequent]

        stock_perf = {
            "code": code,
            "close_T": close_T,
            "open_T1": opens[0],
            # Model specific metadata
            "verdicts_by_model": d.get("verdicts_by_model", {}),
            # Returns data structures for buy at T close and buy at T+1 open
            "buy_T_close": {},
            "buy_T1_open": {}
        }

        # Calculate Scenario A (Buy T Close)
        for k in range(1, 5):
            close_k = closes[k-1]
            if close_k is not None:
                stock_perf["buy_T_close"][f"t+{k}_close"] = (close_k - close_T) / close_T
                valid_highs = [h for h in highs[:k] if h is not None]
                stock_perf["buy_T_close"][f"t+{k}_max_high"] = (max(valid_highs) - close_T) / close_T if valid_highs else None
                valid_lows = [l for l in lows[:k] if l is not None]
                stock_perf["buy_T_close"][f"t+{k}_min_low"] = (min(valid_lows) - close_T) / close_T if valid_lows else None
            else:
                stock_perf["buy_T_close"][f"t+{k}_close"] = None
                stock_perf["buy_T_close"][f"t+{k}_max_high"] = None
                stock_perf["buy_T_close"][f"t+{k}_min_low"] = None

        # Calculate Scenario B (Buy T+1 Open, T+1 restricted)
        open_T1 = opens[0]
        if open_T1 is not None and open_T1 > 0:
            for k in range(1, 5):
                close_target = closes[k]
                if close_target is not None:
                    stock_perf["buy_T1_open"][f"t+{k}_close"] = (close_target - open_T1) / open_T1
                    sellable_highs = [h for h in highs[1:k+1] if h is not None]
                    stock_perf["buy_T1_open"][f"t+{k}_max_high"] = (max(sellable_highs) - open_T1) / open_T1 if sellable_highs else None
                    all_lows = [l for l in lows[:k+1] if l is not None]
                    stock_perf["buy_T1_open"][f"t+{k}_min_low"] = (min(all_lows) - open_T1) / open_T1 if all_lows else None
                else:
                    stock_perf["buy_T1_open"][f"t+{k}_close"] = None
                    stock_perf["buy_T1_open"][f"t+{k}_max_high"] = None
                    stock_perf["buy_T1_open"][f"t+{k}_min_low"] = None

        results.append(stock_perf)

    # Convert to flat dictionary list
    flat_data = []
    for r in results:
        row = {
            "code": r["code"]
        }
        for k in range(1, 5):
            row[f"buy_T_close_t+{k}_close"] = r["buy_T_close"][f"t+{k}_close"]
            row[f"buy_T_close_t+{k}_max_high"] = r["buy_T_close"][f"t+{k}_max_high"]
            row[f"buy_T_close_t+{k}_min_low"] = r["buy_T_close"][f"t+{k}_min_low"]
            
            row[f"buy_T1_open_t+{k}_close"] = r["buy_T1_open"].get(f"t+{k}_close")
            row[f"buy_T1_open_t+{k}_max_high"] = r["buy_T1_open"].get(f"t+{k}_max_high")
            row[f"buy_T1_open_t+{k}_min_low"] = r["buy_T1_open"].get(f"t+{k}_min_low")
            
        for m in models:
            row[f"verdict_{m}"] = r["verdicts_by_model"].get(m, "FAIL")
        flat_data.append(row)

    df = pd.DataFrame(flat_data)

    # Print results grouped by model and verdict
    print("\n" + "="*90)
    print("BACKTEST STATISTICS BY MODEL VERDICTS (PASS, WATCH, FAIL)")
    print("="*90)

    for m in models:
        model_name = m.split("/")[-1]
        print(f"\n################################################################################")
        print(f"MODEL: {model_name}")
        print(f"################################################################################")

        verdicts = ["PASS", "WATCH", "FAIL"]
        for v in verdicts:
            subset = df[df[f"verdict_{m}"] == v]
            if len(subset) == 0:
                print(f"\n--- Verdict Group: {v} (0 stocks) --- No data")
                continue
                
            print(f"\n--- Verdict Group: {v} (Total Stocks: {len(subset)}) ---")
            
            for scenario, prefix in [("Scenario A (Buy T Close)", "buy_T_close_"), 
                                     ("Scenario B (Buy T+1 Open, T+1 Restricted)", "buy_T1_open_")]:
                print(f"  [{scenario}]")
                print(f"    {'Hold Period':<12} | {'Avg Close':<10} | {'Win (Close>0)':<15} | {'Avg Max High':<12} | {'Win (High>2%)':<15} | {'Avg Min Low':<12}")
                print(f"    " + "-"*85)
                for k in range(1, 5):
                    close_col = f"{prefix}t+{k}_close"
                    high_col = f"{prefix}t+{k}_max_high"
                    low_col = f"{prefix}t+{k}_min_low"
                    
                    valid_close = subset[close_col].dropna()
                    valid_high = subset[high_col].dropna()
                    valid_low = subset[low_col].dropna()
                    
                    if len(valid_close) > 0:
                        avg_close = valid_close.mean()
                        win_close = (valid_close > 0).mean()
                        avg_high = valid_high.mean()
                        win_high_2 = (valid_high > 0.02).mean()
                        avg_low = valid_low.mean()
                        print(f"    Hold {k} Days   | {avg_close*100:8.2f}% | {win_close*100:13.2f}% | {avg_high*100:10.2f}% | {win_high_2*100:13.2f}% | {avg_low*100:10.2f}%")
                    else:
                        print(f"    Hold {k} Days   | N/A")

if __name__ == "__main__":
    main()
