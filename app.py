# app.py
import streamlit as st
import yfinance as yf
import pandas as pd
import json
import os

# Import the technical engine out of the helper file
from helpers import calculate_technicals

# --- CONSTANTS & CONFIG ---
WATCHLIST_FILE = "watchlist.json"

st.set_page_config(page_title="Institutional TA Dashboard", layout="wide")
st.title("📈 Institutional Breakout & Trend Alignment Dashboard")

# --- HANDLING QUERY PARAMS FOR TAB LINKING ---
query_params = st.query_params
target_ticker = query_params.get("ticker", None)

# --- DATA PERSISTENCE LAYER ---
def load_watchlist_config():
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

if "watchlist_config" not in st.session_state:
    st.session_state.watchlist_config = load_watchlist_config()

st.session_state.tickers = list(st.session_state.watchlist_config.keys())

def run_backtest_simulation(watchlist, lookback_years, starting_capital=100000, scenario_idx=1):
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
                
                if df_w.empty or df_m.empty: continue
                    
                if isinstance(df_w.columns, pd.MultiIndex): df_w.columns = df_w.columns.get_level_values(0)
                if isinstance(df_m.columns, pd.MultiIndex): df_m.columns = df_m.columns.get_level_values(0)
                
                df_w = calculate_technicals(df_w, timeframe="Weekly", spy_df=spy_weekly)
                df_m = calculate_technicals(df_m, timeframe="Monthly", spy_df=spy_monthly)
                
                if df_w.empty or df_m.empty: continue
                
                monthly_trends = df_m['VSTOP_TREND'].reindex(df_w.index, method='ffill')
                combined_df = df_w.copy()
                combined_df['MONTHLY_VSTOP_TREND'] = monthly_trends
                
                portfolio_data[ticker] = combined_df
                all_dates.update(combined_df.index)
            except Exception:
                continue

    if not portfolio_data: return None, None, None

    start_date = pd.Timestamp.now() - pd.DateOffset(days=int(lookback_years * 365.25))
    timeline = sorted([d for d in all_dates if d >= start_date])
    
    if not timeline: return None, None, None
    
    cash = float(starting_capital)
    positions = {}  
    trade_log = []
    equity_curve = []
    
    prior_w_trends = {ticker: None for ticker in portfolio_data.keys()}
    prior_m_trends = {ticker: None for ticker in portfolio_data.keys()}
    
    for current_date in timeline:
        for ticker in list(positions.keys()):
            df = portfolio_data[ticker]
            if current_date in df.index:
                row = df.loc[current_date]
                w_trend = row.get('VSTOP_TREND', 1)
                m_trend = row.get('MONTHLY_VSTOP_TREND', 1)
                p_w, p_m = prior_w_trends[ticker], prior_m_trends[ticker]
                
                should_exit = False
                if scenario_idx in [1, 4] and p_w == 1 and w_trend == -1: should_exit = True
                elif scenario_idx in [2, 3] and p_m == 1 and m_trend == -1: should_exit = True
                    
                if should_exit:
                    current_price = float(row['Close'])
                    pos = positions.pop(ticker)
                    exit_value = pos['shares'] * current_price
                    cash += exit_value
                    pnl = exit_value - pos['cost']
                    
                    trade_log.append({
                        'Stock Ticker': ticker, 'Entry Date': pos['entry_date'].strftime('%Y-%m-%d'),
                        'Exit Date': current_date.strftime('%Y-%m-%d'), 'Capital Sizing': pos['cost'],
                        'Net PnL ($)': round(pnl, 2), 'Return (%)': round((pnl / pos['cost']) * 100, 2)
                    })
                    
        for ticker, df in portfolio_data.items():
            if current_date in df.index:
                row = df.loc[current_date]
                w_trend = row.get('VSTOP_TREND', 1)
                m_trend = row.get('MONTHLY_VSTOP_TREND', 1)
                p_w, p_m = prior_w_trends[ticker], prior_m_trends[ticker]
                
                should_buy = False
                if scenario_idx in [1, 2] and p_w == -1 and w_trend == 1: should_buy = True
                elif scenario_idx in [3, 4] and p_m == -1 and m_trend == 1: should_buy = True
                    
                if should_buy and ticker not in positions and cash >= 4000.0:
                    current_price = float(row['Close'])
                    cash -= 4000.0
                    positions[ticker] = {'shares': 4000.0 / current_price, 'cost': 4000.0, 'entry_date': current_date}
                    
                prior_w_trends[ticker] = w_trend
                prior_m_trends[ticker] = m_trend

        current_portfolio_value = cash
        for ticker, pos in positions.items():
            df = portfolio_data[ticker]
            current_portfolio_value += pos['shares'] * float(df.loc[current_date, 'Close']) if current_date in df.index else pos['cost']
                
        equity_curve.append({'Date': current_date, 'Portfolio Value': current_portfolio_value})

    equity_df = pd.DataFrame(equity_curve)
    if not equity_df.empty:
        equity_df.set_index('Date', inplace=True)
        quarterly_df = equity_df.resample('QE').last().dropna().reset_index()
        if len(quarterly_df) < 2:
            quarterly_df = equity_df.reset_index()
            quarterly_df['Date'] = quarterly_df['Date'].dt.strftime('%Y-%m-%d')
        else:
            quarterly_df['Date'] = quarterly_df['Date'].dt.strftime('%Y-Q%q')
    else:
        quarterly_df = pd.DataFrame(columns=['Date', 'Portfolio Value'])
        
    final_val = equity_curve[-1]['Portfolio Value'] if equity_curve else starting_capital
    summary = {
        'Starting Capital': starting_capital, 'Ending Value': round(final_val, 2),
        'Net Profit ($)': round(final_val - starting_capital, 2),
        'Total Return (%)': round(((final_val - starting_capital) / starting_capital) * 100, 2),
        'Total Trades Executed': len(trade_log)
    }
    return summary, pd.DataFrame(trade_log), quarterly_df

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
    
    if latest['BREAKOUT_TRIGGERED']: return f"🚀 ELITE BREAKOUT ALERT - {summary_msg}", "green"
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
spy_daily = yf.download("SPY", period="2y", interval="1d", progress=False, multi_level_index=False)
spy_weekly = yf.download("SPY", period="5y", interval="1wk", progress=False, multi_level_index=False)
spy_monthly = yf.download("SPY", period="10y", interval="1mo", progress=False, multi_level_index=False)
for b_df in [spy_daily, spy_weekly, spy_monthly]:
    if isinstance(b_df.columns, pd.MultiIndex): b_df.columns = b_df.columns.get_level_values(0)

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
        raw_df = yf.download(selected_stock, period=period_map[timeframe], interval=interval_map[timeframe], progress=False, multi_level_index=False)
        if isinstance(raw_df.columns, pd.MultiIndex): raw_df.columns = raw_df.columns.get_level_values(0)
            
        df = calculate_technicals(raw_df.copy(), timeframe=timeframe, spy_df=spy_ref)
        if not df.empty and len(df) >= 50:
            summary_text, color = generate_summary(df)
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
    filter_group = st.selectbox("Filter Screening Channel Universe:", ["All Assets Combined", "Small/Mid Growth", "Mega-Cap Tech"])
    multi_timeframe = st.radio("Screener Timeframe Target", ["Daily", "Weekly", "Monthly"], index=1, horizontal=True)
    
    m_interval = "1d" if multi_timeframe == "Daily" else ("1wk" if multi_timeframe == "Weekly" else "1mo")
    m_period = "2y" if multi_timeframe == "Daily" else ("5y" if multi_timeframe == "Weekly" else "10y")
    spy_m_ref = spy_daily if multi_timeframe == "Daily" else (spy_weekly if multi_timeframe == "Weekly" else spy_monthly)
    
    summary_data = []
    for t in st.session_state.tickers:
        grp = st.session_state.watchlist_config.get(t, {}).get("group", "Small/Mid Growth")
        if filter_group == "All Assets Combined" or grp == filter_group:
            try:
                t_df = yf.download(t, period=m_period, interval=m_interval, progress=False, multi_level_index=False)
                if isinstance(t_df.columns, pd.MultiIndex): t_df.columns = t_df.columns.get_level_values(0)
                t_df = calculate_technicals(t_df, timeframe=multi_timeframe, spy_df=spy_m_ref)
                if not t_df.empty and len(t_df) >= 50:
                    latest = t_df.iloc[-1]
                    summary_msg, _ = generate_summary(t_df)
                    summary_data.append({
                        "Link View": f'<a href="?ticker={t}" target="_self">🔍 Deep Dive {t}</a>', "Ticker": t, "Category Channel": f"`{grp}`", "Last Close": round(float(latest['Close']), 2),
                        "Trend Guardrail": "🔥 Aligned" if latest['Close'] > latest['MA50'] else "❌ Below 50MA",
                        "RS Matrix Score": f"{latest['RS_SCORE']:+.2f}%", "Verdict": summary_msg.split("-")[0].strip()
                    })
            except Exception: continue
    if summary_data:
        st.write(pd.DataFrame(summary_data).to_html(escape=False, index=False), unsafe_allow_html=True)

# TAB 3
with tab3:
    st.header("Institutional Sector Rotations Matrix Dashboard")
    if st.button("Calculate Sector Breakouts"):
        sectors_map = {"XLK": "Tech", "XLF": "Financials", "XLY": "Consumer Disc", "XLC": "Comms", "XLI": "Industrials"}
        matrix_rows = []
        for etf, name in sectors_map.items():
            df = yf.download(etf, period="6mo", interval="1d", progress=False, multi_level_index=False)
            if not df.empty and len(df) >= 63:
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
                roc = ((df['Close'].iloc[-1] - df['Close'].iloc[-21]) / df['Close'].iloc[-21]) * 100
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