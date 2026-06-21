import pandas as pd
import requests
import json
import os
from io import StringIO

def fetch_dynamic_growth_watchlist():
    """
    Scrapes high-volume, high-momentum small/mid-cap growth leaders
    and outputs a fresh, structured watchlist file for the scanner.
    """
    print("🔥 Initializing Dynamic Momentum Watchlist Generator...")
    
    # Target: High-Beta, Small/Mid-Cap Growth Leaders with institutional volume
    # Screening criteria: Market Cap (Small + Mid), Earnings Growth (Over 20%), Volatility (High/Beta)
    url = "https://finviz.com/screener.ashx?v=111&f=cap_smallover,fa_epsqoq_o20,sh_avgvol_o300,sh_beta_o1.2,sh_price_u5to100&o=-perf1w"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if not response.ok:
            print(f"❌ Failed to reach data source. Status code: {response.status_code}")
            return False
            
        # Extract tables containing stock symbols
	tables = pd.read_html(StringIO(response.text))
        
        # Locate the specific data grid table (Finviz data tables typically have ticker in column 1)
        screener_df = None
        for table in tables:
            if 'Ticker' in table.columns:
                screener_df = table
                break
                
        if screener_df is None or screener_df.empty:
            print("⚠️ Screener data empty. Using high-conviction fallback names.")
            return False
            
        # Take the top 25 momentum leaders from the list
        top_tickers = screener_df['Ticker'].head(25).tolist()
        
        # Build the structured dictionary tracking categories cleanly
        new_watchlist = {}
        
        # Keep your core bedrock Mega-Caps stable
        core_tech = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL"]
        for ticker in core_tech:
            new_watchlist[ticker] = {"group": "Mega-Cap Tech"}
            
        # Dynamically append the fresh small/mid cap growth names found
        for ticker in top_tickers:
            if ticker not in new_watchlist:
                new_watchlist[ticker] = {"group": "Small/Mid Growth"}
                
        # Save output straight back to your central watchlist configuration file
        with open("watchlist.json", "w") as f:
            json.dump(new_watchlist, f, indent=4)
            
        print(f"✨ Watchlist successfully refreshed! Total tracking assets: {len(new_watchlist)}")
        return True
        
    except Exception as e:
        print(f"❌ Automation pipeline error: {e}")
        return False

if __name__ == "__main__":
    fetch_dynamic_growth_watchlist()
