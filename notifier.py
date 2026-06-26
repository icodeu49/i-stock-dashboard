import os
import json
import requests
import yfinance as yf
import pandas as pd

# Safely import code engine from our isolated backend module
from helpers import calculate_technicals

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")

# 📡 TELEGRAM CREDENTIALS FROM ENVIRONMENT VARIABLES
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_report(message_text):
    """Dispatches the structured alert matrix straight to your Telegram device."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram Config Missing: Report printing to console only.")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=12)
        if response.status_code == 200:
            print("🚀 Telemetry Report transmitted to Telegram successfully.")
            return True
        else:
            print(f"❌ Telegram API Error: Status {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Network Exception during Telegram dispatch: {e}")
        return False

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

    # Dictionary to collect results by stock and timeframe
    scan_results = {}
    alert_triggers_summary = []
    
    # 2. RUN TECHNICALS MATRIX ACROSS ALL TIMEFRAMES
    for ticker in tickers:
        try:
            df_raw = yf.download(ticker, period="2y", interval="1d", progress=False, multi_level_index=False)
            if df_raw.empty: continue
            
            scan_results[ticker] = {}
            
            for tf in ["Daily", "Weekly", "Monthly"]:
                df = calculate_technicals(df_raw, timeframe=tf, spy_df=spy_df)
                latest = df.iloc[-1]
                
                # Check for standard breakout trigger
                is_breakout = latest.get('BREAKOUT_TRIGGERED', False)
                
                # Check specifically for a fresh VSTOP breakdown turning red on macro charts
                is_vstop_sell = latest.get('VSTOP_SELL_SIGNAL', False)
                is_macro_bear_flip = (tf in ["Weekly", "Monthly"]) and is_vstop_sell
                
                # Trigger system alert if either condition is met
                is_triggered = is_breakout or is_macro_bear_flip
                
                scan_results[ticker][tf] = {
                    "triggered": is_triggered,
                    "is_bearish_vstop": is_macro_bear_flip,
                    "matrix": "BEARISH" if (is_macro_bear_flip or not latest.get('EMA_SPEED_ALIGNED', True)) else "BULLISH",
                    "rs_score": round(latest.get('RS_SCORE', 0.0), 2),
                    "pocket_pivot": latest.get('POCKET_PIVOT', False),
                    "vol_accumulation": latest.get('ACCUMULATION_DAY', False),
                    "speed_emas": latest.get('EMA_SPEED_ALIGNED', True),
                    "sar_support": latest.get('SAR_ALIGNED', True),
                    "adx": round(latest.get('ADX', 0