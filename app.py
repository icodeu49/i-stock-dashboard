import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os

# --- CONSTANTS & CONFIG ---
WATCHLIST_FILE = "watchlist.json"
DEFAULT_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

st.set_page_config(page_title="Institutional TA Dashboard", layout="wide")
st.title("📈 Institutional Breakout & Trend Alignment Dashboard")

# --- HANDLING QUERY PARAMS FOR TAB LINKING ---
query_params = st.query_params
target_ticker = query_params.get("ticker", None)

# --- DATA PERSISTENCE ---
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return DEFAULT_TICKERS
    return DEFAULT_TICKERS

def save_watchlist(tickers):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(tickers, f)

if "tickers" not in st.session_state:
    st.session_state.tickers = load_watchlist()

def run_backtest_simulation(watchlist, lookback_years, starting_capital=100000):
    """
    Multi-Asset Portfolio Simulation Engine:
    - Simulates a shared $100k cash pool across the entire watchlist.
    - Allocates a fixed $4,000 per asset on a weekly Bullish crossover.
    - Fully liquidates the asset position on a weekly Bearish crossover.
    - Tracks and records a historical quarterly equity performance curve.
    """
    fetch_years = lookback_years + 1
    period_str = f"{int(fetch_years)}y"
    
    # 1. Gather historical weekly trend data arrays for all assets
    portfolio_data = {}
    all_dates = set()
    
    with st.spinner("📥 Harvesting historical framework arrays for watchlist..."):
        # Download SPY benchmark once
        spy_weekly = yf.download("SPY", period=period_str, interval="1wk", progress=False, multi_level_index=False)
        if isinstance(spy_weekly.columns, pd.MultiIndex): 
            spy_weekly.columns = spy_weekly.columns.get_level_values(0)
            
        for ticker in watchlist:
            try:
                df = yf.download(ticker, period=period_str, interval="1wk", progress=False, multi_level_index=False)
                if df.empty or len(df) < 10:
                    continue
                if isinstance(df.columns, pd.MultiIndex): 
                    df.columns = df.columns.get_level_values(0)
                
                df = calculate_technicals(df, timeframe="Weekly", spy_df=spy_weekly)
                portfolio_data[ticker] = df
                all_dates.update(df.index)
            except Exception:
                continue

    if not portfolio_data:
        return None, None, None

    # Sort timeline down to strict target lookback horizon limits
    start_date = pd.Timestamp.now() - pd.DateOffset(days=int(lookback_years * 365.25))
    timeline = sorted([d for d in all_dates if d >= start_date])
    
    # 2. Initialize Simulation Ledger Matrices
    cash = float(starting_capital)
    positions = {}  # Format: { TICKER: { 'shares': X, 'cost': Y, 'entry_date': Z } }
    trade_log = []
    equity_curve = []
    
    # Store previous trend states to identify crossovers cleanly
    prior_trends = {ticker: None for ticker in portfolio_data.keys()}
    
    # 3. Step Through Time Chronologically (Week-by-Week)
    for current_date in timeline:
        # Process Exits First to free up liquid cash capital
        for ticker in list(positions.keys()):
            df = portfolio_data[ticker]
            if current_date in df.index:
                row = df.loc[current_date]
                current_trend = row.get('VSTOP_TREND', 1)
                p_trend = prior_trends[ticker]
                
                # Exit Crossover: Trend flipped from 1 to -1
                if p_trend == 1 and current_trend == -1:
                    current_price = float(row['Close'])
                    pos = positions.pop(ticker)
                    
                    exit_value = pos['shares'] * current_price
                    cash += exit_value
                    pnl = exit_value - pos['cost']
                    ret_pct = (pnl / pos['cost']) * 100
                    
                    trade_log.append({
                        'Stock Ticker': ticker,
                        'Entry Date': pos['entry_date'].strftime('%Y-%m-%d'),
                        'Exit Date': current_date.strftime('%Y-%m-%d'),
                        'Capital Sizing': pos['cost'],
                        'Net PnL ($)': round(pnl, 2),
                        'Return (%)': round(ret_pct, 2)
                    })
                    
        # Process Entries Second
        for ticker, df in portfolio_data.items():
            if current_date in df.index:
                row = df.loc[current_date]
                current_trend = row.get('VSTOP_TREND', 1)
                p_trend = prior_trends[ticker]
                
                # Entry Crossover: Trend flipped from -1 to 1
                if p_trend == -1 and current_trend == 1:
                    # Verify we aren't already holding it and have enough cash reserves
                    if ticker not in positions and cash >= 4000.0:
                        current_price = float(row['Close'])
                        cash -= 4000.0
                        positions[ticker] = {
                            'shares': 4000.0 / current_price,
                            'cost': 4000.0,
                            'entry_date': current_date
                        }
                        
                prior_trends[ticker] = current_trend

        # Calculate total portfolio liquidation value at this snapshot in time
        current_portfolio_value = cash
        for ticker, pos in positions.items():
            df = portfolio_data[ticker]
            if current_date in df.index:
                current_portfolio_value += pos['shares'] * float(df.loc[current_date, 'Close'])
            else:
                current_portfolio_value += pos['cost'] # Fallback approximation if row gap exists
                
        equity_curve.append({
            'Date': current_date,
            'Portfolio Value': current_portfolio_value
        })

    # 4. Compile and Resample Equity Curve Data Into Quarterly Intervals
    equity_df = pd.DataFrame(equity_curve)
    if not equity_df.empty:
        equity_df.set_index('Date', inplace=True)
        # Resample to Quarter-End ('QE') and take the last available value of that quarter
        quarterly_df = equity_df.resample('QE').last().dropna().reset_index()
        quarterly_df['Date'] = quarterly_df['Date'].dt.strftime('%Y-Q%q')
    else:
        quarterly_df = pd.DataFrame(columns=['Date', 'Portfolio Value'])
        
    # Summarize final terminal statistics
    final_val = equity_curve[-1]['Portfolio Value'] if equity_curve else starting_capital
    summary = {
        'Starting Capital': starting_capital,
        'Ending Value': round(final_val, 2),
        'Net Profit ($)': round(final_val - starting_capital, 2),
        'Total Return (%)': round(((final_val - starting_capital) / starting_capital) * 100, 2),
        'Total Trades Executed': len(trade_log)
    }
    
    return summary, pd.DataFrame(trade_log), quarterly_df


