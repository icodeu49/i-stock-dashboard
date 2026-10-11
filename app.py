# app.py
import streamlit as st
import yfinance as yf
import pandas as pd
import json
import os

# Import the technical engine out of the helper file
from helpers import calculate_technicals
from portfolio_backtest import run_backtest_simulation
from market_data import download_prices as _download_prices
from storage import WATCHLIST_FILE, write_json, load_watchlist, read_json, BASE_DIR
from market_data import last_completed_session
from asset_profiles import asset_profile
from risk_policy import extension_warning, extension_pct

@st.cache_data(ttl=3600)
def download_prices(ticker):
    return _download_prices(ticker)

@st.cache_data(ttl=3600)
def risk_profile(ticker):
    profiles = read_json(BASE_DIR / 'asset_profiles.json', {})
    return asset_profile(ticker, last_completed_session().date().isoformat(), profiles)

# --- CONSTANTS & CONFIG ---


st.set_page_config(page_title="Institutional TA Dashboard", layout="wide")
st.title("📈 Institutional Breakout & Trend Alignment Dashboard")

# --- HANDLING QUERY PARAMS FOR TAB LINKING ---
query_params = st.query_params
target_ticker = query_params.get("ticker", None)

# --- DATA PERSISTENCE LAYER ---
def load_watchlist_config():
    try:
        return load_watchlist(WATCHLIST_FILE)
    except Exception as exc:
        st.error(f"Watchlist unavailable: {exc}")
        st.stop()

def save_watchlist_config(config_dict):
    write_json(WATCHLIST_FILE, config_dict)

if "watchlist_config" not in st.session_state:
    st.session_state.watchlist_config = load_watchlist_config()

st.session_state.tickers = list(st.session_state.watchlist_config.keys())

def generate_summary(df):
    if df.empty or 'VSTOP_TREND' not in df.columns:
        return "Awaiting complete data arrays...", "orange"
    latest = df.iloc[-1]
    conditions = {
        "Institutional Trend Guardrail (Close > 50-DMA)": latest['Close'] > latest['MA50'] if 'MA50' in latest else False,
        "200-day trend guardrail": latest["Close"] > latest["MA200_DAILY"],
        "Institutional Volume Accumulation Trigger (+50% Spike)": latest['ACCUMULATION_DAY'],
        "Institutional Pivot Matrix (Pocket Pivot Spark)": latest['POCKET_PIVOT'],
        "VSTOP Support Structural Integrity Positive": latest['VSTOP_TREND'] == 1,
        "Parabolic Acceleration Status Positive (SAR)": latest['SAR_ALIGNED']
    }
    passed_count = sum(1 for status in conditions.values() if status)
    msg_lines = [f"{'✅' if status else '❌'} {name}" for name, status in conditions.items()]
    summary_msg = f"Score: ({passed_count}/6 Checklist Confluences Cleared) | " + " • ".join(msg_lines)
    
    if latest.get('PRICE_BREAKOUT_TRIGGERED', False): return f"🚀 CONFIRMED PRICE BREAKOUT - {summary_msg}", "green"
    if latest['BREAKOUT_TRIGGERED']: return f"📋 MOMENTUM SETUP - {summary_msg}", "green"
    elif passed_count >= 4: return f"🔥 STRONGLY BULLISH SYSTEM OVERVIEW - {summary_msg}", "green"
    elif passed_count == 3: return f"⚖️ NEUTRAL CONSOLIDATION AREA - {summary_msg}", "orange"
    else: return f"⚠️ BEARISH UNDERPERFORMANCE HAZARD ZONE - {summary_msg}", "red"

# --- SIDEBAR ---
st.sidebar.header("⚙️ Manage Watchlist")
new_ticker = st.sidebar.text_input("Add Ticker (e.g., NVDA):").upper().strip()
new_group = st.sidebar.selectbox("Asset Core Classification:", ["Small/Mid Growth", "Mega-Cap Tech"])

if st.sidebar.button("Add to List") and new_ticker:
    if new_ticker not in st.session_state.tickers:
        st.session_state.watchlist_config[new_ticker] = {"group": new_group}
        st.session_state.tickers.append(new_ticker)
        save_watchlist_config(st.session_state.watchlist_config)
        st.sidebar.success(f"Added {new_ticker} to {new_group}")
        st.rerun()

