import pandas as pd
import yfinance as yf
import vectorbt as vbt

# Import your existing engine!
from helpers import calculate_technicals

def run_backtest(ticker="AAPL", years=5):
    print(f"🧪 Running {years}-Year Backtest on {ticker}...")
    
    # 1. Download Historical Data
    df_raw = yf.download(ticker, period=f"{years}y", interval="1d", progress=False)
    if isinstance(df_raw.columns, pd.MultiIndex):
        df_raw.columns = df_raw.columns.get_level_values(0)
    
    # Strip Timezones to prevent silent Pandas errors
    df_raw.index = pd.to_datetime(df_raw.index).tz_localize(None)

    # 2. Run the data through your EXACT Quant Engine
    df = calculate_technicals(df_raw, timeframe="Daily")
    
    if df is None or df.empty:
        print("Not enough data to backtest.")
        return

    # 3. Define the Strategy Triggers
    # Buy when your custom matrix triggers a breakout
    entries = df['BREAKOUT_TRIGGERED']
    # Sell when the Volatility Stop flips to red
    exits = df['VSTOP_SELL_SIGNAL']

    # 4. Execute the VectorBT Simulation
    pf = vbt.Portfolio.from_signals(
        close=df['Close'],
        entries=entries,
        exits=exits,
        init_cash=10000,        # Start with $10,000
        fees=0.001,             # 0.1% slippage/commission per trade
        sl_stop=0.08,           # 🛡️ Hard 8% Stop-Loss safety net
    )

    # 5. Print the Institutional Tear Sheet
    print("\n==========================================")
    print(f"📊 {ticker} BACKTEST RESULTS ({years} YEARS)")
    print("==========================================")
    
    # Print the full statistical breakdown
    print(pf.stats())
    
    # Uncomment the line below to view an interactive browser chart of every trade!
    # pf.plot().show()

if __name__ == "__main__":
    # Test it on a leading stock like Nvidia
    run_backtest("NVDA", years=5)
