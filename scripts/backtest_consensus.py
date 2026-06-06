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

    results = []

    for d in brick_decisions:
        code = d["code"]
        bucket = d["decision_bucket"]
        recommended_count = d.get("recommended_count", 0)
        
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
        
        # We need subsequent prices (T+1 to T+5)
        subsequent = []
        for offset in range(1, 6):
            n_idx = idx + offset
            if n_idx < len(df):
                subsequent.append(df.iloc[n_idx])
            else:
                subsequent.append(None)

        if not subsequent[0] is not None:
            # Must have at least T+1 data to be useful
            continue

        # Extract prices safely
        opens = [sub["open"] if sub is not None else None for sub in subsequent]
        closes = [sub["close"] if sub is not None else None for sub in subsequent]
        highs = [sub["high"] if sub is not None else None for sub in subsequent]
        lows = [sub["low"] if sub is not None else None for sub in subsequent]

        stock_perf = {
            "code": code,
            "bucket": bucket,
            "recommended_count": recommended_count,
            "close_T": close_T,
            "open_T1": opens[0],
            # Buy at T Close returns
            "buy_T_close": {},
            # Buy at T+1 Open returns
            "buy_T1_open": {}
        }

        # Scenario 1: Buy at T Close (March 2 Close)
        # Hold 1 day: sell at T+1 Close. Can sell at T+1 High.
        # Hold 2 days: sell at T+2 Close. Can sell at max(T+1 High, T+2 High).
        for k in range(1, 5): # k = 1, 2, 3, 4
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

        # Scenario 2: Buy at T+1 Open (March 3 Open)
        # Due to T+1 trading rules:
        # Hold 1 day (T+1 to T+2): Can only sell on T+2. So sell close is T+2 Close. Max sellable high is T+2 High (cannot sell at T+1 High!).
        # Hold 2 days (T+1 to T+3): Can sell on T+2 or T+3. Max sellable high is max(T+2 High, T+3 High).
        # Hold 3 days (T+1 to T+4): Can sell on T+2, T+3, or T+4. Max sellable high is max(T+2..T+4 High).
        # Hold 4 days (T+1 to T+5): Can sell on T+2..T+5. Max sellable high is max(T+2..T+5 High).
        # Drawdowns (min low) include T+1 low since paper loss is carried.
        open_T1 = opens[0]
        if open_T1 is not None and open_T1 > 0:
            for k in range(1, 5): # k = 1, 2, 3, 4
                close_target = closes[k] # T+2 Close for k=1, T+3 Close for k=2, etc.
                if close_target is not None:
                    stock_perf["buy_T1_open"][f"t+{k}_close"] = (close_target - open_T1) / open_T1
                    
                    # Max sellable high starts from T+2 (index 1) to T+k+1 (index k)
                    sellable_highs = [h for h in highs[1:k+1] if h is not None]
                    stock_perf["buy_T1_open"][f"t+{k}_max_high"] = (max(sellable_highs) - open_T1) / open_T1 if sellable_highs else None
                    
                    # Min lows includes T+1 (index 0) to T+k+1 (index k)
                    all_lows = [l for l in lows[:k+1] if l is not None]
                    stock_perf["buy_T1_open"][f"t+{k}_min_low"] = (min(all_lows) - open_T1) / open_T1 if all_lows else None
                else:
                    stock_perf["buy_T1_open"][f"t+{k}_close"] = None
                    stock_perf["buy_T1_open"][f"t+{k}_max_high"] = None
                    stock_perf["buy_T1_open"][f"t+{k}_min_low"] = None
        
        results.append(stock_perf)

    # Analyze results
    print(f"Loaded {len(results)} valid backtest records.")

    def run_statistics(records, group_name):
        # Flatten records for group
        flat = []
        for r in records:
            row = {
                "code": r["code"],
                "bucket": r["bucket"],
                "recommended_count": r["recommended_count"]
            }
            # Add buy_T_close
            for key, val in r["buy_T_close"].items():
                row[f"buy_T_close_{key}"] = val
            # Add buy_T1_open
            for key, val in r["buy_T1_open"].items():
                row[f"buy_T1_open_{key}"] = val
            flat.append(row)
        
        df = pd.DataFrame(flat)
        if df.empty:
            print(f"No records for group: {group_name}")
            return

        print(f"\n==================================================")
        print(f"GROUP: {group_name} (Total Stocks: {len(df)})")
        print(f"==================================================")

        for scenario, prefix in [("Scenario A: Buy at T Close (March 2 Close)", "buy_T_close_"), 
                                 ("Scenario B: Buy at T+1 Open (March 3 Open, T+1 Restricted)", "buy_T1_open_")]:
            print(f"\n--- {scenario} ---")
            print(f"{'Hold Period':<12} | {'Avg Close':<10} | {'Win Rate (Close>0)':<20} | {'Avg Max High':<12} | {'Win (High>2%)':<15} | {'Avg Min Low':<12}")
            print("-"*85)
            for k in range(1, 5):
                close_col = f"{prefix}t+{k}_close"
                high_col = f"{prefix}t+{k}_max_high"
                low_col = f"{prefix}t+{k}_min_low"
                
                valid_close = df[close_col].dropna()
                valid_high = df[high_col].dropna()
                valid_low = df[low_col].dropna()
                
                if len(valid_close) > 0:
                    avg_close = valid_close.mean()
                    win_close = (valid_close > 0).mean()
                    avg_high = valid_high.mean() if len(valid_high) > 0 else 0
                    win_high_2 = (valid_high > 0.02).mean() if len(valid_high) > 0 else 0
                    avg_low = valid_low.mean() if len(valid_low) > 0 else 0
                    
                    print(f"Hold {k} Days   | {avg_close*100:8.2f}% | {win_close*100:18.2f}% | {avg_high*100:10.2f}% | {win_high_2*100:13.2f}% | {avg_low*100:10.2f}%")
                else:
                    print(f"Hold {k} Days   | N/A")

    # Run stats on different slices
    # 1. All Brick Candidates
    run_statistics(results, "All Brick Candidates")

    # 2. Consensus recommended (Score >= 4 by majority or all models)
    consensus_rec = [r for r in results if r["bucket"] in ["majority_recommended", "all_models_recommended"]]
    run_statistics(consensus_rec, "Consensus Recommended (Majority/All Models)")

    # 3. None Recommended
    none_rec = [r for r in results if r["bucket"] == "none_recommended"]
    run_statistics(none_rec, "Not Recommended (None Models)")

    # 4. Single Model Recommended
    single_rec = [r for r in results if r["bucket"] == "single_model_recommended"]
    run_statistics(single_rec, "Single Model Recommended")

    # 5. Majority Recommended
    majority_rec = [r for r in results if r["bucket"] == "majority_recommended"]
    run_statistics(majority_rec, "Majority Recommended Only")

    # 6. All Models Recommended
    all_rec = [r for r in results if r["bucket"] == "all_models_recommended"]
    run_statistics(all_rec, "All Models Recommended Only")

if __name__ == "__main__":
    main()
