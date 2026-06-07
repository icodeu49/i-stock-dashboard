import os
import sys
import json
import requests
import yfinance as yf
import pandas as pd

os.environ["STREAMLIT_RUN_PURE"] = "true"
from app import calculate_technicals

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WATCHLIST_FILE = "watchlist.json"

if os.path.exists(WATCHLIST_FILE):
    try:
        with open(WATCHLIST_FILE, "r") as f: WATCHLIST = json.load(f)
    except Exception: WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
else:
    WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except Exception as e: print(f"Telegram connection error: {e}")

def run_daily_scan():
    print("Downloading global SPY index benchmark details...")
    spy_df = yf.download("SPY", period="1y", interval="1d", progress=False, timeout=10)
    triggered_reports = []
    
    for ticker in WATCHLIST:
        try:
            df = yf.download(ticker, period="1y", interval="1d", progress=False, timeout=10, threads=False)
            if df.empty: continue
            
            df = calculate_technicals(df, spy_df=spy_df)
            if df.empty: continue
            
            latest = df.iloc[-1]
            
            # Extract values from the full indicator stack
            is_pocket = latest.get('POCKET_PIVOT', False)
            is_accum = latest.get('ACCUMULATION_DAY', False)
            is_ema = latest.get('EMA_SPEED_ALIGNED', False)
            is_sar = latest.get('SAR_ALIGNED', False)
            is_adx = latest.get('ADX_STRONG', False)
            buy_alert = latest.get('VSTOP_BUY_SIGNAL', False)
            sell_alert = latest.get('VSTOP_SELL_SIGNAL', False)
            
            trend_state = "🟢 BULLISH UP-TREND" if latest.get('VSTOP_TREND', 1) == 1 else "🔴 BEARISH DOWN-TREND"
            rs_score = latest.get('RS_SCORE', 0.0)
            adx_val = latest.get('ADX', 0.0)

            # FORCED LIVE CHECK LIST VERIFICATION SELECTION:
            # Change 'True' to 'is_pocket or is_accum or buy_alert or sell_alert' for live tracking
            SHOULD_REPORT = True
            
            if SHOULD_REPORT:
                report = f"• *{ticker}* | Structural Trend: `{trend_state}`\n"
                report += f"   ├── 📊 RS Score: `{rs_score:+.2f}%` vs SPY\n"
                report += f"   ├── ⚡ Pocket Pivot Matrix: {'✅ TRIGGERED' if is_pocket else '❌ No Surge'}\n"
                report += f"   ├── 📈 Vol Accumulation Day: {'✅ DETECTED' if is_accum else '❌ Normal Vol'}\n"
                report += f"   ├── 🚀 Speed EMAs (10 > 30): {'✅ BULLISH' if is_ema else '❌ BEARISH'}\n"
                report += f"   ├── 🎯 Parabolic SAR Support: {'✅ ABOVE SAR' if is_sar else '❌ BELOW SAR'}\n"
                report += f"   └── 🌊 Trend Strength (ADX): `{adx_val:.1f}` {'🔥 (Strong Rising)' if is_adx else '⏳ (Weak/Sideways)'}\n"
                
                if buy_alert:
                    report += f"   ⚠️ *ALERT: VOLATILITY STOP FLIPPED GREEN (BUY SIGNAL)* 🚀\n"
                elif sell_alert:
                    report += f"   ⚠️ *ALERT: VOLATILITY STOP FLIPPED RED (SELL SIGNAL)* 🚨\n"
                
                triggered_reports.append(report)
                
        except Exception as e:
            print(f"Skipping asset loop processing error for {ticker}: {e}")
            
    if triggered_reports:
        msg = "🎯 *INSTITUTIONAL METRIC SCORECARD* 🎯\n\n"
        msg += "\n".join(triggered_reports)
        msg += "\n\nOpen your cloud workspace terminal configuration to verify visual matrices!"
        send_telegram_alert(msg)

if __name__ == "__main__":
    run_daily_scan()