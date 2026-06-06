import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os

# --- CONSTANTS & CONFIG ---
WATCHLIST_FILE = "watchlist.json"
DEFAULT_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

st.set_page_config(page_title="Technical Analysis Dashboard", layout="wide")
st.title("📈 Multi-Timeframe Stock Technical Dashboard")

# --- DATA PERSISTENCE ---
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_TICKERS

def save_watchlist(tickers):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(tickers, f)

if "tickers" not in st.session_state:
    st.session_state.tickers = load_watchlist()

# --- CUSTOM TECHNICAL CALCULATIONS ---
def calculate_technicals(df, spy_df=None):
    if df.empty or len(df) < 50:
        return df

    # 1. EMAs (10, 20, 30, 50)
    df['EMA10'] = df['Close'].ewm(span=10, adjust=False).mean()
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA30'] = df['Close'].ewm(span=30, adjust=False).mean()
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
    
    # 2. ADX (Average Directional Index) Calculation
    plus_dm = df['High'].diff()
    minus_dm = df['Low'].diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    minus_dm = abs(minus_dm)
    
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.rolling(window=14).mean()
    plus_di = 100 * (plus_dm.rolling(window=14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=14).mean() / atr)
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9))
    df['ADX'] = dx.rolling(window=14).mean()

    # 3. VSTOP (Volatility Stop - Trail Multiplier based on ATR)
    mult = 2.5
    df['TR'] = tr
    df['ATR'] = atr
    vstop = np.zeros(len(df))
    trend = np.ones(len(df)) # 1 for up, -1 for down
    
    for i in range(1, len(df)):
        close_val = float(df['Close'].iloc[i])
        prev_vstop = vstop[i-1]
        prev_trend = trend[i-1]
        atr_val = float(df['ATR'].iloc[i]) if not pd.isna(df['ATR'].iloc[i]) else 0.0
        
        if prev_trend == 1:
            stop_level = close_val - (mult * atr_val)
            vstop[i] = max(prev_vstop, stop_level)
            if close_val < vstop[i]:
                trend[i] = -1
                vstop[i] = close_val + (mult * atr_val)
            else:
                trend[i] = 1
        else:
            stop_level = close_val + (mult * atr_val)
            vstop[i] = min(prev_vstop, stop_level) if prev_vstop != 0 else stop_level
            if close_val > vstop[i]:
                trend[i] = 1
                vstop[i] = close_val - (mult * atr_val)
            else:
                trend[i] = -1
                
    df['VSTOP'] = vstop
    df['VSTOP_Trend'] = trend # 1 implies positive structural tracking

    # 4. Relative Strength (RS vs SPY Benchmark)
    if spy_df is not None and not spy_df.empty:
        merged = df[['Close']].join(spy_df[['Close']], rsuffix='_spy')
        # Standardized baseline score mapped to a 0-100 threshold range
        rs_ratio = merged['Close'] / merged['Close_spy']
        df['RS_Score'] = (rs_ratio / rs_ratio.rolling(window=50).mean()) * 50
    else:
        df['RS_Score'] = 55.0 # Fallback buffer metric if benchmark fetching is delayed

    # 5. Parabolic SAR (Simplified tracking trend script proxy)
    df['SAR_Trend'] = np.where(df['Close'] > df['Close'].rolling(window=14).min(), 1, -1)

    return df

# --- SCREENING AND CRITERIA ALERTS ENGINE ---
def generate_summary(df):
    if df.empty or 'EMA50' not in df.columns:
        return "Insufficient Historical Horizon Bounds", "orange"
    
    latest = df.iloc[-1]
    conditions = {
        "EMA Stack (10>20>30>50)": latest['EMA10'] > latest['EMA20'] > latest['EMA30'] > latest['EMA50'],
        "VSTOP Is Positive": latest['VSTOP_Trend'] == 1,
        "ADX Trend Strength > 20": latest['ADX'] > 20 if not pd.isna(latest['ADX']) else False,
        "Relative Strength > 50": latest['RS_Score'] > 50 if not pd.isna(latest['RS_Score']) else True,
        "Parabolic SAR Uptrending": latest['SAR_Trend'] == 1
    }
    
    passed_count = sum(1 for status in conditions.values() if status)
    
    # Message formatting 
    msg_lines = [f"{'✅' if status else '❌'} {name}" for name, status in conditions.items()]
    summary_msg = f"Score: ({passed_count}/5 Active Confluences) | " + " • ".join(msg_lines)
    
    if passed_count >= 4:
        return f"🔥 STRONGLY BULLISH STRUCTURE - {summary_msg}", "green"
    elif passed_count == 3:
        return f"⚖️ NEUTRAL CONSOLIDATION - {summary_msg}", "orange"
    else:
        return f"⚠️ BEARISH / WEAK STRUCTURE - {summary_msg}", "red"

# --- SIDEBAR CONTROL PANEL ---
st.sidebar.header("⚙️ Manage Watchlist")
new_ticker = st.sidebar.text_input("Add Ticker (e.g., NVDA, AMD):").upper().strip()

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
    st.sidebar.button("Refresh Workspace To Confirm")

# --- NAVIGATION TABS ---
tab1, tab2 = st.tabs(["🎯 Single Stock Analysis", "📊 Multi-Stock Overview"])

# Download Index data once up-front to speed up calculation loops
spy_weekly = yf.download("SPY", period="2y", interval="1wk", progress=False)
spy_monthly = yf.download("SPY", period="5y", interval="1mo", progress=False)

