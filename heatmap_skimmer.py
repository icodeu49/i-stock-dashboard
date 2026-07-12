import json
import os
import requests
import pandas as pd
from io import StringIO

# Force clean absolute pathing relative to where the script actually sits
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")

def fetch_dynamic_growth_watchlist():
    """
    Ingests high-volume, high-momentum leaders by directly pulling the clean 
    Finviz CSV export endpoint, bypassing volatile HTML table scrapes entirely.
    """
    print("🔥 Initializing Dynamic Momentum Watchlist Generator...")
    
    # Target the official raw CSV data export link with your exact momentum filters
    url = "https://finviz.com/export.ashx?v=111&f=cap_smallover,fa_epsqoq_o20,sh_avgvol_o300,sh_beta_o1.2,sh_price_u5to100&o=-perf1w"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if not response.ok:
            print(f"❌ Failed to reach data export link. Status code: {response.status_code}")
            return False
            
        # Parse the clean comma-separated text table directly into a DataFrame
        csv_data = StringIO(response.text)
        df = pd.read_csv(csv_data)
        
        if df.empty or 'Ticker' not in df.columns:
            print("⚠️ Export payload empty or missing Ticker index column.")
            return False
            
        # Clean values and grab top 25 records cleanly
        df['Ticker'] = df['Ticker'].astype(str)
        top_tickers = [t.strip().upper() for t in df['Ticker'].dropna().tolist() if t.strip()]
        top_tickers = top_tickers[:25]
        
        print(f"📋 RAW FINVIZ LIST FETCHED ({len(top_tickers)} items): {top_tickers}")
        
        # ─── READ EXISTING DISK STATE ───────────────────
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, "r") as f:
                try:
                    master_watchlist = json.load(f)
                    if isinstance(master_watchlist, list):
                        master_watchlist = {ticker: {"group": "Small/Mid Growth"} for ticker in master_watchlist}
                except Exception:
                    master_watchlist = {}
        else:
            master_watchlist = {}

        # Add stable baseline Mega-Caps only if missing
        core_tech = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL"]
        for ticker in core_tech:
            if ticker not in master_watchlist:
                master_watchlist[ticker] = {"group": "Mega-Cap Tech"}
            
        # ─── DUPLICATE-PROOF HARDENED MERGE ENGINE ───────────
        new_additions_count = 0
        for ticker_clean in top_tickers:
            if not ticker_clean or ticker_clean == "NAN" or not ticker_clean.isalpha():
                continue
                
            if ticker_clean not in master_watchlist:  
                master_watchlist[ticker_clean] = {"group": "Small/Mid Growth"}
                new_additions_count += 1
                print(f"➕ [ADDED] {ticker_clean} is new. Appending to watchlist.")
            else:
                print(f"⏭️ [SKIPPED] {ticker_clean} already exists in watchlist.")
        
        print(f"🔄 Sync complete. Identified {new_additions_count} new tickers.")
            
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