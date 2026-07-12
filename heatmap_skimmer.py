import json
import os
import yfinance as yf
import pandas as pd

# Force clean absolute pathing relative to where the script actually sits
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")

def fetch_dynamic_growth_watchlist():
    """
    Cloud-proof momentum engine. Bypasses volatile web scraping entirely by 
    calculating trailing 1-week performance metrics directly via yfinance.
    """
    print("🔥 Initializing Cloud-Proof Momentum Watchlist Generator...")
    
    # High-conviction, high-volume growth & momentum universe pool to rotate through
    # High-conviction, high-volume growth & momentum universe pool to rotate through
    momentum_universe = [
        "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "NFLX", "AMD",
        "SMCI", "ARM", "PLTR", "PANW", "CRWD", "COIN", "MARA", "RIOT", "HOOD", "SOFI",
        "AFRM", "UPST", "AI", "PATH", "CELH", "WING", "DUOL", "CARD", "APP", "MELI",
        "SHOP", "TSM", "GEV", "NET", "OKTA", "DDOG", "SNOW", "ZS", "MDB", "TEAM",
        "SPOT", "TTD", "RBLX", "U", "AAL", "CCL", "NCLH", "RCL", "DKNG", "BABA"
    ]
    
    
    try:
        print(f"📡 Downloading market data for universe pool...")
        # Download 2 weeks of daily data to ensure we can calculate a clean trailing 1-week return
        data = yf.download(momentum_universe, period="2wk", interval="1d", progress=False, multi_level_index=False)
        
        if data.empty or 'Close' not in data:
            print("❌ Failed to pull market data feed from Yahoo Finance.")
            return False
            
        close_df = data['Close']
        
        # Calculate trailing 1-week performance matrix (Current Close vs Close 5 trading sessions ago)
        performance_dict = {}
        for ticker in momentum_universe:
            if ticker in close_df.columns:
                series = close_df[ticker].dropna()
                if len(series) >= 5:
                    current_price = series.iloc[-1]
                    prev_price = series.iloc[-5]
                    one_week_return = ((current_price - prev_price) / prev_price) * 100
                    performance_dict[ticker] = one_week_return
        
        # Sort universe by best performing assets and slice the top 25 momentum leaders
        sorted_leaders = sorted(performance_dict.items(), key=lambda item: item[1], reverse=True)
        top_tickers = [ticker for ticker, perf in sorted_leaders[:25]]
        
        print(f"📋 RAW MOMENTUM LIST GENERATED ({len(top_tickers)} items): {top_tickers}")
        
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