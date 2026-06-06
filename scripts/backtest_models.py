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
            "recommended_by_model": d.get("recommended_by_model", {}),
            "scores_by_model": d.get("scores_by_model", {}),
            "verdicts_by_model": d.get("verdicts_by_model", {}),
            # Returns
            "buy_T_close_t+1_close": (closes[0] - close_T) / close_T if closes[0] is not None else None,
            "buy_T_close_t+1_max_high": (highs[0] - close_T) / close_T if highs[0] is not None else None,
            
            "buy_T_close_t+2_close": (closes[1] - close_T) / close_T if closes[1] is not None else None,
            "buy_T_close_t+2_max_high": (max([h for h in highs[:2] if h is not None]) - close_T) / close_T if any(highs[:2]) else None,
            
            "buy_T1_open_t+1_close": (closes[1] - opens[0]) / opens[0] if closes[1] is not None and opens[0] > 0 else None,
            "buy_T1_open_t+1_max_high": (highs[1] - opens[0]) / opens[0] if highs[1] is not None and opens[0] > 0 else None,
            
            "buy_T1_open_t+2_close": (closes[2] - opens[0]) / opens[0] if closes[2] is not None and opens[0] > 0 else None,
            "buy_T1_open_t+2_max_high": (max([h for h in highs[1:3] if h is not None]) - opens[0]) / opens[0] if any(highs[1:3]) and opens[0] > 0 else None,
        }
        
        results.append(stock_perf)

    print(f"Loaded {len(results)} valid backtest records.")

    # Convert to a flat list for pandas
    flat_data = []
    for r in results:
        row = {
            "code": r["code"],
            "buy_T_close_t+1_close": r["buy_T_close_t+1_close"],
            "buy_T_close_t+1_max_high": r["buy_T_close_t+1_max_high"],
            "buy_T_close_t+2_close": r["buy_T_close_t+2_close"],
            "buy_T_close_t+2_max_high": r["buy_T_close_t+2_max_high"],
            
            "buy_T1_open_t+1_close": r["buy_T1_open_t+1_close"],
            "buy_T1_open_t+1_max_high": r["buy_T1_open_t+1_max_high"],
            "buy_T1_open_t+2_close": r["buy_T1_open_t+2_close"],
            "buy_T1_open_t+2_max_high": r["buy_T1_open_t+2_max_high"],
        }
        for m in models:
            row[f"rec_{m}"] = r["recommended_by_model"].get(m, False)
            row[f"score_{m}"] = r["scores_by_model"].get(m, np.nan)
        flat_data.append(row)

    df = pd.DataFrame(flat_data)

    print("\n" + "="*80)
    print("BACKTEST STATISTICS GROUPED BY MODEL")
    print("="*80)

    # Let's print stats for each model
    for m in models:
        model_name = m.split("/")[-1]
        print(f"\n### Model: {model_name} ###")
        
        # Recommendations counts
        rec_true = df[df[f"rec_{m}"] == True]
        rec_false = df[df[f"rec_{m}"] == False]
        print(f"Recommended: {len(rec_true)} stocks | Not Recommended: {len(rec_false)} stocks")
        
        # Scenario A: Buy at T Close, Hold 1 & 2 Days
        # Scenario B: Buy at T+1 Open, Hold 1 & 2 Days
        
        print(f"\n{'Scenario & Period':<35} | {'Recommended Avg':<15} | {'Not Rec Avg':<12}")
        print("-"*70)
        
        metrics = [
            ("buy_T_close_t+1_close", "T Close Buy, Hold 1D Close"),
            ("buy_T_close_t+1_max_high", "T Close Buy, Hold 1D Max High"),
            ("buy_T_close_t+2_close", "T Close Buy, Hold 2D Close"),
            ("buy_T_close_t+2_max_high", "T Close Buy, Hold 2D Max High"),
            
            ("buy_T1_open_t+1_close", "T+1 Open Buy, Hold 1D Close (T+2)"),
            ("buy_T1_open_t+1_max_high", "T+1 Open Buy, Hold 1D Max High (T+2)"),
            ("buy_T1_open_t+2_close", "T+1 Open Buy, Hold 2D Close (T+3)"),
            ("buy_T1_open_t+2_max_high", "T+1 Open Buy, Hold 2D Max High (T+3)"),
        ]
        
        for col, label in metrics:
            val_true = rec_true[col].mean()
            val_false = rec_false[col].mean()
            print(f"{label:<35} | {val_true*100:13.2f}% | {val_false*100:10.2f}%")
            
        # Win rate for Max High > 2%
        print("-"*70)
        print("Win Rate (Max High > 2%):")
        for col, label in [("buy_T_close_t+1_max_high", "T Close Buy, Hold 1D"),
                           ("buy_T_close_t+2_max_high", "T Close Buy, Hold 2D"),
                           ("buy_T1_open_t+1_max_high", "T+1 Open Buy, Hold 1D"),
                           ("buy_T1_open_t+2_max_high", "T+1 Open Buy, Hold 2D")]:
            wr_true = (rec_true[col] > 0.02).mean()
            wr_false = (rec_false[col] > 0.02).mean()
            print(f"  {label:<33} | {wr_true*100:13.2f}% | {wr_false*100:10.2f}%")

        # Correlation between score and returns
        print("-"*70)
        print("Score-to-Return Pearson Correlation:")
        for col, label in [("buy_T_close_t+1_close", "T Close Buy, Hold 1D Close"),
                           ("buy_T_close_t+1_max_high", "T Close Buy, Hold 1D Max High"),
                           ("buy_T1_open_t+1_close", "T+1 Open Buy, Hold 1D Close")]:
            # drop nans
            subset = df[[f"score_{m}", col]].dropna()
            if len(subset) > 1:
                corr = subset[f"score_{m}"].corr(subset[col])
                print(f"  {label:<33} | {corr:14.3f}")
            else:
                print(f"  {label:<33} | N/A")

if __name__ == "__main__":
    main()
