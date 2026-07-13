import streamlit as st
import pandas as pd
import yfinance as yf
import vectorbt as vbt
import plotly.graph_objects as go
import json
import os

from helpers import calculate_technicals

# --- Setup Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")

# --- Streamlit Page Configuration ---
st.set_page_config(page_title="Quant Terminal", layout="wide", page_icon="📈")
st.title("📈 Institutional Quant Terminal")

# --- Load Watchlist ---
@st.cache_data
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r") as f:
                return list(json.load(f).keys())
        except:
            pass
    return ["NVDA", "AAPL", "MSFT", "META", "SPY"]

tickers = load_watchlist()

# --- UI Sidebar Controls ---
st.sidebar.header("⚙️ Global Parameters")
years = st.sidebar.slider("Lookback Period (Years)", min_value=1, max_value=10, value=5)
stop_loss = st.sidebar.number_input("Hard Stop Loss Safety Net (%)", min_value=1.0, max_value=20.0, value=8.0, step=0.5) / 100.0

st.sidebar.markdown("---")
select_all = st.sidebar.checkbox("Select All from Watchlist")
selected_tickers = st.sidebar.multiselect("Selected Assets", tickers, default=tickers if select_all else tickers[:5])

# ==========================================
# 📑 TAB ARCHITECTURE
# ==========================================
tab1, tab2 = st.tabs(["🧪 Backtest Engine & Analytics", "🔎 Live Market Screener"])

# ------------------------------------------
# TAB 1: BACKTESTER & MACRO CHARTS
# ------------------------------------------
with tab1:
    # --- ENHANCEMENT 2: Macro Index Overlay ---
    st.subheader("🌎 Macro Market Context (SPY vs QQQ)")
    with st.expander("View Macro Charts", expanded=False):
        try:
            macro_data = yf.download(["SPY", "QQQ"], period=f"{years}y", interval="1d", progress=False)['Close']
            # Normalize to percentage return from day 1 for direct comparison
            macro_returns = (macro_data / macro_data.iloc[0] - 1) * 100
            
            fig_macro = go.Figure()
            fig_macro.add_trace(go.Scatter(x=macro_returns.index, y=macro_returns['SPY'], name="S&P 500 (SPY)", line=dict(color="blue")))
            fig_macro.add_trace(go.Scatter(x=macro_returns.index, y=macro_returns['QQQ'], name="Nasdaq (QQQ)", line=dict(color="orange")))
            fig_macro.update_layout(height=400, yaxis_title="Cumulative Return (%)", margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig_macro, use_container_width=True)
        except Exception as e:
            st.warning("Could not load macro indices.")

    st.divider()

    # --- Backtest Execution ---
    if st.button("🚀 Run Backtest Simulation", use_container_width=True):
        if not selected_tickers:
            st.warning("⚠️ Please select assets in the sidebar.")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            summary_results = []
            
            st.session_state['portfolios'] = {}
            st.session_state['technical_data'] = {}

            for i, ticker in enumerate(selected_tickers):
                status_text.text(f"Simulating {ticker} ({i+1}/{len(selected_tickers)})...")
                try:
                    df_raw = yf.download(ticker, period=f"{years}y", interval="1d", progress=False)
                    if df_raw.empty or len(df_raw) < 100: continue
                    
                    if isinstance(df_raw.columns, pd.MultiIndex):
                        df_raw.columns = df_raw.columns.get_level_values(0)
                    df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
                    df_raw.index = pd.to_datetime(df_raw.index).tz_localize(None)

                    df = calculate_technicals(df_raw, timeframe="Daily")
                    if df is None or df.empty: continue

                    pf = vbt.Portfolio.from_signals(
                        close=df['Close'], entries=df['BREAKOUT_TRIGGERED'], exits=df['VSTOP_SELL_SIGNAL'],
                        init_cash=10000, fees=0.001, sl_stop=stop_loss 
                    )
                    
                    stats = pf.stats()
                    st.session_state['portfolios'][ticker] = pf
                    st.session_state['technical_data'][ticker] = df

                    summary_results.append({
                        "Ticker": ticker,
                        "Total Return %": round(stats.get('Total Return [%]', 0), 2),
                        "Win Rate %": round(stats.get('Win Rate [%]', 0), 2) if not pd.isna(stats.get('Win Rate [%]')) else 0.0,
                        "Max Drawdown %": round(stats.get('Max Drawdown [%]', 0), 2),
                        "Total Trades": int(stats.get('Total Trades', 0)),
                        "Open PnL ($)": round(stats.get('Open Trade PnL', 0), 2) if not pd.isna(stats.get('Open Trade PnL')) else 0.0,
                    })
                except:
                    pass
                progress_bar.progress((i + 1) / len(selected_tickers))
            status_text.text("✅ Complete!")

            # --- ENHANCEMENT 1: Persistent Table ---
            if summary_results:
                summary_df = pd.DataFrame(summary_results).sort_values(by="Total Return %", ascending=False)
                st.session_state['summary_df'] = summary_df # Save to session state so it doesn't disappear!

    # Render Table if it exists in session state
    if 'summary_df' in st.session_state:
        st.subheader("🏆 Strategy Leaderboard")
        st.dataframe(
            st.session_state['summary_df'].style.format({
                "Total Return %": "{:,.2f}%", "Win Rate %": "{:.2f}%", "Max Drawdown %": "{:.2f}%", "Open PnL ($)": "${:,.2f}"
            }).background_gradient(subset=['Total Return %'], cmap='Greens')
              .background_gradient(subset=['Max Drawdown %'], cmap='Reds'),
            use_container_width=True, height=300
        )

    # Render Chart below the table
    if 'portfolios' in st.session_state and st.session_state['portfolios']:
        st.divider()
        st.subheader("📈 Deep Dive & Signal Feed")
        chart_ticker = st.selectbox("Select Asset to Visualize:", list(st.session_state['portfolios'].keys()))
        
        if chart_ticker:
            # Plot
            fig = st.session_state['portfolios'][chart_ticker].plot()
            fig.update_layout(height=600, margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)
            
            # Feed
            df_hist = st.session_state['technical_data'].get(chart_ticker)
            if df_hist is not None:
                signal_df = df_hist[df_hist['BREAKOUT_TRIGGERED'] | df_hist['VSTOP_SELL_SIGNAL']].sort_index(ascending=False)
                with st.expander("📜 View Historical Signal Feed"):
                    for ts, row in signal_df.iterrows():
                        is_bear = row.get('VSTOP_SELL_SIGNAL', False)
                        msg = f"🚨 BEARISH EXIT @ ${row['Close']:.2f}" if is_bear else f"🟢 BULLISH ENTRY @ ${row['Close']:.2f}"
                        st.write(f"**{ts.strftime('%Y-%m-%d')}** | {msg}")

