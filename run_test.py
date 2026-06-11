import pandas as pd
import yfinance as yf
from app import calculate_technicals, run_backtest_simulation

def execute_terminal_test():
    # User Configuration Inputs
    target_stock = "AAPL"
    test_horizon_years = 5.0
    starting_pool = 100000
    
    print("=" * 60)
    print(f"🧪 STARTING 5-YEAR STRATEGY BACKTEST FOR: {target_stock}")
    print("=" * 60)
    
    summary, trades_df = run_backtest_simulation(target_stock, test_horizon_years, starting_pool)
    
    if summary is None:
        print("❌ Error: Simulation failed to process. Check data connection.")
        return
        
    # Output High-Level Metrics directly to terminal output stream
    print(f"🟢 Starting Capital Pool:   ${summary['Starting Capital']:,}")
    print(f"💰 Final Ending Value:     ${summary['Ending Value']:,}")
    print(f"📈 Net Strategy Profit:    ${summary['Net Profit ($']:,}")
    print(f"📊 Total Return Rate:      {summary['Total Return (%)']:+.2f}%")
    print(f"🔄 Total Completed Trades: {summary['Total Trades Executed']}")
    print("-" * 60)
    
    if not trades_df.empty:
        print("📜 LAST 10 COMPLETED TRADES:")
        print(trades_df.tail(10).to_string(index=False))
    else:
        print("⏳ Zero strategy triggers occurred during this macro timeline window.")
    print("=" * 60)

if __name__ == "__main__":
    execute_terminal_test()
