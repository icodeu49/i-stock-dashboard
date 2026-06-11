import pandas as pd
import numpy as np
import yfinance as yf
from app import calculate_technicals  # Reusing your core math formulas

def simulate_strategy(ticker, lookback_years=2, starting_capital=100000):
    print(f"📊 Initializing Backtest for {ticker} over {lookback_years} Years...")
    
    # 1. Fetch deep history to avoid calculation starvation
    fetch_period = f"{lookback_years + 2}y" 
    
    df_daily = yf.download(ticker, period=fetch_period, interval="1d", progress=False, multi_level_index=False)
    df_weekly = yf.download(ticker, period=fetch_period, interval="1wk", progress=False, multi_level_index=False)
    spy_daily = yf.download("SPY", period=fetch_period, interval="1d", progress=False, multi_level_index=False)
    spy_weekly = yf.download("SPY", period=fetch_period, interval="1wk", progress=False, multi_level_index=False)
    
    # Remove multi-indexes if present
    for frame in [df_daily, df_weekly, spy_daily, spy_weekly]:
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)

    # 2. Run your identical indicator calculations
    df_daily = calculate_technicals(df_daily, timeframe="Daily", spy_df=spy_daily)
    df_weekly = calculate_technicals(df_weekly, timeframe="Weekly", spy_df=spy_weekly)
    
    # Resample weekly trend states down to daily rows so we can track daily exits cleanly
    df_weekly['_weekly_date'] = df_weekly.index
    weekly_trends = df_weekly[['VSTOP_TREND']].resample('D').ffill()
    
    # Merge trend lines
    df = df_daily.join(weekly_trends, rsuffix='_WEEKLY', how='inner')
    
    # Filter down into our strict testing target window
    start_date = pd.Timestamp.now() - pd.DateOffset(years=lookback_years)
    df = df[df.index >= start_date]
    
    # 3. Backtest Simulation Variables
    cash = starting_capital
    active_trades = []
    trade_log = []
    
    for date, row in df.iterrows():
        current_price = row['Close']
        weekly_trend = row.get('VSTOP_TREND_WEEKLY', 1)
        
        # Check Exits First: If weekly trend flips negative, close all existing entries
        if weekly_trend == -1 and len(active_trades) > 0:
            for trade in active_trades:
                exit_value = trade['shares'] * current_price
                cash += exit_value
                pnl = exit_value - trade['cost_basis']
                return_pct = (pnl / trade['cost_basis']) * 100
                
                trade_log.append({
                    'Ticker': ticker, 'Buy Date': trade['date'], 'Exit Date': date,
                    'Sizing': trade['cost_basis'], 'PnL ($)': pnl, 'Return (%)': return_pct
                })
            active_trades = [] # Clear positions array
            
        # Check Entries: Look for setups ONLY if weekly baseline is healthy/positive
        if weekly_trend == 1:
            is_pocket = row.get('POCKET_PIVOT', False)
            is_accum = row.get('ACCUMULATION_DAY', False)
            
            if is_pocket or is_accum:
                # Determine Conviction Sizing
                conviction_score = sum([is_pocket, is_accum, row.get('EMA_SPEED_ALIGNED', False)])
                allocation = 6000 if conviction_score >= 2 else 3000
                
                # Risk Guard: Only execute if we have liquid cash reserves left
                if cash >= allocation:
                    cash -= allocation
                    shares_bought = allocation / current_price
                    active_trades.append({
                        'date': date, 'shares': shares_bought, 'cost_basis': allocation
                    })
                    
    # Clean up outstanding open trades at current terminal price
    if active_trades:
        final_price = df.iloc[-1]['Close']
        for trade in active_trades:
            exit_value = trade['shares'] * final_price
            cash += exit_value
            pnl = exit_value - trade['cost_basis']
            trade_log.append({
                'Ticker': ticker, 'Buy Date': trade['date'], 'Exit Date': df.index[-1],
                'Sizing': trade['cost_basis'], 'PnL ($)': pnl, 'Return (%)': (pnl / trade['cost_basis']) * 100
            })

    total_value = cash
    net_profit = total_value - starting_capital
    total_return_pct = (net_profit / starting_capital) * 100
    
    return total_return_pct, pd.DataFrame(trade_log)

# Example Execution
if __name__ == "__main__":
    # Test across one of your primary watchlist parameters
    for period in [0.5, 2, 4]:
        pct, log = simulate_strategy("AAPL", lookback_years=period)
        print(f"📈 Result over {period} Years: {pct:+.2f}% Cumulative Portfolio Return")
