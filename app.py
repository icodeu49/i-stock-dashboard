streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import sys

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
            return json.load(f)
    return DEFAULT_TICKERS

def save_watchlist(tickers):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(tickers, f)

if __name__ == "__main__" or "streamlit" in sys.modules:
    if "tickers" not in st.session_state:
        st.session_state.tickers = load_watchlist()

# --- THE INSTITUTIONAL ENGINE ---
def calculate_technicals(df, spy_df=None):
    """
    Master Institutional Confluence Matrix.
    Processes 10/30 EMAs, Volatility Stops, Parabolic SAR, ADX, 
    Relative Strength, Pocket Pivots, and Accumulation Engines together.
    """
    if df.empty or len(df) < 50:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 1. Core Speed EMAs (10 vs 30)
    df['EMA10'] = df['Close'].ewm(span=10, adjust=False).mean()
    df['EMA30'] = df['Close'].ewm(span=30, adjust=False).mean()
    df['EMA_SPEED_ALIGNED'] = df['EMA10'] > df['EMA30']

    # 2. Average Directional Index (ADX 14)
    high_low = df['High'] - df['Low']
    high_cp = (df['High'] - df['Close'].shift(1)).abs()
    low_cp = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(window=14).mean()

    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    
    pos_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    neg_dm = ((down_move > up_move) & (down_move > 0)) * down_move
    
    di_plus = 100 * (pos_dm.rolling(window=14).mean() / df['ATR14'])
    di_minus = 100 * (neg_dm.rolling(window=14).mean() / df['ATR14'])
    
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
    df['ADX'] = dx.rolling(window=14).mean()
    # Trend is strong if ADX is above 20 and rising
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
# 5. Relative Strength (RS) Matrix vs SPY Index (FIXED COLUMN STRIPPING)
    if spy_df is not None and not spy_df.empty:
        # Copy to avoid modifying original dataframe out of scope
        spy_ref = spy_df.copy()
        if isinstance(spy_ref.columns, pd.MultiIndex): 
            spy_ref.columns = spy_ref.columns.get_level_values(0)
            
        # Select strictly the 'Close' column series cleanly
        spy_close = spy_ref[['Close']].rename(columns={'Close': 'Close_SPY'})
        
        merged = df[['Close']].merge(spy_close, left_index=True, right_index=True, how='left')
        if not merged.empty and 'Close_SPY' in merged.columns:
            df['RS_Ratio'] = merged['Close'] / merged['Close_SPY']
            df['RS_SCORE'] = df['RS_Ratio'].pct_change(periods=min(63, len(df)-1)) * 100
        else: 
            df['RS_SCORE'] = 0.0
    else: 
        df['RS_SCORE'] = 0.0

    # 6. Advanced Dual-Direction Volatility Trailing Stop Logic (3x ATR Window)
    vstop_arr, trend_arr = [], []
    current_trend, current_stop = 1, df['Close'].iloc[0] - (df['ATR14'].fillna(0).iloc[0] * 3)
    
    for i in range(len(df)):
        close_p, high_p, low_p = df['Close'].iloc[i], df['High'].iloc[i], df['Low'].iloc[i]
        atr = df['ATR14'].fillna(0).iloc[i]
        if current_trend == 1:
            current_stop = max(current_stop, high_p - (atr * 3))
            if close_p < current_stop:
                current_trend, current_stop = -1, low_p + (atr * 3)
        else:
            current_stop = min(current_stop, low_p + (atr * 3))
            if close_p > current_stop:
                current_trend, current_stop = 1, high_p - (atr * 3)
        vstop_arr.append(current_stop)
        trend_arr.append(current_trend)
        
    df['VSTOP_LINE'] = vstop_arr
    df['VSTOP_TREND'] = trend_arr
    df['VSTOP_BUY_SIGNAL'] = (df['VSTOP_TREND'] == 1) & (df['VSTOP_TREND'].shift(1) == -1)
    df['VSTOP_SELL_SIGNAL'] = (df['VSTOP_TREND'] == -1) & (df['VSTOP_TREND'].shift(1) == 1)

    # Master alerting gate configuration
    df['BREAKOUT_TRIGGERED'] = df['POCKET_PIVOT'] | df['ACCUMULATION_DAY'] | df['VSTOP_BUY_SIGNAL'] | df['VSTOP_SELL_SIGNAL']
    return df