# --- THE INSTITUTIONAL ENGINE ---
def calculate_technicals(df, timeframe="Weekly", spy_df=None):
    """
    Master Institutional Confluence Matrix.
    Processes 10/30 EMAs, Volatility Stops, Parabolic SAR, ADX, 
    Relative Strength, Pocket Pivots, and Accumulation Engines together.
    """
    if df.empty or len(df) < 50:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # DYNAMIC LOOKBACK CONFIGURATION
    length_map = {"Daily": 30, "Weekly": 20, "Monthly": 10}
    chosen_length = length_map.get(timeframe, 20)

    # 1. Core Speed EMAs (10 vs 30)
    df['EMA10'] = df['Close'].ewm(span=10, adjust=False).mean()
    df['EMA30'] = df['Close'].ewm(span=30, adjust=False).mean()
    df['EMA_SPEED_ALIGNED'] = df['EMA10'] > df['EMA30']

    # 2. Average Directional Index (ADX via Dynamic Length Lookback)
    high_low = df['High'] - df['Low']
    high_cp = (df['High'] - df['Close'].shift(1)).abs()
    low_cp = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    df['ATR_CHOSEN'] = tr.rolling(window=chosen_length).mean()
    df['ATR14'] = tr.rolling(window=14).mean()  # Kept as legacy indicator backup if needed

    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    
    pos_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    neg_dm = ((down_move > up_move) & (down_move > 0)) * down_move
    
    atr_filled = df['ATR_CHOSEN'].replace(0, np.nan)
    di_plus = 100 * (pos_dm.rolling(window=chosen_length).mean() / atr_filled).fillna(0)
    di_minus = 100 * (neg_dm.rolling(window=chosen_length).mean() / atr_filled).fillna(0)
    
    dm_sum = di_plus + di_minus
    dm_sum = dm_sum.replace(0, np.nan)
    dx = 100 * (di_plus - di_minus).abs() / dm_sum
    df['ADX'] = dx.rolling(window=chosen_length).mean().fillna(0)
    df['ADX_STRONG'] = (df['ADX'] > 20) & (df['ADX'] > df['ADX'].shift(1))

    # 3. Parabolic SAR Generation Engine (0.02 / 0.2 Standard Setup)
    highs, lows, sar = df['High'].values, df['Low'].values, list(df['Close'][:2])
    is_long, ep, af = True, highs[0], 0.02
    for i in range(2, len(df)):
        prev_sar = sar[-1]
        if is_long:
            current_sar = prev_sar + af * (ep - prev_sar)
            current_sar = min(current_sar, lows[i-1], lows[i-2])
            if lows[i] < current_sar:
                is_long, current_sar, ep, af = False, ep, lows[i], 0.02
            else:
                if highs[i] > ep: ep, af = highs[i], min(af + 0.02, 0.2)
        else:
            current_sar = prev_sar + af * (ep - prev_sar)
            current_sar = max(current_sar, highs[i-1], highs[i-2])
            if highs[i] > current_sar:
                is_long, current_sar, ep, af = True, ep, highs[i], 0.02
            else:
                if lows[i] < ep: ep, af = lows[i], min(af + 0.02, 0.2)
        sar.append(current_sar)
    df['SAR'] = sar
    df['SAR_ALIGNED'] = df['Close'] > df['SAR']

    # 4. Institutional Pocket Pivots & Accumulation Signals
    df['MA50'] = df['Close'].rolling(window=50).mean()
    df['MA200'] = df['Close'].rolling(window=200).mean()
    df['AvgVolume50'] = df['Volume'].rolling(window=50).mean()
    df['Price_Up'] = df['Close'] > df['Close'].shift(1)
    df['Price_Down'] = df['Close'] < df['Close'].shift(1)
    
    df['ACCUMULATION_DAY'] = df['Price_Up'] & (df['Volume'] > (df['AvgVolume50'] * 1.5))
    max_down_vol_10d = (df['Volume'] * df['Price_Down'].astype(int)).rolling(window=10).max()
    df['POCKET_PIVOT'] = df['Price_Up'] & (df['Volume'] > max_down_vol_10d) & (df['Close'] > df['MA50'])

    # 5. Relative Strength (RS) Matrix vs SPY Index
    if spy_df is not None and not spy_df.empty:
        spy_ref = spy_df.copy()
        if isinstance(spy_ref.columns, pd.MultiIndex): 
            spy_ref.columns = spy_ref.columns.get_level_values(0)
            
        spy_close = spy_ref[['Close']].rename(columns={'Close': 'Close_SPY'})
        merged = df[['Close']].merge(spy_close, left_index=True, right_index=True, how='left')
        
        if not merged.empty and 'Close_SPY' in merged.columns:
            df['RS_Ratio'] = merged['Close'] / merged['Close_SPY']
            df['RS_SCORE'] = df['RS_Ratio'].pct_change(periods=min(63, len(df)-1)) * 100
        else: 
            df['RS_SCORE'] = 0.0
    else: 
        df['RS_SCORE'] = 0.0

    # 6. Advanced Dual-Direction Volatility Trailing Stop Logic (Using Dynamic ATR Lookback)
    vstop_arr, trend_arr = [], []
    current_trend, current_stop = 1, df['Close'].iloc[0] - (df['ATR_CHOSEN'].fillna(0).iloc[0] * 2.5)
    
    for i in range(len(df)):
        close_p, high_p, low_p = df['Close'].iloc[i], df['High'].iloc[i], df['Low'].iloc[i]
        atr = df['ATR_CHOSEN'].fillna(0).iloc[i]
        if current_trend == 1:
            current_stop = max(current_stop, high_p - (atr * 2.5))
            if close_p < current_stop:
                current_trend, current_stop = -1, low_p + (atr * 2.5)
        else:
            current_stop = min(current_stop, low_p + (atr * 2.5))
            if close_p > current_stop:
                current_trend, current_stop = 1, high_p - (atr * 2.5)
        vstop_arr.append(current_stop)
        trend_arr.append(current_trend)
        
    df['VSTOP_LINE'] = vstop_arr
    df['VSTOP_TREND'] = trend_arr
    df['VSTOP_BUY_SIGNAL'] = (df['VSTOP_TREND'] == 1) & (df['VSTOP_TREND'].shift(1) == -1)
    df['VSTOP_SELL_SIGNAL'] = (df['VSTOP_TREND'] == -1) & (df['VSTOP_TREND'].shift(1) == 1)

    # Master alerting gate configuration
    df['BREAKOUT_TRIGGERED'] = df['POCKET_PIVOT'] | df['ACCUMULATION_DAY'] | df['VSTOP_BUY_SIGNAL'] | df['VSTOP_SELL_SIGNAL']
    return df

