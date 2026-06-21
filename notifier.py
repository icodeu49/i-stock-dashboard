# notifier.py

def run_automated_scanner():
    """
    Background worker process designed for headless execution loops inside cloud servers.
    """
    print("🤖 Booting automated analysis daemon workflow...")
    
    # 1. Read category profile configurations safely
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r") as f:
            raw_data = json.load(f)
    else:
        print("❌ System watchlist file context configuration missing. Execution aborted.")
        return

    # --- SAFETY NET: Handle both List and Dictionary formats ---
    if isinstance(raw_data, list):
        print("💡 Detected legacy list format. Automatically converting to dictionary framework...")
        # Converts ["AAPL", "NVDA"] -> {"AAPL": {"group": "Small/Mid Growth"}, "NVDA": {"group": "Small/Mid Growth"}}
        watchlist = {ticker: {"group": "Small/Mid Growth"} for ticker in raw_data}
    elif isinstance(raw_data, dict):
        watchlist = raw_data
    else:
        print("❌ Unknown data format inside watchlist.json. Execution aborted.")
        return
    # --------------------------------────────────────-----------

    tickers = list(watchlist.keys())
    print(f"📋 Watchlist successfully parsed. Target systems: {tickers}")

    # 2. Download macro baseline framework parameters
    spy_df = yf.download("SPY", period="2y", interval="1d", progress=False, multi_level_index=False)
    if isinstance(spy_df.columns, pd.MultiIndex):
        spy_df.columns = spy_df.columns.get_level_values(0)

    # 3. Process execution scanning maps
    alert_triggers = []
    
    for ticker in tickers:
        try:
            print(f"🔍 Analyzing tracking targets: {ticker}")
            df_raw = yf.download(ticker, period="2y", interval="1d", progress=False, multi_level_index=False)
            
            if df_raw.empty:
                continue
                
            # Direct math execution call without generating UI side effects
            df = calculate_technicals(df_raw, timeframe="Daily", spy_df=spy_df)
            latest = df.iloc[-1]
            
            # Evaluate alert criteria
            if latest['BREAKOUT_TRIGGERED']:
                alert_triggers.append(ticker)
                print(f"🚨 ALERT TRIGGERED: {ticker} has breached resistance lines!")
                
        except Exception as e:
            print(f"⚠️ Error compiling data array for {ticker}: {str(e)}")
            continue

    # 4. Summary output reporting logs
    print("\n==========================================")
    print("🏁 AUTOMATION RUN MATRIX COMPLETED")
    print(f"Total Assets Processed: {len(tickers)}")
    print(f"Active Alert Triggers Flagged: {alert_triggers}")
    print("==========================================")