# --- SCANNER VISUAL SUMMARY MATRIX SUMMARY GENERATOR ---
def generate_summary(df):
    if df.empty or 'Stage2_Aligned' not in df.columns:
        return "Awaiting complete data arrays...", "orange"
        
    latest = df.iloc[-1]
    
    conditions = {
        "Stage 2 Trend Guardrail (Close > 50 > 150 > 200 EMA)": latest['Stage2_Aligned'],
        "Institutional Volume Accumulation Trigger (+50% Spike)": latest['Inst_Accumulation'],
        "Relative Strength Line Nearing 52-Week High Leaderships": latest['RS_New_High'],
        "VSTOP Support Structural Integrity Positive": latest['VSTOP_Trend'] == 1,
        "Parabolic Acceleration Status Positive": latest['SAR_Trend'] == 1
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

# --- GLOBAL BENCHMARK DATA PRE-FETCH (S&P 500 reference mapping required for RS indicators) ---
spy_weekly = yf.download("SPY", period="3y", interval="1wk", progress=False, multi_level_index=False)
spy_monthly = yf.download("SPY", period="6y", interval="1mo", progress=False, multi_level_index=False)
if isinstance(spy_weekly.columns, pd.MultiIndex): spy_weekly.columns = spy_weekly.columns.get_level_values(0)
if isinstance(spy_monthly.columns, pd.MultiIndex): spy_monthly.columns = spy_monthly.columns.get_level_values(0)

# --- NAVIGATION TABS ---
tab1, tab2 = st.tabs(["🎯 Single Stock Analysis", "📊 Multi-Stock Overview"])

initial_index = st.session_state.tickers.index(target_ticker) if target_ticker in st.session_state.tickers else 0

# ==============================================================================
# TAB 1: ONE STOCK ONLY (Institutional Overrides Blueprint View)
# ==============================================================================
with tab1:
    st.header("Single Ticker Deep Dive")
    col1, col2 = st.columns([1, 2])
    with col1:
        selected_stock = st.selectbox("Select Target Stock", st.session_state.tickers, index=initial_index, key="single_select")
    with col2:
        timeframe = st.selectbox("Select Interval", ["Weekly", "Monthly"])
    
    interval_map = {"Weekly": "1wk", "Monthly": "1mo"}
    period_map = {"Weekly": "3y", "Monthly": "6y"} # Expanded period range history tracking
    spy_ref = spy_weekly if timeframe == "Weekly" else spy_monthly
    
    if selected_stock:
        if target_ticker and selected_stock != target_ticker:
            st.query_params.clear()

        raw_df = yf.download(selected_stock, period=period_map[timeframe], interval=interval_map[timeframe], progress=False)
        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)
            
        df = calculate_technicals(raw_df.copy(), spy_df=spy_ref)
        
        if not df.empty and 'Stage2_Aligned' in df.columns:
            summary_text, color = generate_summary(df)
            st.markdown(f"### 📋 Technical Summary Confluence Matrix ({timeframe} View)")
            
            if color == "green": st.success(summary_text)
            elif color == "red": st.error(summary_text)
            else: st.warning(summary_text)
            
            # --- THE 6-PANE CHART MATRIX VISUAL GRID ---
