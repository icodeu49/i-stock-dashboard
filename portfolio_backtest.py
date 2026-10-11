"""Portfolio simulation: completed-period signals executed at the next session open."""
import pandas as pd
from market_data import download_prices
from helpers import calculate_technicals

def run_backtest_simulation(watchlist, lookback_years, starting_capital=100000, scenario_idx=1):
    if isinstance(watchlist, str):
        watchlist = [watchlist]
    
    portfolio_data = {}
    all_dates = set()
    
    spy = download_prices("SPY")
    for ticker in watchlist:
        try:
            raw = download_prices(ticker)
            df_w = calculate_technicals(raw, timeframe="Weekly", spy_df=spy)
            df_m = calculate_technicals(raw, timeframe="Monthly", spy_df=spy)
            if df_w.empty or df_m.empty:
                continue
            combined_df = raw.copy()
            for label, source in [("WEEKLY", df_w), ("MONTHLY", df_m)]:
                entry = source['VSTOP_BUY_SIGNAL'] & (source.Close > source.MA50) & (source.Close > source.MA200_DAILY)
                combined_df['BUY_' + label] = entry.reindex(raw.index, fill_value=False).shift(1, fill_value=False)
                combined_df['SELL_' + label] = source['VSTOP_SELL_SIGNAL'].reindex(raw.index, fill_value=False).shift(1, fill_value=False)

            portfolio_data[ticker] = combined_df
            all_dates.update(combined_df.index)
        except Exception as exc:
            raise RuntimeError(f"Backtest data unavailable for {ticker}: {exc}") from exc

    if not portfolio_data: return None, None, None

    start_date = pd.Timestamp.now() - pd.DateOffset(days=int(lookback_years * 365.25))
    timeline = sorted([d for d in all_dates if d >= start_date])
    
    if not timeline: return None, None, None
    
    cash = float(starting_capital)
    positions = {}  
    trade_log = []
    equity_curve = []
    
    for current_date in timeline:
        for ticker in list(positions.keys()):
            df = portfolio_data[ticker]
            if current_date in df.index:
                row = df.loc[current_date]
                should_exit = bool(row['SELL_WEEKLY' if scenario_idx in [1, 4] else 'SELL_MONTHLY'])
                if should_exit:
                    current_price = float(row['Open'])
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
                should_buy = bool(row['BUY_WEEKLY' if scenario_idx in [1, 2] else 'BUY_MONTHLY'])
                if should_buy and ticker not in positions and cash >= 4000.0:
                    current_price = float(row['Open'])
                    cash -= 4000.0
                    positions[ticker] = {'shares': 4000.0 / current_price, 'cost': 4000.0, 'entry_date': current_date}
                    

        current_portfolio_value = cash
        for ticker, pos in positions.items():
            df = portfolio_data[ticker]
            current_portfolio_value += pos['shares'] * float(df.loc[current_date, 'Close']) if current_date in df.index else pos['shares'] * float(df.loc[:current_date, 'Close'].iloc[-1])
                
        equity_curve.append({'Date': current_date, 'Portfolio Value': current_portfolio_value})

    equity_df = pd.DataFrame(equity_curve)
    if not equity_df.empty:
        equity_df.set_index('Date', inplace=True)
        quarterly_df = equity_df.resample('QE').last().dropna().reset_index()
        if len(quarterly_df) < 2:
            quarterly_df = equity_df.reset_index()
            quarterly_df['Date'] = quarterly_df['Date'].dt.strftime('%Y-%m-%d')
        else:
            quarterly_df['Date'] = quarterly_df['Date'].dt.to_period('Q').astype(str)
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

