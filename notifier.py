import os
import sys
import json
import requests
import yfinance as yf
import pandas as pd

# Suppress Streamlit's internal execution triggers during background cron runs
os.environ["STREAMLIT_RUN_PURE"] = "true"
from app import calculate_technicals

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WATCHLIST_FILE = "watchlist.json"

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
        res = requests.post(url, json=payload, timeout=10)
        if not res.ok:
            print(f"❌ Telegram API Error Response: {res.text}")
        else:
            print("✨ Message chunk transmitted successfully to handset.")
    except Exception as e: 
        print(f"Telegram connection error: {e}")

def run_daily_scan():
    # Define our targeted analysis timeframes and text labels
    intervals = {
        "1d": "Daily Close",
        "1wk": "Weekly Close",
        "1mo": "Monthly Close"
    }

    triggered_reports = []
    
    for ticker in WATCHLIST:
        print(f"🔍 Processing asset: {ticker}...")
        
        for timeframe_key, timeframe_label in intervals.items():
            print(f"  ├── Analyzing {timeframe_label} timeframe...")
            
            try:
                # --- NEW CHANGES START HERE ---
                # Map yfinance short intervals to semantic app framework names
                tf_map = {"1d": "Daily", "1wk": "Weekly", "1mo": "Monthly"}
                chosen_tf = tf_map.get(timeframe_key, "Weekly")

                # Dynamic historical windows ensure math operations don't get starved of rows
                period_map = {"1d": "2y", "1wk": "5y", "1mo": "10y"}
                chosen_period = period_map.get(timeframe_key, "2y")

                # 📥 Step 1: Download matching interval for SPY reference baseline using dynamic buffer
                spy_df = yf.download("SPY", period=chosen_period, interval=timeframe_key, progress=False, timeout=15, multi_level_index=False)
                if isinstance(spy_df.columns, pd.MultiIndex):
                    spy_df.columns = spy_df.columns.get_level_values(0)
                
                # 📥 Step 2: Download matching interval for target stock using dynamic buffer
                df = yf.download(ticker, period=chosen_period, interval=timeframe_key, progress=False, timeout=15, threads=False, multi_level_index=False)
                if df.empty: 
                    continue
                
                if isinstance(df.columns, pd.MultiIndex): 
                    df.columns = df.columns.get_level_values(0)
                
                # 🧮 Step 3: Crunch technical calculations matrix with explicit length instructions
                df = calculate_technicals(df, timeframe=chosen_tf, spy_df=spy_df)
                # --- NEW CHANGES END HERE ---
                
                if df.empty or len(df) < 30: # Safety margin adjusted for monthly data sets
                    continue
                
                latest = df.iloc[-1]
                
                is_pocket = latest.get('POCKET_PIVOT', False)
                is_accum = latest.get('ACCUMULATION_DAY', False)
                is_ema = latest.get('EMA_SPEED_ALIGNED', False)
                is_sar = latest.get('SAR_ALIGNED', False)
                is_adx = latest.get('ADX_STRONG', False)
                buy_alert = latest.get('VSTOP_BUY_SIGNAL', False)
                sell_alert = latest.get('VSTOP_SELL_SIGNAL', False)
                
                trend_state = "🟢 BULLISH" if latest.get('VSTOP_TREND', 1) == 1 else "🔴 BEARISH"
                rs_score = latest.get('RS_SCORE', 0.0)
                adx_val = latest.get('ADX', 0.0)

                # TRIGGER FILTER: Reports if VSTOP flips or if there's institutional volume
                SHOULD_REPORT = is_pocket or is_accum or buy_alert or sell_alert
                
                if SHOULD_REPORT:
                    # Explicit timeframe formatting applied to the header block
                    report = f"• *{ticker} ({timeframe_label})* | Trend Matrix: `{trend_state}`\n"
                    report += f"    ├── 📊 RS Score: `{rs_score:+.2f}%` vs SPY\n"
                    report += f"    ├── ⚡️ Pocket Pivot Matrix: {'✅ TRIGGERED' if is_pocket else '❌ No Surge'}\n"
                    report += f"    ├── 📈 Vol Accumulation Day: {'✅ DETECTED' if is_accum else '❌ Normal Vol'}\n"
                    report += f"    ├── 🚀 Speed EMAs (10 > 30): {'✅ BULLISH' if is_ema else '❌ BEARISH'}\n"
                    report += f"    ├── 🎯 Parabolic SAR Support: {'✅ ABOVE SAR' if is_sar else '❌ BELOW SAR'}\n"
                    report += f"    └── 🌊 Trend Strength (ADX): `{adx_val:.1f}` {'🔥 (Strong)' if is_adx else '⏳ (Weak)'}\n"
                    
                    if buy_alert:
                        report += f"    ⚠️ *ALERT: VOLATILITY STOP FLIPPED GREEN (BUY SIGNAL)* 🚀\n"
                    elif sell_alert:
                        report += f"    ⚠️ *ALERT: VOLATILITY STOP FLIPPED RED (SELL SIGNAL)* 🚨\n"
                    
                    triggered_reports.append(report)
                    
            except Exception as e:
                print(f"❌ Error on {ticker} ({timeframe_label}): {e}")
            
    # --- SMART TELEGRAM CHUNKING ENGINE ---
    if triggered_reports:
        print(f"📤 Transmitting alerts to Telegram...")
        current_chunk = "🎯 *MULTI-TIMEFRAME SCORECARD* 🎯\n\n"
        
        for report in triggered_reports:
            if len(current_chunk) + len(report) > 3500:
                send_telegram_alert(current_chunk)
                current_chunk = "🎯 *MULTI-TIMEFRAME SCORECARD (CONT)* 🎯\n\n"
            current_chunk += report + "\n"
        
        if current_chunk:
            send_telegram_alert(current_chunk)
        print("🚀 Success! All updates delivered.")
    else:
        print("Scan finished. Zero actionable multi-timeframe alerts found.")

if __name__ == "__main__":
    run_daily_scan()