remove_ticker = st.sidebar.selectbox("Exclude/Remove Ticker:", [""] + st.session_state.tickers)
if st.sidebar.button("Remove Selected") and remove_ticker:
    st.session_state.watchlist_config.pop(remove_ticker, None)
    st.session_state.tickers.remove(remove_ticker)
    save_watchlist_config(st.session_state.watchlist_config)
    st.sidebar.error(f"Removed {remove_ticker}")
    st.rerun()

# --- FETCH BENCHMARKS ---
try:
    spy_daily = download_prices("SPY")
    spy_weekly = spy_monthly = spy_daily
except Exception as exc:
    st.error(f"Benchmark data unavailable: {exc}")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📊 Stock Dashboard", "📈 Watchlist Alerts ", "🔥 Macro Sector Heatmap", "🧪 Historical Backtester"])
initial_index = st.session_state.tickers.index(target_ticker) if target_ticker in st.session_state.tickers else 0

# TAB 1
with tab1:
    st.header("Single Ticker Deep Dive")
    col1, col2 = st.columns([1, 2])
    with col1: selected_stock = st.selectbox("Select Target Stock", st.session_state.tickers, index=initial_index, key="single_select")
    with col2: timeframe = st.selectbox("Select Interval", ["Daily", "Weekly", "Monthly"], index=1)
    
    interval_map = {"Daily": "1d", "Weekly": "1wk", "Monthly": "1mo"}
    period_map = {"Daily": "2y", "Weekly": "5y", "Monthly": "10y"}
    spy_ref = spy_daily if timeframe == "Daily" else (spy_weekly if timeframe == "Weekly" else spy_monthly)
    
    if selected_stock:
        if target_ticker and selected_stock != target_ticker: st.query_params.clear()
        raw_df = download_prices(selected_stock)
        if isinstance(raw_df.columns, pd.MultiIndex): raw_df.columns = raw_df.columns.get_level_values(0)
            
        df = calculate_technicals(raw_df.copy(), timeframe=timeframe, spy_df=spy_ref, asset_profile=risk_profile(selected_stock))
        if not df.empty:
            summary_text, color = generate_summary(df)
            warning = extension_warning(extension_pct(df.Close.iloc[-1], df.MA50.iloc[-1]))
            if warning:
                st.warning(warning)
            st.caption('VSTOP uses daily ATR(30) and consecutive daily closes. Mega-cap protection requires dated size metadata; missing data uses normal two-close stops.')
            current_category = st.session_state.watchlist_config.get(selected_stock, {}).get("group", "Unassigned Group")
            st.markdown(f"### 📋 Technical Summary Confluence Matrix ({timeframe} View) | Group: `{current_category}`")
            st.success(summary_text) if color == "green" else (st.error(summary_text) if color == "red" else st.warning(summary_text))
            
            # TradingView Embedded IFrames
            def render_tv_widget(html_payload, height=310):
                from base64 import b64encode
                data_uri = f"data:text/html;base64,{b64encode(html_payload.encode('utf-8')).decode('utf-8')}"
                return st.components.v1.iframe(src=data_uri, height=height)

            tv_interval = "D" if timeframe == "Daily" else ("W" if timeframe == "Weekly" else "M")
            render_tv_widget(f"""<body style="margin:0;background:#0e1117;"><div id="tv_m" style="height:460px;width:100%;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{selected_stock}","interval":"{tv_interval}","theme":"dark","style":"1","container_id":"tv_m"}});</script></body>""", height=480)
            
            ind_col1, ind_col2 = st.columns(2)
            with ind_col1:
                render_tv_widget(f"""<body style="margin:0;"><div id="c1" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{selected_stock}","interval":"{tv_interval}","theme":"dark","style":"1","hide_top_toolbar":true,"hide_side_toolbar":true,"studies":[ {{"id":"MAExp@tv-basicstudies","inputs":{{"length":10}}}},{{"id":"MAExp@tv-basicstudies","inputs":{{"length":30}}}} ],"container_id":"c1"}});</script></body>""")
            with ind_col2:
                render_tv_widget(f"""<body style="margin:0;"><div id="c2" style="height:300px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{selected_stock}","interval":"{tv_interval}","theme":"dark","style":"1","hide_top_toolbar":true,"hide_side_toolbar":true,"studies":["MA_Ribbon@tv-basicstudies"],"container_id":"c2"}});</script></body>""")

