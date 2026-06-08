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
    print("📥 Step 1: Downloading global SPY index benchmark details...")
    try:
        spy_df = yf.download("SPY", period="1y", interval="1d", progress=False, timeout=15, multi_level_index=False)
        if isinstance(spy_df.columns, pd.MultiIndex):
            spy_df.columns = spy_df.columns.get_level_values(0)
        print(f"✅ SPY downloaded successfully. Total historical data rows: {len(spy_df)}")
    except Exception as e:
        print(f"❌ Failed downloading SPY reference baseline matrix: {e}")
        spy_df = None

    triggered_reports = []
    
    for ticker in WATCHLIST:
        print(f"🔍 Step 2: Fetching data frames for target asset: {ticker}...")
        try:
            df = yf.download(ticker, period="1y", interval="1d", progress=False, timeout=15, threads=False, multi_level_index=False)
            if df.empty: 
                print(f"⚠️ Warning: Asset {ticker} returned empty dataset.")
                continue
            
            if isinstance(df.columns, pd.MultiIndex): 
                df.columns = df.columns.get_level_values(0)
            
            print(f"🧮 Step 3: Crunching calculations engine matrix for {ticker}...")
            df = calculate_technicals(df, spy_df=spy_df)
            
            if df.empty or len(df) < 50:
                print(f"⚠️ Warning: Dataframe for {ticker} has insufficient rows ({len(df)}) after metrics run.")
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

            # FORCED FORGE LIVE MESSAGING OVERRIDE SWITCH:
            # For testing, we leave this as True. Once confirmed, you can toggle back to filtering breakouts.
            #SHOULD_REPORT = True
            SHOULD_REPORT = is_pocket or is_accum or buy_alert or sell_alert

            if SHOULD_REPORT:
                print(f"📝 Step 4: Compiling metric scorecard string block for {ticker}...")
                report = f"• *{ticker}* | Trend Matrix: `{trend_state}`\n"
                report += f"    ├── 📊 RS Score: `{rs_score:+.2f}%` vs SPY\n"
                report += f"    ├── ⚡ Pocket Pivot Matrix: {'✅ TRIGGERED' if is_pocket else '❌ No Surge'}\n"
                report += f"    ├── 📈 Vol Accumulation Day: {'✅ DETECTED' if is_accum else '❌ Normal Vol'}\n"
                report += f"    ├── 🚀 Speed EMAs (10 > 30): {'✅ BULLISH' if is_ema else '❌ BEARISH'}\n"
                report += f"    ├── 🎯 Parabolic SAR Support: {'✅ ABOVE SAR' if is_sar else '❌ BELOW SAR'}\n"
                report += f"    └── 🌊 Trend Strength (ADX): `{adx_val:.1f}` {'🔥 (Strong)' if is_adx else '⏳ (Weak)'}\n"
                
                if buy_alert:
                    report += f"    ⚠️ *ALERT: TRAILING VOLATILITY STOP FLIPPED GREEN (BUY)* 🚀\n"
                elif sell_alert:
                    report += f"    ⚠️ *ALERT: TRAILING VOLATILITY STOP FLIPPED RED (SELL)* 🚨\n"
                
                triggered_reports.append(report)
                
        except Exception as e:
            print(f"❌ Error while running metrics calculations loop for {ticker}: {e}")
            
    # --- SMART TELEGRAM CHUNKING ENGINE ---
    if triggered_reports:
        print(f"📤 Step 5: Transmitting payloads directly to your Telegram Chat ID...")
        
        current_chunk = "🎯 *INSTITUTIONAL METRIC SCORECARD* 🎯\n\n"
        
        for report in triggered_reports:
            # If adding this report exceeds 3500 characters, send the current chunk and start a new one
            if len(current_chunk) + len(report) > 3500:
                send_telegram_alert(current_chunk)
                current_chunk = "🎯 *INSTITUTIONAL METRIC SCORECARD (CONTINUED)* 🎯\n\n"
            
            current_chunk += report + "\n"
        
        # Send any remaining reports left in the final chunk
        if current_chunk:
            send_telegram_alert(current_chunk)
            
        print("🚀 Success! All Telegram payload chunks delivered cleanly.")
    else:
        print("Scan finished. Zero reports matched formatting criteria loops.")

if __name__ == "__main__":
    run_daily_scan()