# --- SCANNER VISUAL SUMMARY MATRIX GENERATOR ---
def generate_summary(df):
    if df.empty or 'VSTOP_TREND' not in df.columns:
        return "Awaiting complete data arrays...", "orange"
        
    latest = df.iloc[-1]
    
    conditions = {
        "Institutional Trend Guardrail (Close > 50MA)": latest['Close'] > latest['MA50'] if 'MA50' in latest else False,
        "Institutional Volume Accumulation Trigger (+50% Spike)": latest['ACCUMULATION_DAY'],
        "Institutional Pivot Matrix (Pocket Pivot Spark)": latest['POCKET_PIVOT'],
        "VSTOP Support Structural Integrity Positive": latest['VSTOP_TREND'] == 1,
        "Parabolic Acceleration Status Positive (SAR)": latest['SAR_ALIGNED']
    }
    
    passed_count = sum(1 for status in conditions.values() if status)
    msg_lines = [f"{'✅' if status else '❌'} {name}" for name, status in conditions.items()]
    summary_msg = f"Score: ({passed_count}/5 Checklist Confluences Cleared) | " + " • ".join(msg_lines)
    
    if latest['BREAKOUT_TRIGGERED']:
        return f"🚀 ELITE BREAKOUT ALERT - {summary_msg}", "green"
    elif passed_count >= 4:
        return f"🔥 STRONGLY BULLISH SYSTEM OVERVIEW - {summary_msg}", "green"
    elif passed_count == 3:
        return f"⚖️ NEUTRAL CONSOLIDATION AREA - {summary_msg}", "orange"
    else:
        return f"⚠️ BEARISH UNDERPERFORMANCE HAZARD ZONE - {summary_msg}", "red"