# TAB 2
with tab2:
    st.header("Multi-Stock Technical Screening Matrix")
    filter_group = st.selectbox("Filter Screening Channel Universe:", ["All Assets Combined"] + sorted({v.get("group", "Unassigned") for v in st.session_state.watchlist_config.values()}))
    multi_timeframe = st.radio("Screener Timeframe Target", ["Daily", "Weekly", "Monthly"], index=1, horizontal=True)
    
    m_interval = "1d" if multi_timeframe == "Daily" else ("1wk" if multi_timeframe == "Weekly" else "1mo")
    m_period = "2y" if multi_timeframe == "Daily" else ("5y" if multi_timeframe == "Weekly" else "10y")
    spy_m_ref = spy_daily if multi_timeframe == "Daily" else (spy_weekly if multi_timeframe == "Weekly" else spy_monthly)
    
    summary_data = []
    for t in st.session_state.tickers:
        grp = st.session_state.watchlist_config.get(t, {}).get("group", "Small/Mid Growth")
        if filter_group == "All Assets Combined" or grp == filter_group:
            try:
                t_df = download_prices(t)
                if isinstance(t_df.columns, pd.MultiIndex): t_df.columns = t_df.columns.get_level_values(0)
                t_df = calculate_technicals(t_df, timeframe=multi_timeframe, spy_df=spy_m_ref, asset_profile=risk_profile(t))
                if not t_df.empty:
                    latest = t_df.iloc[-1]
                    summary_msg, _ = generate_summary(t_df)
                    summary_data.append({
                        "Link View": f'<a href="?ticker={t}" target="_self">🔍 Deep Dive {t}</a>', "Ticker": t, "Category Channel": f"`{grp}`", "Last Close": round(float(latest['Close']), 2),
                        "Trend Guardrail": "🔥 Aligned" if latest['Close'] > latest['MA50'] else "❌ Below 50-DMA",
                        "RS Matrix Score": f"{latest['RS_SCORE']:+.2f}%", "Verdict": summary_msg.split("-")[0].strip()
                    })
            except Exception as exc:
                st.warning(f"{t}: analysis unavailable ({exc})")
    if summary_data:
        st.write(pd.DataFrame(summary_data).to_html(escape=False, index=False), unsafe_allow_html=True)

# TAB 3
with tab3:
    st.header("Institutional Sector Rotations Matrix Dashboard")
    if st.button("Calculate Sector Breakouts"):
        sectors_map = {"XLK": "Tech", "XLF": "Financials", "XLY": "Consumer Disc", "XLC": "Comms", "XLI": "Industrials"}
        matrix_rows = []
        for etf, name in sectors_map.items():
            df = download_prices(etf)
            if not df.empty and len(df) >= 63:
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
                roc = ((df['Close'].iloc[-1] - df['Close'].iloc[-22]) / df['Close'].iloc[-22]) * 100
                matrix_rows.append({"Sector ETF": etf, "Sector Name": name, "Momentum Score": round(float(roc), 2)})
        st.dataframe(pd.DataFrame(matrix_rows))

# TAB 4
with tab4:
    st.header("🧪 Quantitative Strategy Backtester Suite")
    lookback_years = st.selectbox("Select Strategy Horizon Timeline:", [1.0, 2.0, 3.0, 5.0], index=3)
    scenario_idx = st.selectbox("Select Backtest Condition:", ["Scenario 1: Entry on Weekly Buy | Exit on Weekly Sell", "Scenario 2: Entry on Weekly Buy | Exit on Monthly Sell"], index=0)
    parsed_idx = int(scenario_idx.split(":")[0].split(" ")[1])
    
    if st.button("🚀 Run Portfolio Simulation"):
        summary, trade_log, quarterly_df = run_backtest_simulation(st.session_state.tickers, lookback_years, scenario_idx=parsed_idx)
        if summary:
            st.metric("Total Portfolio Return", f"{summary['Total Return (%)']}%")
            st.dataframe(trade_log)
