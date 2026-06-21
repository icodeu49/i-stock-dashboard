import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os

# --- CONSTANTS & CONFIG ---
WATCHLIST_FILE = "watchlist.json"

st.set_page_config(page_title="Institutional TA Dashboard", layout="wide")
st.title("📈 Institutional Breakout & Trend Alignment Dashboard")

# --- HANDLING QUERY PARAMS FOR TAB LINKING ---
query_params = st.query_params
target_ticker = query_params.get("ticker", None)

# --- DATA PERSISTENCE LAYER ---
def load_watchlist_config():
    """
    Loads the custom structured category-mapped dictionary built by heatmap_skimmer.py.
    Provides a fallback configuration if file is corrupted or not yet generated.
    """
    fallback_config = {
        "AAPL": {"group": "Mega-Cap Tech"}, "NVDA": {"group": "Mega-Cap Tech"},
        "MSFT": {"group": "Mega-Cap Tech"}, "AMZN": {"group": "Mega-Cap Tech"},
        "CELH": {"group": "Small/Mid Growth"}, "VRT": {"group": "Small/Mid Growth"},
        "ALAB": {"group": "Small/Mid Growth"}, "HOLO": {"group": "Small/Mid Growth"}
    }
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r") as f:
                config_data = json.load(f)
                if isinstance(config_data, dict) and len(config_data) > 0:
                    return config_data
                else:
                    return fallback_config
        except Exception:
            return fallback_config
    return fallback_config

def save_watchlist_config(config_dict):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(config_dict, f, indent=4)

# Synchronize application session state properties
if "watchlist_config" not in st.session_state:
    st.session_state.watchlist_config = load_watchlist_config()

# Derived flat arrays required for traditional standard data loop runs
st.session_state.tickers = list(st.session_state.watchlist_config.keys())

def run_backtest_simulation(watchlist, lookback_years, starting_capital=100000, scenario_idx=1):
    """
    Upgraded Multi-Asset Portfolio Simulation Engine:
    - Fixed: Maps Monthly trends safely onto Weekly index rows without dropping data.
    - Added: Warmup buffer expanded to prevent monthly calculation starvation.
    - Added: Fallback chart generator if resampled data arrays are tight.
    """
    fetch_years = lookback_years + 5  
    period_str = f"{int(fetch_years)}y"
    
    portfolio_data = {}
    all_dates = set()
    
    with st.spinner("📥 Harvesting historical framework arrays (Weekly & Monthly)..."):
        spy_weekly = yf.download("SPY", period=period_str, interval="1wk", progress=False, multi_level_index=False)
        spy_monthly = yf.download("SPY", period=period_str, interval="1mo", progress=False, multi_level_index=False)
        
        if isinstance(spy_weekly.columns, pd.MultiIndex): spy_weekly.columns = spy_weekly.columns.get_level_values(0)
        if isinstance(spy_monthly.columns, pd.MultiIndex): spy_monthly.columns = spy_monthly.columns.get_level_values(0)
            
        for ticker in watchlist:
            try:
                df_w = yf.download(ticker, period=period_str, interval="1wk", progress=False, multi_level_index=False)
                df_m = yf.download(ticker, period=period_str, interval="1mo", progress=False, multi_level_index=False)
                
                if df_w.empty or df_m.empty:
                    continue
                    
                if isinstance(df_w.columns, pd.MultiIndex): df_w.columns = df_w.columns.get_level_values(0)
                if isinstance(df_m.columns, pd.MultiIndex): df_m.columns = df_m.columns.get_level_values(0)
                
                df_w = calculate_technicals(df_w, timeframe="Weekly", spy_df=spy_weekly)
                df_m = calculate_technicals(df_m, timeframe="Monthly", spy_df=spy_monthly)
                
                if df_w.empty or df_m.empty:
                    continue
                
                # Reindex Monthly matrix records matching exactly to target weekly row timestamps
                monthly_trends = df_m['VSTOP_TREND'].reindex(df_w.index, method='ffill')
                
                combined_df = df_w.copy()
                combined_df['MONTHLY_VSTOP_TREND'] = monthly_trends
                
                portfolio_data[ticker] = combined_df
                all_dates.update(combined_df.index)
            except Exception:
                continue

    if not portfolio_data:
        return None, None, None

    start_date = pd.Timestamp.now() - pd.DateOffset(days=int(lookback_years * 365.25))
    timeline = sorted([d for d in all_dates if d >= start_date])
    
    if not timeline:
        return None, None, None
    
    cash = float(starting_capital)
    positions = {}  
    trade_log = []
    equity_curve = []
    
    prior_w_trends = {ticker: None for ticker in portfolio_data.keys()}
    prior_m_trends = {ticker: None for ticker in portfolio_data.keys()}
    
    for current_date in timeline:
        # --- EXITS PROCESS ---
        for ticker in list(positions.keys()):
            df = portfolio_data[ticker]
            if current_date in df.index:
                row = df.loc[current_date]
                w_trend = row.get('VSTOP_TREND', 1)
                m_trend = row.get('MONTHLY_VSTOP_TREND', 1)
                
                p_w = prior_w_trends[ticker]
                p_m = prior_m_trends[ticker]
                
                should_exit = False
                if scenario_idx in [1, 4] and p_w == 1 and w_trend == -1:
                    should_exit = True
                elif scenario_idx in [2, 3] and p_m == 1 and m_trend == -1:
                    should_exit = True