# --- SIDEBAR CONTROL PANEL ---
st.sidebar.header("⚙️ Manage Watchlist")
new_ticker = st.sidebar.text_input("Add Ticker (e.g., NVDA):").upper().strip()

if st.sidebar.button("Add to List") and new_ticker:
    if new_ticker not in st.session_state.tickers:
        st.session_state.tickers.append(new_ticker)
        save_watchlist(st.session_state.tickers)
        st.sidebar.success(f"Added {new_ticker}")
    else:
        st.sidebar.warning("Ticker already exists.")

remove_ticker = st.sidebar.selectbox("Exclude/Remove Ticker:", [""] + st.session_state.tickers)
if st.sidebar.button("Remove Selected") and remove_ticker:
    st.session_state.tickers.remove(remove_ticker)
    save_watchlist(st.session_state.tickers)
    st.sidebar.error(f"Removed {remove_ticker}")

# --- GLOBAL BENCHMARK DATA PRE-FETCH ---
spy_daily = yf.download("SPY", period="2y", interval="1d", progress=False, multi_level_index=False)
spy_weekly = yf.download("SPY", period="5y", interval="1wk", progress=False, multi_level_index=False)
spy_monthly = yf.download("SPY", period="10y", interval="1mo", progress=False, multi_level_index=False)
if isinstance(spy_daily.columns, pd.MultiIndex): spy_daily.columns = spy_daily.columns.get_level_values(0)
if isinstance(spy_weekly.columns, pd.MultiIndex): spy_weekly.columns = spy_weekly.columns.get_level_values(0)
if isinstance(spy_monthly.columns, pd.MultiIndex): spy_monthly.columns = spy_monthly.columns.get_level_values(0)

# --- NAVIGATION TABS ---
#tab1, tab2, tab3 = st.tabs(["Stock Dashboard", "Watchlist Alerts", "🔥 Macro Sector Heatmap"])
tab1, tab2, tab3, tab4 = st.tabs(["📊 Stock Dashboard", "📈 Watchlist Alerts ", "🔥 Macro Sector Heatmap", "🧪 Historical Backtester"])
initial_index = st.session_state.tickers.index(target_ticker) if target_ticker in st.session_state.tickers else 0


