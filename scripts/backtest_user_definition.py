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
        
        # We need subsequent prices (T+1 to T+4)
        subsequent = []
        for offset in range(1, 5): # index offset 1 = T+1, 2 = T+2, 3 = T+3, 4 = T+4
            n_idx = idx + offset
            if n_idx < len(df):
                subsequent.append(df.iloc[n_idx])
            else:
                subsequent.append(None)

        if len(subsequent) < 4 or any(s is None for s in subsequent):
            # Must have complete T+1 to T+4 data
            continue

        # Extract values
        open_T1 = subsequent[0]["open"]
        
        close_T2 = subsequent[1]["close"]
        high_T2 = subsequent[1]["high"]
        low_T2 = subsequent[1]["low"]
        
        close_T3 = subsequent[2]["close"]
        high_T3 = subsequent[2]["high"]
        low_T3 = subsequent[2]["low"]
        
        close_T4 = subsequent[3]["close"]
        high_T4 = subsequent[3]["high"]
        low_T4 = subsequent[3]["low"]

        # Lows for drawdown include T+1 low
        low_T1 = subsequent[0]["low"]

        if open_T1 <= 0:
            continue

        stock_perf = {
            "code": code,
            "verdicts_by_model": d.get("verdicts_by_model", {}),
            # Hold 2 Days (Sell at T+2 Close)
            "hold_2d_close": (close_T2 - open_T1) / open_T1,
            "hold_2d_max_high": (high_T2 - open_T1) / open_T1, # can only sell starting T+2
            "hold_2d_min_low": (min(low_T1, low_T2) - open_T1) / open_T1, # drawdown includes T+1 & T+2
            
            # Hold 3 Days (Sell at T+3 Close)
            "hold_3d_close": (close_T3 - open_T1) / open_T1,
            "hold_3d_max_high": (max(high_T2, high_T3) - open_T1) / open_T1,
            "hold_3d_min_low": (min(low_T1, low_T2, low_T3) - open_T1) / open_T1,
            
            # Hold 4 Days (Sell at T+4 Close)
            "hold_4d_close": (close_T4 - open_T1) / open_T1,
            "hold_4d_max_high": (max(high_T2, high_T3, high_T4) - open_T1) / open_T1,
            "hold_4d_min_low": (min(low_T1, low_T2, low_T3, low_T4) - open_T1) / open_T1,
        }
        
        results.append(stock_perf)

    print(f"Loaded {len(results)} valid backtest records with complete 4-day subsequent data.")

    # Convert to flat list for pandas
    flat_data = []
    for r in results:
        row = {
            "code": r["code"],
            "h2_close": r["hold_2d_close"],
            "h2_high": r["hold_2d_max_high"],
            "h2_low": r["hold_2d_min_low"],
            
            "h3_close": r["hold_3d_close"],
            "h3_high": r["hold_3d_max_high"],
            "h3_low": r["hold_3d_min_low"],
            
            "h4_close": r["hold_4d_close"],
            "h4_high": r["hold_4d_max_high"],
            "h4_low": r["hold_4d_min_low"],
        }
        for m in models:
            row[f"verdict_{m}"] = r["verdicts_by_model"].get(m, "FAIL")
        flat_data.append(row)

    df = pd.DataFrame(flat_data)

    # Print results grouped by model and verdict
    print("\n" + "="*95)
    print("BACKTEST STATISTICS BY MODEL VERDICTS (T+1 OPEN ENTRY, HOLD 2, 3, 4 DAYS)")
    print("="*95)

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
            
            # Print table headers
            print(f"  {'Hold Period':<12} | {'Avg Close':<10} | {'Median Close':<12} | {'Win (Close>0)':<15} | {'Avg Max High':<12} | {'Win (High>2%)':<15} | {'Avg Min Low':<12}")
            print(f"  " + "-"*95)
            
            for k in [2, 3, 4]:
                c_col = f"h{k}_close"
                h_col = f"h{k}_high"
                l_col = f"h{k}_low"
                
                avg_close = subset[c_col].mean()
                med_close = subset[c_col].median()
                win_close = (subset[c_col] > 0).mean()
                
                avg_high = subset[h_col].mean()
                win_high_2 = (subset[h_col] > 0.02).mean()
                
                avg_low = subset[l_col].mean()
                
                print(f"  Hold {k} Days  | {avg_close*100:8.2f}% | {med_close*100:10.2f}% | {win_close*100:13.2f}% | {avg_high*100:10.2f}% | {win_high_2*100:13.2f}% | {avg_low*100:10.2f}%")

if __name__ == "__main__":
    main()
