import os
import json
import requests
import yfinance as yf
import pandas as pd

# Safely import code engine from our isolated backend module
from helpers import calculate_technicals
from alpha_engine import check_vcp_contraction, check_overextension, check_market_regime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")

# 📡 TELEGRAM CREDENTIALS FROM ENVIRONMENT VARIABLES
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_report(message_text):
    """Dispatches the structured alert matrix, auto-splitting if it exceeds limits."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram Config Missing: Report printing to console only.")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Telegram limit is 4096; chunking safely at 3800 to avoid clipping text boundaries
    MAX_LENGTH = 3800 
    
    # Split by lines to prevent breaking a stock tree in half
    lines = message_text.split('\n')
    chunks = []
    current_chunk = []
    current_length = 0
    
    for line in lines:
        if current_length + len(line) + 1 > MAX_LENGTH:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += len(line) + 1
            
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    # Send each chunk sequentially
    success = True
    for idx, chunk in enumerate(chunks):
        suffix = f" (Part {idx+1}/{len(chunks)})" if len(chunks) > 1 else ""
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk + suffix,
            "parse_mode": "Markdown"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=12)
            if response.status_code != 200:
                print(f"❌ Telegram API Error on Chunk {idx+1}: Status {response.status_code} - {response.text}")
                success = False
            else:
                print(f"🚀 Telemetry Report Part {idx+1} transmitted successfully.")
        except Exception as e:
            print(f"❌ Network Exception during chunk dispatch: {e}")
            success = False
            
    return success

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
    
    # Pre-download SPY for relative strength computations
    spy_df = yf.download("SPY", period="max", interval="1d", progress=False, multi_level_index=False)
    if isinstance(spy_df.columns, pd.MultiIndex):
        spy_df.columns = spy_df.columns.get_level_values(0)

    macro_regime = check_market_regime(spy_df)
    print(f"🚦 Macro Market Regime: {macro_regime}")

    # ─── BULK FETCH SECTOR ETFs ───
    unique_sectors = set(info.get("sector_etf") for info in watchlist.values() if isinstance(info, dict) and info.get("sector_etf"))
    sector_dfs = {}
    if unique_sectors:
        print(f"📥 Pre-fetching {len(unique_sectors)} Sector ETFs: {', '.join(unique_sectors)}")
        for etf in unique_sectors:
            sdf = yf.download(etf, period="max", interval="1d", progress=False, multi_level_index=False)
            if isinstance(sdf.columns, pd.MultiIndex):
                sdf.columns = sdf.columns.get_level_values(0)
            sector_dfs[etf] = sdf
    # ──────────────────────────────

 
    # Dictionary to collect results by stock and timeframe
    scan_results = {}
    alert_triggers_summary = []
    
    # 2. RUN TECHNICALS MATRIX ACROSS ALL TIMEFRAMES
    for ticker in tickers:
        try:
            scan_results[ticker] = {}
            
            for tf in ["Daily", "Weekly", "Monthly"]:
                # ─── FIXED: FORCE MAXIMUM HISTORY FOR PERFECT WILDER SMOOTHING WARM-UP ───
                df_raw = yf.download(ticker, period="max", interval="1d", progress=False, multi_level_index=False)
                
                if df_raw.empty: 
                    continue
                
                # Retrieve the specific sector dataframe for this stock
                sector_etf_ticker = watchlist[ticker].get("sector_etf") if isinstance(watchlist[ticker], dict) else None
                current_sector_df = sector_dfs.get(sector_etf_ticker)

                # Pass both SPY and Sector data into the math engine
                df = calculate_technicals(df_raw, timeframe=tf, spy_df=spy_df, sector_df=current_sector_df)
                if df is None or df.empty:
                    continue
                    
                latest = df.iloc[-1]
                
                # Check for standard breakout trigger
                is_breakout = latest.get('BREAKOUT_TRIGGERED', False)
                
                # Check specifically for a fresh VSTOP breakdown turning red on macro charts
                is_vstop_sell = latest.get('VSTOP_SELL_SIGNAL', False)
                is_macro_bear_flip = (tf in ["Weekly", "Monthly"]) and is_vstop_sell
                
                # Trigger system alert if either condition is met
                is_triggered = is_breakout or is_macro_bear_flip

                # ─── THE ALPHA ENGINE HOOK ───
                # Only run heavy advanced math on the Daily chart to grade execution timing
                alpha_vcp = {"has_vcp": False}
                alpha_ext = {"is_extended": False}
                
                if tf == "Daily" and is_triggered:
                    alpha_vcp = check_vcp_contraction(df)
                    alpha_ext = check_overextension(df)
                # ─────────────────────────────
                
                scan_results[ticker][tf] = {
                    "triggered": is_triggered,
                    "is_bearish_vstop": is_macro_bear_flip,
                    "matrix": "BEARISH" if (is_macro_bear_flip or not latest.get('EMA_SPEED_ALIGNED', True)) else "BULLISH",
                    "rs_score": round(latest.get('RS_SCORE', 0.0), 2),
                    "rs_sector_score": round(latest.get('RS_SECTOR_SCORE', 0.0), 2),
                    "sector_ticker": sector_etf_ticker,
                    "pocket_pivot": latest.get('POCKET_PIVOT', False),
                    "vol_accumulation": latest.get('ACCUMULATION_DAY', False),
                    "speed_emas": latest.get('EMA_SPEED_ALIGNED', True),
                    "sar_support": latest.get('SAR_ALIGNED', True),
                    "adx": round(latest.get('ADX', 0.0), 1),
                    "vcp_setup": alpha_vcp.get("has_vcp", False),
                    "vcp_range": alpha_vcp.get("tight_range", 0.0),
                    "is_extended": alpha_ext.get("is_extended", False),
                    "dist_50ma": alpha_ext.get("dist_50", 0.0)
                }
                
                # ─── THE DISCREPANCY AUDIT LOG ────────────────────────────────────────
                if tf in ["Weekly", "Monthly"]:
                    print(f"🔍 AUDIT [{ticker} - {tf} Close]")
                    print(f"    ├── Current Close Price: ${round(latest.get('Close', 0.0), 2)}")
                    print(f"    ├── VSTOP Line Value:    ${round(latest.get('VSTOP_LINE', 0.0), 2)}")
                    print(f"    ├── VSTOP Trend State:   {latest.get('VSTOP_TREND', 0)}")
                    print(f"    └── ADX Value:           {round(latest.get('ADX', 0.0), 2)}")
                # ──────────────────────────────────────────────────────────────────────
                
                if is_triggered and ticker not in alert_triggers_summary:
                    alert_triggers_summary.append(ticker)
                    
        except Exception as e:
            print(f"⚠️ Error scanning {ticker} on {tf}: {e}")
            continue

    # 3. SAVE THE CONVERTED WATCHLIST DATABASE
    try:
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(watchlist, f, indent=4)
        print("💾 Success: watchlist.json successfully written to disk architecture.")
    except Exception as e:
        print(f"❌ Failed writing structural dictionary framework data to file: {e}")

    # 4. GROUP DATA BY TIMEFRAME CATEGORIES FOR TELEGRAM SEND
    message_blocks = [
        "🎯 **MULTI-TIMEFRAME SCORECARD** 🎯",
        f"🚦 **MACRO REGIME:** {macro_regime}\n"
    ]
    any_signals_found = False

    for tf in ["Monthly", "Weekly", "Daily"]:
        tf_block = f"\n📊 **{tf.upper()} CLOSE TRIGGERS** ════════"
        has_triggers = False
        
        for ticker, results in scan_results.items():
            if tf in results and results[tf]["triggered"]:
                has_triggers = True
                any_signals_found = True
                data = results[tf]
                
                if data["is_bearish_vstop"]:
                    tf_block += f"\n\n• 🚨 **{ticker} (BEARISH BREAKDOWN)**"
                    tf_block += f"\n    └── 📉 Volatility Stop: 🔴 FLIPPED RED (Multiplier: 2.0)"
                else:
                    # Inject Premium Alpha Engine Labels
                    if data.get("vcp_setup"):
                        tf_block += f"\n\n⭐ **ALPHA SETUP: True VCP Contraction (Tightly coiled at {data.get('vcp_range')}%)**"
                    if data.get("is_extended"):
                        tf_block += f"\n\n⚠️ **HIGH RISK: Late-Stage Breakout (+{data.get('dist_50ma')}% above 50-Day MA)**"

                    emoji = "🟢" if data["matrix"] == "BULLISH" else "🔴"
                    pivot = "✅ TRIGGERED" if data["pocket_pivot"] else "❌ No Surge"
                    vol = "✅ DETECTED" if data["vol_accumulation"] else "❌ Normal Vol"
                    ema = "✅ BULLISH" if data["speed_emas"] else "❌ BEARISH"
                    sar = "✅ ABOVE SAR" if data["sar_support"] else "❌ BELOW SAR"
                    adx_status = "🔥 (Strong)" if data["adx"] > 25 else "⏳ (Weak)"

                    tf_block += f"\n\n• **{ticker}** | Trend Matrix: {emoji} {data['matrix']}"
                    tf_block += f"\n    ├── 📊 RS vs SPY: +{data['rs_score']}%"
                    
                    if data.get('sector_ticker'):
                        tf_block += f"\n    ├── 🎯 RS vs Sector ({data['sector_ticker']}): +{data['rs_sector_score']}%"
                        
                    tf_block += f"\n    ├── ⚡️ Pocket Pivot Matrix: {pivot}"
                    tf_block += f"\n    ├── 📈 Vol Accumulation Day: {vol}"
                    tf_block += f"\n    ├── 🚀 Speed EMAs (10 > 30): {ema}"
                    tf_block += f"\n    ├── 🎯 Parabolic SAR Support: {sar}"
                    tf_block += f"\n    └── 🌊 Trend Strength (ADX): {data['adx']} {adx_status}"
        
        if not has_triggers:
            tf_block += f"\n*No active breakout or VSTOP triggers on the {tf.lower()} chart.*"
            
        message_blocks.append(tf_block)

    final_report = "\n".join(message_blocks)

    # 5. DISPATCH DATA DIRECTLY VIA LIVE PIPELINE HOOK
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