# ==============================================================================
# TAB 1: ONE STOCK ONLY
# ==============================================================================
with tab1:
    st.header("Single Ticker Deep Dive")
    col1, col2 = st.columns([1, 2])
    with col1:
        selected_stock = st.selectbox("Select Target Stock", st.session_state.tickers, index=initial_index, key="single_select")
    with col2:
        timeframe = st.selectbox("Select Interval", ["Daily", "Weekly", "Monthly"], index=1)
    
    interval_map = {"Daily": "1d", "Weekly": "1wk", "Monthly": "1mo"}
    period_map = {"Daily": "2y", "Weekly": "5y", "Monthly": "10y"}
    
    # Select benchmark asset mapping reference dynamically
    if timeframe == "Daily":
        spy_ref = spy_daily
    elif timeframe == "Weekly":
        spy_ref = spy_weekly
    else:
        spy_ref = spy_monthly
    
    if selected_stock:
        if target_ticker and selected_stock != target_ticker:
            st.query_params.clear()

        raw_df = yf.download(selected_stock, period=period_map[timeframe], interval=interval_map[timeframe], progress=False, multi_level_index=False)
        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)
            
        df = calculate_technicals(raw_df.copy(), timeframe=timeframe, spy_df=spy_ref)
        
        if not df.empty and len(df) >= 50:
            summary_text, color = generate_summary(df)
            st.markdown(f"### 📋 Technical Summary Confluence Matrix ({timeframe} View)")
            
            if color == "green": st.success(summary_text)
            elif color == "red": st.error(summary_text)
            else: st.warning(summary_text)
            
            st.markdown("### 📊 Master Interactive Chart")
            tv_interval = "D" if timeframe == "Daily" else ("W" if timeframe == "Weekly" else "M")
            
            def render_tv_widget(html_payload, height=310):
                from base64 import b64encode
                encoded_html = b64encode(html_payload.encode('utf-8')).decode('utf-8')
                data_uri = f"data:text/html;base64,{encoded_html}"
                return st.components.v1.iframe(src=data_uri, height=height)

            master_chart_html = f"""
            <body style="margin:0;background:#0e1117;">
              <div id="tv_master_chart" style="height:460px;width:100%;"></div>
              <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
              <script type="text/javascript">
              new TradingView.widget({{
                "autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}",
                "timezone": "Etc/UTC", "theme": "dark", "style": "1", "locale": "en",
                "enable_publishing": false, "hide_side_toolbar": false, "allow_symbol_change": false,
                "container_id": "tv_master_chart"
              }});
              </script>
            </body>
            """
            render_tv_widget(master_chart_html, height=480)

            st.markdown("### 🔍 Dedicated Institutional Checklist Windows")
            ind_col1, ind_col2 = st.columns(2)
            
            with ind_col1:
                st.caption("📈 Core Speed EMAs (10 & 30 Stack Focus)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_ema_fast" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": [
                  {{ "id": "MAExp@tv-basicstudies", "inputs": {{ "length": 10 }} }}, {{ "id": "MAExp@tv-basicstudies", "inputs": {{ "length": 30 }} }}
                ], "container_id": "tv_ema_fast"}});
                </script></body>""")

                # Map text presentation cleanly to match user selections
                vstop_len_lbl = 30 if timeframe == "Daily" else (20 if timeframe == "Weekly" else 10)
                st.caption(f"🛡️ Volatility Stop Line Matrix (Chandelier Core {vstop_len_lbl} / 2.5 Multiplier Lookbacks)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_vstop" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": [
                  {{ "id": "ChandelierExit@tv-basicstudies", "inputs": {{ "ATR Period": {vstop_len_lbl}, "Multiplier": 2.5 }} }}
                ], "container_id": "tv_vstop"}});
                </script></body>""")

                st.caption("🎯 Parabolic SAR (Step Acceleration Matrix: 0.02, 0.05, 0.2)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_sar_chart" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": [
                  {{ "id": "SAR@tv-basicstudies", "inputs": {{ "start": 0.02, "increment": 0.05, "maximum": 0.2 }} }}
                ], "container_id": "tv_sar_chart"}});
                </script></body>""")

            with ind_col2:
                st.caption("🌊 Moving Average Anchor Waves (20 / 50 / 100 / 200)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_ema_macro" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": ["MA_Ribbon@tv-basicstudies"], "container_id": "tv_ema_macro"}});
                </script></body>""")

                st.caption(f"💪 Average Directional Index (DMI Analysis Engine Frame Length: {vstop_len_lbl})")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_adx_chart" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": [
                  {{ "id": "DX@tv-basicstudies", "inputs": {{ "ADX Smoothing": {vstop_len_lbl}, "DI Length": {vstop_len_lbl} }} }}
                ], "container_id": "tv_adx_chart"}});
                </script></body>""")

                st.caption("📈 Relative Strength Index Momentum Filter (RSI 14)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_rsi_window" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": ["RSI@tv-basicstudies"], "container_id": "tv_rsi_window"}});
                </script></body>""")

            with st.expander("🔍 Audit Raw Mathematical Data Engine Metrics"):
                st.write("Below are the real-time calculations matching your requested institutional setups:")
                audit_cols = ['Close', 'Volume', 'AvgVolume50', 'ACCUMULATION_DAY', 'POCKET_PIVOT', 'RS_SCORE', 'VSTOP_LINE', 'VSTOP_TREND']
                available_cols = [c for c in audit_cols if c in df.columns]
                st.dataframe(df[available_cols].tail(5))
        else:
            st.error("Historical data footprint bounds not met for calculations. Please wait.")

