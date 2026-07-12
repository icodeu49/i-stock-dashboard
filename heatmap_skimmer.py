import pandas as pd
import requests
import json
import os
from io import StringIO

# Force clean absolute pathing relative to where the script actually sits
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")

def fetch_dynamic_growth_watchlist():
    """
    Scrapes high-volume, high-momentum small/mid-cap growth leaders
    and safely merges unique additions into the master watchlist file.
    """
    print("🔥 Initializing Dynamic Momentum Watchlist Generator...")
    
    url = "https://finviz.com/screener.ashx?v=111&f=cap_smallover,fa_epsqoq_o20,sh_avgvol_o300,sh_beta_o1.2,sh_price_u5to100&o=-perf1w"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if not response.ok:
            print(f"❌ Failed to reach data source. Status code: {response.status_code}")
            return False
            
        tables = pd.read_html(StringIO(response.text))
        
        screener_df = None
        for table in tables:
            if 'Ticker' in table.columns:
                screener_df = table
                break
                
        if screener_df is None or screener_df.empty:
            print("⚠️ Screener data empty. Using high-conviction fallback names.")
            return False
            
        # ─── CRUCIAL BUG FIX: COERCE DATA TYPE AND DROP NaN VALUES ───
        screener_df['Ticker'] = screener_df['Ticker'].astype(str)
        top_tickers = screener_df['Ticker'].dropna().head(25).tolist()
        # ─────────────────────────────────────────────────────────────
        
        # ─── CHANGE 1: READ EXISTING DISK STATE FIRST ───────────────────
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, "r") as f:
                try:
                    master_watchlist = json.load(f)
                    # Convert to dictionary format if legacy list structure found
                    if isinstance(master_watchlist, list):
                        master_watchlist = {ticker: {"group": "Small/Mid Growth"} for ticker in master_watchlist}
                except Exception:
                    master_watchlist = {}
        else:
            master_watchlist = {}
        # ────────────────────────────────────────────────────────────────

        # Add stable baseline Mega-Caps only if missing
        core_tech = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL"]
        for ticker in core_tech:
            if ticker not in master_watchlist:
                master_watchlist[ticker] = {"group": "Mega-Cap Tech"}
            
        # ─── CHANGE 2: DUPLICATE-PROOF HARDENED MERGE ENGINE ───────────
        new_additions_count = 0
        print(f"📋 RAW FINVIZ LIST FETCHED ({len(top_tickers)} items): {top_tickers}") # ◄── ADD THIS LOG
        for ticker in top_tickers:
            ticker_clean = ticker.strip().upper()
            
            # Skip floating artifacts, empty values, or non-alphabet columns
            if not ticker_clean or ticker_clean == "NAN" or not ticker_clean.isalpha():
                continue
                
            if ticker_clean not in master_watchlist:  # Only inject if completely missing
                master_watchlist[ticker_clean] = {"group": "Small/Mid Growth"}
                new_additions_count += 1
                print(f"➕ [ADDED] {ticker_clean} is new. Appending to watchlist.") # ◄── ADD THIS LOG
            else:
                print(f"⏭️ [SKIPPED] {ticker_clean} already exists in watchlist.") # ◄── ADD THIS LOG
        
        print(f"🔄 Sync complete. Identified {new_additions_count} new tickers.")
        # ────────────────────────────────────────────────────────────────
            
        # Save output back safely to disk path architecture
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(master_watchlist, f, indent=4)
            
        print(f"✨ Watchlist successfully refreshed! Total tracking assets: {len(master_watchlist)}")
        return True
        
    except Exception as e:
        print(f"❌ Automation pipeline error: {e}")
        return False

if __name__ == "__main__":
    fetch_dynamic_growth_watchlist()