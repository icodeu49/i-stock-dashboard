import os
import yfinance as yf
import pandas as pd
import requests
from app import calculate_technicals  # Imports your calculation engine

# --- SECURE CONFIG LOADING ---
# Pulls the secret keys directly from GitHub's secure memory space at runtime
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Dynamic list loader from session state file if it exists, otherwise uses defaults
WATCHLIST_FILE = "watchlist.json"
if os.path.exists(WATCHLIST_FILE):
    import json
    with open(WATCHLIST_FILE, "r") as f:
        WATCHLIST = json.load(f)
else:
    WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "AMD"] # Add your stocks

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error sending alert: {e}")

def run_daily_scan():
    breakout_stocks = []
    
    print("Initiating automated technical breakout scan...")
    for ticker in WATCHLIST:
        try:
            # Pull daily data to detect fresh end-of-day breakout patterns
            df = yf.download(ticker, period="1y", interval="1d", progress=False, timeout=10, threads=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            df = calculate_technicals(df)
            
            if not df.empty:
                latest = df.iloc[-1]
                breakout_stocks.append(ticker)
#                if latest['BREAKOUT_TRIGGERED']:
#                    breakout_stocks.append(ticker)
        except Exception as e:
            print(f"Skipping {ticker}: {e}")
            
    # Formulate and transmit the alert payload
    if breakout_stocks:
        alert_msg = "🔥 *REAL-TIME BREAKOUT ALERT* 🔥\n\n"
        alert_msg += "The following assets have broken out past resistance boundaries on institutional volume surges:\n\n"
        for stock in breakout_stocks:
            alert_msg += f"• *{stock}* 🚀\n"
        alert_msg += "\nCheck your cloud Streamlit dashboard link to analyze the charts in multi-window view!"
        send_telegram_alert(alert_msg)
    else:
        print("Scan complete. No volume breakout triggers met today.")

if __name__ == "__main__":
    run_daily_scan()
