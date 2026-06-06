import os
import json
import pandas as pd
import numpy as np
from pathlib import Path

def main():
    workspace_dir = Path("/Users/wangxinduo/Development/Code/Personal/StockTradebyZ-pre-refactor-baseline")
    history_dir = workspace_dir / "data" / "history"
    raw_dir = workspace_dir / "data" / "raw"

    index_path = history_dir / "index.json"
    if not index_path.exists():
        print(f"Error: Index file not found at {index_path}")
        return

    with open(index_path, "r", encoding="utf-8") as f:
        history_index = json.load(f)

    dates_info = history_index.get("dates", [])
    print(f"Found {len(dates_info)} historical run dates.")

    all_stock_results = []

    for date_item in dates_info:
        date_str = date_item["date"]
        brick_json_path = history_dir / date_str / "brick.json"
        
        if not brick_json_path.exists():
            print(f"Skipping {date_str}: brick.json not found")
            continue

        with open(brick_json_path, "r", encoding="utf-8") as f:
            brick_data = json.load(f)

        results = brick_data.get("results", [])
        print(f"Processing {date_str}: {len(results)} brick candidates found.")

        for res in results:
            code = res["code"]
            status = res.get("status", "unreviewed")
            # Some files might not have status field or might be different
            # Let's check the score as well
            verdict = "FAIL"
            score = 0
            if "review" in res and res["review"]:
                verdict = res["review"].get("verdict", "FAIL")
                score = res["review"].get("total_score", 0)
            
            is_recommended = status == "recommended" or verdict == "PASS" or score >= 4.0

            csv_path = raw_dir / f"{code}.csv"
            if not csv_path.exists():
                # Try finding in raw dir
                continue

            # Load stock data
            df = pd.read_csv(csv_path)
            df["date"] = pd.to_datetime(df["date"])
            target_date = pd.to_datetime(date_str)
            
            matching = df[df["date"] == target_date]
            if matching.empty:
                continue

            idx = matching.index[0]
            close_0 = matching.iloc[0]["close"]

            # Calculate returns for T+1 to T+4
            stock_perf = {
                "date": date_str,
                "code": code,
                "is_recommended": is_recommended,
                "score": score,
                "close_0": close_0,
                "returns": {}
            }

            highs = []
            lows = []
            for k in range(1, 5):
                next_idx = idx + k
                if next_idx < len(df):
                    row = df.iloc[next_idx]
                    close_k = row["close"]
                    high_k = row["high"]
                    low_k = row["low"]
                    
                    highs.append(high_k)
                    lows.append(low_k)
                    
                    stock_perf["returns"][f"t+{k}_close"] = (close_k - close_0) / close_0
                    stock_perf["returns"][f"t+{k}_max_high"] = (max(highs) - close_0) / close_0
                    stock_perf["returns"][f"t+{k}_min_low"] = (min(lows) - close_0) / close_0
                else:
                    stock_perf["returns"][f"t+{k}_close"] = None
                    stock_perf["returns"][f"t+{k}_max_high"] = None
                    stock_perf["returns"][f"t+{k}_min_low"] = None

            all_stock_results.append(stock_perf)

    # Convert to DataFrame for easier analysis
    flat_data = []
    for s in all_stock_results:
        row = {
            "date": s["date"],
            "code": s["code"],
            "is_recommended": s["is_recommended"],
            "score": s["score"],
            "close_0": s["close_0"]
        }
        for k, v in s["returns"].items():
            row[k] = v
        flat_data.append(row)

    df_res = pd.DataFrame(flat_data)
    
    if df_res.empty:
        print("No stock performance data could be collected.")
        return

    # Print overall statistics
    print("\n" + "="*80)
    print("BACKTEST STATISTICS FOR BRICK CHART STRATEGY (1 to 4 Days Hold)")
    print("="*80)
    
    for group_name, group_df in [("All Candidates", df_res), ("Recommended Only (Score >= 4.0)", df_res[df_res["is_recommended"]])]:
        print(f"\n--- Group: {group_name} (Total Stocks: {len(group_df)}) ---")
        
        # Calculate average returns
        avg_stats = {}
        win_rates = {}
        
        for k in range(1, 5):
            close_col = f"t+{k}_close"
            high_col = f"t+{k}_max_high"
            low_col = f"t+{k}_min_low"
            
            valid_close = group_df[close_col].dropna()
            valid_high = group_df[high_col].dropna()
            valid_low = group_df[low_col].dropna()
            
            if len(valid_close) > 0:
                avg_stats[f"T+{k} Close Return"] = valid_close.mean()
                avg_stats[f"T+{k} Max High Return"] = valid_high.mean()
                avg_stats[f"T+{k} Min Low Return"] = valid_low.mean()
                
                win_rates[f"T+{k} Win Rate (Close > 0)"] = (valid_close > 0).mean()
                win_rates[f"T+{k} Win Rate (Max High > 2%)"] = (valid_high > 0.02).mean()
                win_rates[f"T+{k} Win Rate (Max High > 5%)"] = (valid_high > 0.05).mean()
        
        # Print tables
        print(f"{'Metric':<30} | {'Value':<10}")
        print("-"*45)
        for metric, val in avg_stats.items():
            print(f"{metric:<30} | {val*100:6.2f}%")
        print("-"*45)
        for metric, val in win_rates.items():
            print(f"{metric:<30} | {val*100:6.2f}%")

    # Date-by-date details
    print("\n" + "="*80)
    print("DATE-BY-DATE BREAKDOWN (Recommended Only)")
    print("="*80)
    rec_df = df_res[df_res["is_recommended"]]
    dates = sorted(rec_df["date"].unique())
    for d in dates:
        d_df = rec_df[rec_df["date"] == d]
        print(f"Date: {d} | Recommended Stocks: {len(d_df)}")
        for k in range(1, 5):
            close_col = f"t+{k}_close"
            high_col = f"t+{k}_max_high"
            valid_close = d_df[close_col].dropna()
            valid_high = d_df[high_col].dropna()
            if len(valid_close) > 0:
                print(f"  T+{k} Close: {valid_close.mean()*100:5.2f}% (Win: {(valid_close>0).mean()*100:5.1f}%) | Max High: {valid_high.mean()*100:5.2f}%")
            else:
                print(f"  T+{k}: No data")

if __name__ == "__main__":
    main()