# ------------------------------------------
# TAB 2: LIVE STOCK SCREENER
# ------------------------------------------
with tab2:
    st.subheader("🔎 End-of-Day Screener")
    st.markdown("Scans the current market close to find live setups based on your 4-Pillar metrics.")
    
    # Define Scan Types
    scan_type = st.radio("Select Scan Criteria:", [
        "🔥 Full VCP Breakout (Buy Signal Triggered Today)",
        "🌊 Accumulation Volume (Institutions are buying)",
        "🎯 Trend Matrix Alignment (Bullish Setup Forming)"
    ])
    
    if st.button("📡 Scan Live Market", use_container_width=True):
        scan_progress = st.progress(0)
        scan_status = st.empty()
        live_results = []
        
        for i, ticker in enumerate(selected_tickers):
            scan_status.text(f"Scanning {ticker}...")
            try:
                # Only need 1 year of data to calculate the 200-day MAs for today's signal
                df_raw = yf.download(ticker, period="1y", interval="1d", progress=False)
                if df_raw.empty: continue
                
                if isinstance(df_raw.columns, pd.MultiIndex):
                    df_raw.columns = df_raw.columns.get_level_values(0)
                df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
                df_raw.index = pd.to_datetime(df_raw.index).tz_localize(None)

                df = calculate_technicals(df_raw, timeframe="Daily")
                if df is None or df.empty: continue
                
                # Get the very last row (Today's Data)
                today = df.iloc[-1]
                
                # Filter Logic based on user selection
                passed = False
                if "Breakout" in scan_type and today.get('BREAKOUT_TRIGGERED', False):
                    passed = True
                elif "Accumulation" in scan_type and today.get('ACCUMULATION_DAY', False):
                    passed = True
                elif "Trend Matrix" in scan_type and today.get('EMA_SPEED_ALIGNED', False) and today.get('SAR_ALIGNED', False):
                    passed = True
                    
                if passed:
                    live_results.append({
                        "Ticker": ticker,
                        "Close Price": round(today['Close'], 2),
                        "RS Score vs SPY": round(today.get('RS_SCORE', 0), 2),
                        "ADX (Trend Strength)": round(today.get('ADX', 0), 2),
                        "Volume Surge": "Yes" if today.get('ACCUMULATION_DAY', False) else "No"
                    })
            except:
                pass
            scan_progress.progress((i + 1) / len(selected_tickers))
            
        scan_status.text("✅ Scan Complete!")
        
        if live_results:
            st.success(f"Found {len(live_results)} stocks matching your criteria today!")
            live_df = pd.DataFrame(live_results).sort_values(by="RS Score vs SPY", ascending=False)
            st.dataframe(live_df, use_container_width=True)
        else:
            st.info("No stocks matched this criteria today. The market might be chopping.")