if isinstance(spy_weekly.columns, pd.MultiIndex): spy_weekly.columns = spy_weekly.columns.get_level_values(0)
if isinstance(spy_monthly.columns, pd.MultiIndex): spy_monthly.columns = spy_monthly.columns.get_level_values(0)

# ==============================================================================
# TAB 1: ONE STOCK ONLY (TradingView Integrated Frame)
# ==============================================================================
with tab1:
    st.header("Single Ticker Deep Dive")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        selected_stock = st.selectbox("Select Target Stock", st.session_state.tickers, key="single_select")
    with col2:
        timeframe = st.selectbox("Select Interval", ["Weekly", "Monthly"])
    
    interval_map = {"Weekly": "1wk", "Monthly": "1mo"}
    period_map = {"Weekly": "2y", "Monthly": "5y"}
    spy_ref = spy_weekly if timeframe == "Weekly" else spy_monthly
    
    if selected_stock:
        # Fetching processing bounds
        raw_df = yf.download(selected_stock, period=period_map[timeframe], interval=interval_map[timeframe], progress=False)
        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)
            
        df = calculate_technicals(raw_df.copy(), spy_df=spy_ref)
        
        if not df.empty:
            # Native Streamlit color status card alerts
            summary_text, color = generate_summary(df)
            st.markdown(f"### 📋 Technical Summary Confluence Matrix ({timeframe} View)")
            
            if color == "green":
                st.success(summary_text)
            elif color == "red":
                st.error(summary_text)
            else:
                st.warning(summary_text)
            
            # TradingView Advanced Chart Integration Module
            st.markdown("### 📊 Live Interactive TradingView Chart")
            tv_interval = "W" if timeframe == "Weekly" else "M"
            
            tv_widget_html = f"""
            <div class="tradingview-widget-container" style="height:620px;width:100%;">
              <div id="tradingview_advanced_chart" style="height:580px;"></div>
              <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
              <script type="text/javascript">
              new TradingView.widget({{
                "autosize": true,
                "symbol": "{selected_stock}",
                "interval": "{tv_interval}",
                "timezone": "Etc/UTC",
                "theme": "dark",
                "style": "1",
                "locale": "en",
                "enable_publishing": false,
                "hide_side_toolbar": false,
                "allow_symbol_change": true,
                "studies": [
                  "MAExp@tv-basicstudies",
                  "RSI@tv-basicstudies",
                  "SAR@tv-basicstudies",
                  "DX@tv-basicstudies"
                ],
                "container_id": "tradingview_advanced_chart"
              }});
              </script>
            </div>
            """
            import streamlit.components.v1 as components
            components.html(tv_widget_html, height=600)
        else:
            st.error("No data engine records found for target.")

# ==============================================================================
# TAB 2: MULTIPLE STOCKS DASHBOARD MATRIX
# ==============================================================================
with tab2:
    st.header("Multi-Stock Technical Screening Matrix")
    multi_timeframe = st.radio("Screener Timeframe Target", ["Weekly", "Monthly"], horizontal=True)
    
    m_interval = "1wk" if multi_timeframe == "Weekly" else "1mo"
    m_period = "2y" if multi_timeframe == "Weekly" else "5y"
    spy_m_ref = spy_weekly if multi_timeframe == "Weekly" else spy_monthly
    
    summary_data = []
    
    if st.session_state.tickers:
        with st.spinner("Scanning system indicators across watchlist portfolio matrix..."):
            for ticker in st.session_state.tickers:
                try:
                    t_df = yf.download(ticker, period=m_period, interval=m_interval, progress=False)
                    if isinstance(t_df.columns, pd.MultiIndex):
                        t_df.columns = t_df.columns.get_level_values(0)
                        
                    t_df = calculate_technicals(t_df, spy_df=spy_m_ref)
                    
                    if not t_df.empty:
                        latest = t_df.iloc[-1]
                        summary_msg, _ = generate_summary(t_df)
                        
                        summary_data.append({
                            "Ticker": ticker,
                            "Last Close": round(float(latest['Close']), 2),
                            "ADX (14)": round(float(latest['ADX']), 1) if not pd.isna(latest['ADX']) else "N/A",
                            "RS Score": round(float(latest['RS_Score']), 1) if not pd.isna(latest['RS_Score']) else "N/A",
                            "VSTOP State": "🟢 Positive" if latest['VSTOP_Trend'] == 1 else "🔴 Negative",
                            "SAR State": "🟢 Up" if latest['SAR_Trend'] == 1 else "🔴 Down",
                            "Overall Filter Metric Verdict": summary_msg.split("-")[0].strip()
                        })
                except Exception:
                    continue
                    
        if summary_data:
            summary_df = pd.DataFrame(summary_data)
            st.dataframe(summary_df, use_container_width=True, hide_index=True)
            
            bulls = sum(1 for x in summary_data if "BULLISH" in x["Overall Filter Metric Verdict"])
            bears = sum(1 for x in summary_data if "BEARISH" in x["Overall Filter Metric Verdict"])
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Screener Assets Count", len(summary_data))
            c2.metric("Conforming Setup High-Scorers (Bullish)", bulls)
            c3.metric("Failing Structural Profiles (Bearish)", bears)
    else:
        st.info("Watchlist array is empty. Inject tickers via the sidebar to populate data matrix maps.")