# --- UPDATED NATIVE FRAME RENDERING ENGINE ---
            st.markdown("### 📊 Master Interactive Chart")
            tv_interval = "W" if timeframe == "Weekly" else "M"
            
            # Reusable frame generator utilizing the modern native iframe API
            def render_tv_widget(html_payload, height=310):
                # Encodes raw HTML code dynamically into a compliant inline data URI
                from base64 import b64encode
                encoded_html = b64encode(html_payload.encode('utf-8')).decode('utf-8')
                data_uri = f"data:text/html;base64,{encoded_html}"
                return st.iframe(src=data_uri, height=height)

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
             body>
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

                st.caption("🛡️ Volatility Stop (VSTOP Proxy - Chandelier Core 20 / 2.5 Parameters)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_vstop" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": [
                  {{ "id": "ChandelierExit@tv-basicstudies", "inputs": {{ "ATR Period": 20, "Multiplier": 2.5 }} }}
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
                st.caption("🌊 Indicator 3 Check: Stage 2 Moving Average Anchor Waves (20 / 50 / 100 / 200)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_ema_macro" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": ["MA_Ribbon@tv-basicstudies"], "container_id": "tv_ema_macro"}});
                </script></body>""")

                st.caption("💪 Average Directional Index (DMI Analysis Engine Frame Length: 14)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_adx_chart" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": [
                  {{ "id": "DX@tv-basicstudies", "inputs": {{ "ADX Smoothing": 14, "DI Length": 14 }} }}
                ], "container_id": "tv_adx_chart"}});
                </script></body>""")

                st.caption("📈 Relative Strength Index Momentum Filter (RSI 14)")
                render_tv_widget(f"""
                <body style="margin:0;"><div id="tv_rsi_window" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>
                new TradingView.widget({{"autosize": true, "symbol": "{selected_stock}", "interval": "{tv_interval}", "theme": "dark", "style": "1", "hide_top_toolbar": true, "hide_side_toolbar": true, "studies": ["RSI@tv-basicstudies"], "container_id": "tv_rsi_window"}});
                </script></body>""")

            with st.expander("🔍 Audit Raw Mathematical Data Engine Metrics"):
                st.write("Below are the real-time calculations matching your requested institutional setups:")
                audit_cols = ['Close', 'Volume', 'Vol_Avg50', 'Inst_Accumulation', 'EMA50', 'EMA200', 'Stage2_Aligned', 'RS_New_High']
                st.dataframe(df[audit_cols].tail(5))
        else:
            st.error("Historical data footprint bounds not met for calculations. Please wait.")

# ==============================================================================
# TAB 2: MULTIPLE STOCKS DASHBOARD MATRIX
# ==============================================================================
with tab2:
    st.header("Multi-Stock Technical Screening Matrix")
    multi_timeframe = st.radio("Screener Timeframe Target", ["Weekly", "Monthly"], horizontal=True)
    
    m_interval = "1wk" if multi_timeframe == "Weekly" else "1mo"
    m_period = "3y" if multi_timeframe == "Weekly" else "6y"
    spy_m_ref = spy_weekly if multi_timeframe == "Weekly" else spy_monthly
    
    summary_data = []
    
    if st.session_state.tickers:
        with st.spinner("Scanning assets for Stage 2 configurations and breakout patterns..."):
            for ticker in st.session_state.tickers:
                try:
                    t_df = yf.download(ticker, period=m_period, interval=m_interval, progress=False, multi_level_index=False)
                    if isinstance(t_df.columns, pd.MultiIndex):
                        t_df.columns = t_df.columns.get_level_values(0)
                        
                    t_df = calculate_technicals(t_df, spy_df=spy_m_ref)
                    
                    if not t_df.empty and 'Stage2_Aligned' in t_df.columns:
                        latest = t_df.iloc[-1]
                        summary_msg, _ = generate_summary(t_df)
                        link_url = f"?ticker={ticker}"
                        
                        summary_data.append({
                            "Link View": f'<a href="{link_url}" target="_self">🔍 Deep Dive {ticker}</a>',
                            "Ticker": ticker,
                            "Last Close": round(float(latest['Close']), 2),
                            "Stage 2 Structural Alignment": "🔥 Aligned" if latest['Stage2_Aligned'] else "❌ Under 200MA",
                            "Inst. Accumulation Active": "✅ Vol Spike" if latest['Inst_Accumulation'] else "❌ Standard Vol",
                            "RS Line 52W Standing": "👑 Market Leader" if latest['RS_New_High'] else "⚖️ Index Tracker",
                            "Overall Filter Metric Verdict": summary_msg.split("-")[0].strip()
                        })
                except Exception:
                    continue
                    
        if summary_data:
            summary_df = pd.DataFrame(summary_data)
            st.write(summary_df.to_html(escape=False, index=False), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            
            breakouts = sum(1 for x in summary_data if "BREAKOUT" in x["Overall Filter Metric Verdict"])
            stage2_count = sum(1 for x in summary_data if "Aligned" in x["Stage 2 Structural Alignment"])
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Screener Assets Count", len(summary_data))
            c2.metric("Active High-Volume Breakouts Running", breakouts)
            c3.metric("Assets Holding Stage 2 Alignment Structures", stage2_count)
    else:
        st.info("Watchlist is empty. Populate items inside the sidebar engine panel menu.")