# ==============================================================================
# TAB 2: MULTIPLE STOCKS DASHBOARD MATRIX
# ==============================================================================
with tab2:
    st.header("Multi-Stock Technical Screening Matrix")
    multi_timeframe = st.radio("Screener Timeframe Target", ["Daily", "Weekly", "Monthly"], index=1, horizontal=True)
    
    m_interval = "1d" if multi_timeframe == "Daily" else ("1wk" if multi_timeframe == "Weekly" else "1mo")
    m_period = "2y" if multi_timeframe == "Daily" else ("5y" if multi_timeframe == "Weekly" else "10y")
    
    if multi_timeframe == "Daily":
        spy_m_ref = spy_daily
    elif multi_timeframe == "Weekly":
        spy_m_ref = spy_weekly
    else:
        spy_m_ref = spy_monthly
        
    summary_data = []
    
    if st.session_state.tickers:
        with st.spinner("Scanning assets for Stage 2 configurations and breakout patterns..."):
            for ticker in st.session_state.tickers:
                try:
                    t_df = yf.download(ticker, period=m_period, interval=m_interval, progress=False, multi_level_index=False)
                    if isinstance(t_df.columns, pd.MultiIndex):
                        t_df.columns = t_df.columns.get_level_values(0)
                        
                    t_df = calculate_technicals(t_df, timeframe=multi_timeframe, spy_df=spy_m_ref)
                    
                    if not t_df.empty and len(t_df) >= 50:
                        latest = t_df.iloc[-1]
                        summary_msg, _ = generate_summary(t_df)
                        link_url = f"?ticker={ticker}"
                        
                        is_aligned = latest['Close'] > latest['MA50'] if 'MA50' in latest else False
                        
                        summary_data.append({
                            "Link View": f'<a href="{link_url}" target="_self">🔍 Deep Dive {ticker}</a>',
                            "Ticker": ticker,
                            "Last Close": round(float(latest['Close']), 2),
                            "Trend Guardrail (Close > 50MA)": "🔥 Aligned" if is_aligned else "❌ Below 50MA",
                            "Inst. Accumulation Active": "✅ Vol Spike" if latest['ACCUMULATION_DAY'] else "❌ Standard Vol",
                            "Pocket Pivot Triggered": "⚡ Pivot Active" if latest['POCKET_PIVOT'] else "❌ No Pivot",
                            "RS Matrix Score": f"{latest['RS_SCORE']:+.2f}%",
                            "Overall Filter Metric Verdict": summary_msg.split("-")[0].strip()
                        })
                except Exception:
                    continue
                    
        if summary_data:
            summary_df = pd.DataFrame(summary_data)
            st.write(summary_df.to_html(escape=False, index=False), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            
            breakouts = sum(1 for x in summary_data if "BREAKOUT" in x["Overall Filter Metric Verdict"])
            stage2_count = sum(1 for x in summary_data if "Aligned" in x["Trend Guardrail (Close > 50MA)"])
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Screener Assets Count", len(summary_data))
            c2.metric("Active High-Volume Breakouts Running", breakouts)
            c3.metric("Assets Holding Bullish Trend Alignment Structures", stage2_count)
    else:
        st.info("Watchlist is empty. Populate items inside the sidebar engine panel menu.")

# ====================================================================
# TAB 3: SECTOR HEATMAP
# ====================================================================
if os.environ.get("STREAMLIT_RUN_PURE") != "true":

    def get_advanced_heatmap_matrix():
        sectors_map = {
            "XLK": {"name": "Technology", "stocks": ["MSFT", "AAPL", "NVDA", "AVGO", "ORCL", "CSCO", "AMD", "QCOM", "NOW", "INTU"]},
            "XLF": {"name": "Financials", "stocks": ["JPM", "BAC", "WFC", "MS", "GS", "BRK-B", "AXP", "V", "MA", "BLK"]},
            "XLY": {"name": "Consumer Disc.", "stocks": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG"]},
            "XLC": {"name": "Communications", "stocks": ["META", "GOOGL", "NFLX", "TMUS", "DIS", "CHTR", "CMCSA", "VZ", "T", "PINS"]},
            "XLI": {"name": "Industrials", "stocks": ["CAT", "GE", "UNP", "HON", "ETN", "URI", "WM", "UPS", "DE", "LMT"]},
            "XLP": {"name": "Consumer Staples", "stocks": ["PG", "COST", "KO", "PEP", "WMT", "PM", "MDLZ", "EL", "MO", "CL"]},
            "XLV": {"name": "Healthcare", "stocks": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ISRG", "PFE", "AMGN", "REGN"]},
            "XLE": {"name": "Energy", "stocks": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "WMB", "HAL"]},
            "XLU": {"name": "Utilities", "stocks": ["NEE", "SO", "DUK", "CEG", "AEP", "SRE", "D", "FE", "EXC", "PCG"]},
            "XLRE": {"name": "Real Estate", "stocks": ["PLD", "AMT", "EQIX", "CCI", "WY", "PSA", "O", "IRM", "DLR", "AVB"]}
        }
        
        spy_df = yf.download("SPY", period="3mo", interval="1d", progress=False, multi_level_index=False)
        if isinstance(spy_df.columns, pd.MultiIndex): spy_df.columns = spy_df.columns.get_level_values(0)
        spy_roc = float(((spy_df['Close'].iloc[-1] - spy_df['Close'].iloc[-21]) / spy_df['Close'].iloc[-21]) * 100)
        
        matrix_rows = []
        
        for etf, metadata in sectors_map.items():
            try:
                df = yf.download(etf, period="6mo", interval="1d", progress=False, multi_level_index=False)
                if df.empty or len(df) < 63: continue
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
                
                roc_3m = ((df['Close'].iloc[-1] - df['Close'].iloc[-63]) / df['Close'].iloc[-63]) * 100
                roc_1m = ((df['Close'].iloc[-1] - df['Close'].iloc[-21]) / df['Close'].iloc[-21]) * 100
                roc_1w = ((df['Close'].iloc[-1] - df['Close'].iloc[-5]) / df['Close'].iloc[-5]) * 100
                raw_score = (0.40 * roc_1m) + (0.40 * roc_1w) + (0.20 * roc_3m)
                
                vol_5d = df['Volume'].iloc[-5:].mean()
                vol_50d = df['Volume'].iloc[-50:].mean()
                vol_mult = max(0.5, min(vol_5d / vol_50d if vol_50d > 0 else 1.0, 2.0))
                final_momentum_score = round(float(raw_score * vol_mult), 2)
                
                stock_rs_rankings = []
                for ticker in metadata['stocks']:
                    try:
                        stk_df = yf.download(ticker, period="3mo", interval="1d", progress=False, multi_level_index=False)
                        if stk_df.empty or len(stk_df) < 22: continue
                        if isinstance(stk_df.columns, pd.MultiIndex): stk_df.columns = stk_df.columns.get_level_values(0)
                        
                        stk_roc = ((stk_df['Close'].iloc[-1] - stk_df['Close'].iloc[-21]) / stk_df['Close'].iloc[-21]) * 100
                        rs_score = stk_roc - spy_roc
                        stock_rs_rankings.append((ticker, rs_score))
                    except Exception: continue
                
                stock_rs_rankings.sort(key=lambda x: x[1], reverse=True)
                top_5_leaders = ", ".join([item[0] for item in stock_rs_rankings[:5]])
                
                matrix_rows.append({
                    "Sector Name": metadata['name'],
                    "ETF Ticker": etf,
                    "Blended Momentum Score": final_momentum_score,
                    "1-Week Absolute %": round(float(roc_1w), 2),
                    "1-Month Absolute %": round(float(roc_1m), 2),
                    "🏆 Sector Alpha Horse Leaders (Top 5)": top_5_leaders
                })
            except Exception: continue
            
        return pd.DataFrame(matrix_rows)

    with tab3:
        st.subheader("Institutional Sector Rotation Master Matrix")
        st.write("Ranked dynamically by multi-timeframe price returns and volume force parameters.")
        
        with st.spinner("Processing structural sector data maps..."):
            master_matrix_df = get_advanced_heatmap_matrix()
            
            if not master_matrix_df.empty:
                master_matrix_df = master_matrix_df.sort_values(by="Blended Momentum Score", ascending=False)
                st.dataframe(
                    master_matrix_df.style.background_gradient(
                        cmap="RdYlGn", 
                        subset=["Blended Momentum Score", "1-Week Absolute %", "1-Month Absolute %"]
                    ),
                    use_container_width=True,
                    hide_index=True
                )

with tab4:
    st.header("🧪 Portfolio Macro Framework Backtester")
    st.markdown("Simulates running your system across your entire **Watchlist Pool** over the last 5 years as a single multi-position portfolio.")
    
    # Read active watchlist directly out of your global JSON config state
    active_watchlist = WATCHLIST if 'WATCHLIST' in globals() else ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
    
    st.info(f"📋 **Active Configuration:** Backtester will run across the **{len(active_watchlist)} assets** currently saved in your system scanner watchlist using a shared $100,000 baseline cash account.")

    cc1, cc2 = st.columns(2)
    with cc1:
        bt_years = st.selectbox("Select Strategy Horizon Timeline:", options=[1.0, 2.0, 3.0, 5.0], index=3, format_func=lambda x: f"{int(x)} Years")
    with cc2:
        bt_capital = st.number_input("Starting Capital Allocation Pool ($):", value=100000, step=25000)

    if st.button("🚀 Run Portfolio Level Simulation", use_container_width=True):
        summary, trades_df, quarterly_df = run_backtest_simulation(active_watchlist, bt_years, bt_capital)
        
        if summary is None:
            st.error("Simulation failed to compile. Ensure your watchlist components contain accessible historical data rows.")
        else:
            # --- ROW 1: METRIC SCORECARDS ---
            st.subheader("📊 Portfolio Performance Scorecard")
            m1, m2, m3, m4 = st.columns(4)
            profit_color = "normal" if summary['Net Profit ($)'] >= 0 else "inverse"
            
            m1.metric("Starting Pool", f"${summary['Starting Capital']:,}")
            m2.metric("Ending Liquidation Value", f"${summary['Ending Value']:,}")
            m3.metric("Net Portfolio Return", f"${summary['Net Profit ($)']:,}", delta=f"{summary['Total Return (%)']:+.2f}%", delta_color=profit_color)
            m4.metric("Total Watchlist Trades", f"{summary['Total Trades Executed']} Trades")
            
            # --- ROW 2: QUARTERLY EQUITY CURVE LINE CHART ---
            st.subheader("📈 Total Portfolio Equity Curve (Quarterly Sampling)")
            if not quarterly_df.empty:
                # Set 'Date' as the index for a clean Streamlit chart display
                chart_data = quarterly_df.set_index('Date')
                st.line_chart(chart_data['Portfolio Value'], y_label="Account Value ($)", x_label="Fiscal Quarter Horizon")
            else:
                st.warning("Insufficient historic intervals to format a timeline equity curve chart.")

            # --- ROW 3: ALL TRADES LEDGER TABLE ---
            st.subheader("📜 System Entry & Exit Ledger Logs")
            if not trades_df.empty:
                # Sort trades chronologically by execution exit dates
                trades_df = trades_df.sort_values(by='Exit Date', ascending=False)
                
                # Render beautiful interactive spreadsheet table complete with background gradients
                st.dataframe(
                    trades_df.style.background_gradient(subset=['Net PnL ($)', 'Return (%)'], cmap='RdYlGn', vmin=-400, vmax=400),
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info("Zero strategy cross-over triggers occurred across the watchlist assets during this testing timeframe.")
