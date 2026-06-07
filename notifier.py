import os
import sys

# Completely decouple the background environment wrapper from Streamlit's runtime engine
os.environ["STREAMLIT_RUN_PURE"] = "true"

import yfinance as yf
import pandas as pd
import requests
import json
from app import calculate_technicals  # Safely imports the math model matrix

# --- SECURE CREDENTIAL ARRAYS ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WATCHLIST_FILE = "watchlist.json"

# Read your watchlist file directly from disk, bypassing Streamlit session states entirely
if os.path.exists(WATCHLIST_FILE):
    try:
        with open(WATCHLIST_FILE, "r") as f:
            WATCHLIST = json.load(f)
    except Exception:
        WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
else:
    WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Telegram API response code: {r.status_code}")
    except Exception as e:
        print(f"Critical error sending Telegram message payload: {e}")

def run_daily_scan():
    breakout_stocks = []
    print(f"Initiating automated technical breakout scan for assets: {WATCHLIST}")
    
    for ticker in WATCHLIST:
        print(f"Crunching technical metrics for: {ticker}...")
        try:
            df = yf.download(ticker, period="1y", interval="1d", progress=False, timeout=10, threads=False)
            if df.empty:
                print(f"⚠️ Empty dataframe returned for {ticker}")
                continue
                
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            # Run calculations
            df = calculate_technicals(df)
            
            # FORCED TEST TRIGGER: Appends every successfully parsed stock to ensure verification
            #if not df.empty:
            #    breakout_stocks.append(ticker)

            # RESTORED: Only triggers when all 3 elite institutional volume rules are met
            if not df.empty:
                latest = df.iloc[-1]
                if 'BREAKOUT_TRIGGERED' in latest and latest['BREAKOUT_TRIGGERED']:
                    breakout_stocks.append(ticker)                
                
        except Exception as e:
            print(f"Skipping {ticker} due to calculation error: {e}")
            
    print(f"Scan complete. Found triggers for: {breakout_stocks}")
    
    # Send the alert message payload
    if breakout_stocks:
        alert_msg = "🔥 *REAL-TIME BREAKOUT ALERT* 🔥\n\n"
        alert_msg += "The following assets have broken out past resistance boundaries on institutional volume surges:\n\n"
        for stock in breakout_stocks:
            alert_msg += f"• *{stock}* 🚀\n"
        alert_msg += "\nCheck your cloud Streamlit dashboard link to analyze the charts in multi-window view!"
        send_telegram_alert(alert_msg)
    else:
        print("No volume breakout conditions were met today.")

if __name__ == "__main__":
    run_daily_scan()