# notifier.py

# notifier.py
import os
import json
import yfinance as yf
import pandas as pd

# Safely import code engine from our isolated backend module
from helpers import calculate_technicals

# --- CHANGE THIS LINE TO FORCE ABSOLUTE PATHING ---

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
# --------------------------------------------------

def run_automated_scanner():
    print("🤖 Booting automated analysis daemon workflow...")
    
    # 1. Read category profile configurations safely
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r") as f:
            raw_data = json.load(f)
    else:
        print("❌ System watchlist file context configuration missing. Execution aborted.")
        return

    if isinstance(raw_data, list):
        print("💡 Detected legacy list format. Automatically converting to dictionary framework...")
        watchlist = {ticker: {"group": "Small/Mid Growth"} for ticker in raw_data}
    elif isinstance(raw_data, dict):
        watchlist = raw_data
    else:
        return

    tickers = list(watchlist.keys())
    spy_df = yf.download("SPY", period="2y", interval="1d", progress=False, multi_level_index=False)
    if isinstance(spy_df.columns, pd.MultiIndex):
        spy_df.columns = spy_df.columns.get_level_values(0)

    alert_triggers = []
    
    for ticker in tickers:
        try:
            df_raw = yf.download(ticker, period="2y", interval="1d", progress=False, multi_level_index=False)
            if df_raw.empty: continue
                
            df = calculate_technicals(df_raw, timeframe="Daily", spy_df=spy_df)
            latest = df.iloc[-1]
            
            if latest['BREAKOUT_TRIGGERED']:
                alert_triggers.append(ticker)
                
        except Exception as e:
            continue

    # ─── ADDED SAVE LOGIC ───────────────────────────────────────
    # This ensures the converted dictionary structure gets saved to disk!
    try:
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(watchlist, f, indent=4)
        print("💾 Success: watchlist.json successfully written to disk architecture.")
    except Exception as e:
        print(f"❌ Failed writing structural dictionary framework data to file: {e}")
    # ────────────────────────────────────────────────────────────

    print("\n==========================================")
    print("🏁 AUTOMATION RUN MATRIX COMPLETED")
    print(f"Active Alert Triggers Flagged: {alert_triggers}")
    print("==========================================")

if __name__ == "__main__":
    run_automated_scanner()