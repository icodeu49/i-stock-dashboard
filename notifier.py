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
                
                # Check if breakout engine triggered on this timeframe
                is_triggered = latest.get('BREAKOUT_TRIGGERED', False)
                
                scan_results[ticker][tf] = {
                    "triggered": is_triggered,
                    "matrix": "BULLISH" if latest.get('TREND_MATRIX', True) else "BEARISH",
                    "rs_score": round(latest.get('RS_SCORE', 0.0), 2),
                    "pocket_pivot": latest.get('POCKET_PIVOT', False),
                    "vol_accumulation": latest.get('VOL_ACCUM', False),
                    "speed_emas": latest.get('SPEED_EMAS', True),
                    "sar_support": latest.get('SAR_SUPPORT', True),
                    "adx": round(latest.get('ADX', 0.0), 1)
                }
                
                if is_triggered and ticker not in alert_triggers_summary:
                    alert_triggers_summary.append(ticker)
                    
        except Exception as e:
            print(f"⚠️ Error scanning {ticker}: {e}")
            continue

    # 3. SAVE THE CONVERTED WATCHLIST DATABASE
    try:
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(watchlist, f, indent=4)
        print("💾 Success: watchlist.json successfully written to disk architecture.")
    except Exception as e:
        print(f"❌ Failed writing structural dictionary framework data to file: {e}")

    # 4. THE UPGRADE: GROUP DATA BY TIMEFRAME FOR TELEGRAM
    message_blocks = ["🎯 **MULTI-TIMEFRAME SCORECARD** 🎯\n"]
    any_signals_found = False

    for tf in ["Monthly", "Weekly", "Daily"]:
        tf_block = f"\n📊 **{tf.upper()} CLOSE TRIGGERS** ════════"
        has_triggers = False
        
        for ticker, results in scan_results.items():
            if tf in results and results[tf]["triggered"]:
                has_triggers = True
                any_signals_found = True
                data = results[tf]
                
                emoji = "🟢" if data["matrix"] == "BULLISH" else "🔴"
                pivot = "✅ TRIGGERED" if data["pocket_pivot"] else "❌ No Surge"
                vol = "✅ DETECTED" if data["vol_accumulation"] else "❌ Normal Vol"
                ema = "✅ BULLISH" if data["speed_emas"] else "❌ BEARISH"
                sar = "✅ ABOVE SAR" if data["sar_support"] else "❌ BELOW SAR"
                adx_status = "🔥 (Strong)" if data["adx"] > 25 else "⏳ (Weak)"

                tf_block += f"\n\n• **{ticker}** | Trend Matrix: {emoji} {data['matrix']}"
                tf_block += f"\n    ├── 📊 RS Score: +{data['rs_score']}% vs SPY"
                tf_block += f"\n    ├── ⚡️ Pocket Pivot Matrix: {pivot}"
                tf_block += f"\n    ├── 📈 Vol Accumulation Day: {vol}"
                tf_block += f"\n    ├── 🚀 Speed EMAs (10 > 30): {ema}"
                tf_block += f"\n    ├── 🎯 Parabolic SAR Support: {sar}"
                tf_block += f"\n    └── 🌊 Trend Strength (ADX): {data['adx']} {adx_status}"
        
        if not has_triggers:
            tf_block += f"\n*No active breakout triggers on the {tf.lower()} chart.*"
            
        message_blocks.append(tf_block)

    final_report = "\n".join(message_blocks)

    # 5. DISPATCH DATA
    if any_signals_found:
        send_telegram_report(final_report)
    else:
        print("💡 No active triggers today across any timeframe. Skipping Telegram dispatch.")

    print("\n==========================================")
    print("🏁 AUTOMATION RUN MATRIX COMPLETED")
    print(f"Active Alert Triggers Flagged: {alert_triggers_summary}")
    print("==========================================")

if __name__ == "__main__":
    run_automated_scanner()