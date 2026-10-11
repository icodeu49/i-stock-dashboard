import pandas as pd
import yfinance as yf
import numpy as np
import requests
import io
from market_data import normalize_prices, resample_prices

def calculate_sector_rank(target_ticker, target_score, gics_sector, timeframe="Daily"):
    """
    Scrapes S&P 500 peers for a sector, calculates their RS scores, 
    and returns the target ticker's exact leaderboard rank.
    """
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        
        tables = pd.read_html(io.StringIO(response.text))
        sp500_df = tables[0]
        
        sec_col = [c for c in sp500_df.columns if 'Sector' in str(c)][0]
        sym_col = [c for c in sp500_df.columns if 'Symbol' in str(c) or 'Ticker' in str(c)][0]
        
        peer_tickers = sp500_df[sp500_df[sec_col].astype(str).str.strip() == gics_sector.strip()][sym_col].tolist()
        peer_tickers = [str(t).replace('.', '-') for t in peer_tickers]

        if target_ticker in peer_tickers:
            peer_tickers.remove(target_ticker)
            
        if not peer_tickers:
            return "N/A (No Peers Found)"
            
        all_tickers = list(set(peer_tickers + [target_ticker])) 
        
        data = yf.download(all_tickers, period="6mo", interval="1d", progress=False)
        
        if isinstance(data.columns, pd.MultiIndex):
            if 'Close' in data.columns.get_level_values(0):
                close_prices = data['Close']
            else:
                close_prices = data.xs('Close', level=1, axis=1)
        else:
            close_prices = data[['Close']].rename(columns={'Close': target_ticker})
            
        close_prices = close_prices.dropna(how='all')
        close_prices.index = pd.to_datetime(close_prices.index).tz_localize(None)
        
        if timeframe == "Weekly":
            close_prices = close_prices.resample('W').last().dropna(how='all')
        elif timeframe == "Monthly":
            close_prices = close_prices.resample('ME').last().dropna(how='all')
            
        spy = yf.download("SPY", period="6mo", interval="1d", progress=False)
        if isinstance(spy.columns, pd.MultiIndex): 
            spy.columns = spy.columns.get_level_values(0)
            
        spy_close = spy['Close'].dropna()
        spy_close.index = pd.to_datetime(spy_close.index).tz_localize(None)
        
        if timeframe == "Weekly":
            spy_close = spy_close.resample('W').last()
        elif timeframe == "Monthly":
            spy_close = spy_close.resample('ME').last()
        else:
            spy_close = spy_close.resample('D').last()
        
        rs_lookback = {"Daily": 63, "Weekly": 13, "Monthly": 3}.get(timeframe, 63)
        leaderboard = {}
        
        for ticker in all_tickers:
            if ticker in close_prices.columns:
                stock_series = close_prices[ticker].dropna()
                merged = pd.DataFrame({'Stock': stock_series, 'SPY': spy_close}).dropna()
                
                if len(merged) >= rs_lookback * 0.8: 
                    ratio = merged['Stock'] / merged['SPY']
                    pct_chg = ratio.pct_change(periods=min(rs_lookback, len(ratio)-1)).iloc[-1] * 100
                    if not pd.isna(pct_chg):
                        leaderboard[ticker] = pct_chg

        leaderboard[target_ticker] = target_score
        
        sorted_leaderboard = sorted(leaderboard.items(), key=lambda x: x[1], reverse=True)
        rank = [item[0] for item in sorted_leaderboard].index(target_ticker) + 1
        total_peers = len(sorted_leaderboard)
        
        return f"🏆 #{rank} of {total_peers}"
        
    except Exception as e:
        print(f"⚠️ Leaderboard ranking failed for {target_ticker}: {e}")
        return "N/A (Error)"

def calculate_position_size(ticker, base_capital, sector_rank, atr_14_day, current_price):
    """
    Calculates dynamic position size based on Sector Momentum and Asset Volatility.
    """
    if current_price == 0 or atr_14_day == 0:
        return {"ticker": ticker, "suggested_shares": 0, "suggested_allocation": 0}

    sector_multipliers = {1: 1.5, 2: 1.25, 3: 1.0, 4: 0.75, 5: 0.5}
    conviction_capital = base_capital * sector_multipliers.get(sector_rank, 1.0)
    
    atr_pct = atr_14_day / current_price
    baseline_volatility = 0.03 
    
    volatility_scalar = baseline_volatility / atr_pct
    volatility_scalar = min(volatility_scalar, 2.0) 
    
    final_allocation = conviction_capital * volatility_scalar
    suggested_shares = int(final_allocation / current_price)
    
    return {
        "ticker": ticker,
        "suggested_allocation": round(final_allocation, 2),
        "suggested_shares": suggested_shares,
        "conviction_capital": conviction_capital,
        "volatility_scalar": round(volatility_scalar, 2)
    }

def calculate_technicals(df, timeframe="Weekly", spy_df=None, sector_df=None, sector_rank=3, now=None):
    """Calculate indicators from DAILY bars, using only completed exchange periods.

    EMA/ATR/MA50 lengths are bars of the selected timeframe. The 200-DMA
    entry guardrail always uses 200 daily sessions, even on weekly/monthly views.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    df = normalize_prices(df)
    df['MA200_DAILY'] = df['Close'].rolling(200).mean()
    df = resample_prices(df, timeframe, now)
    for label, ref in [('SPY', spy_df), ('Sector', sector_df)]:
        if ref is not None and not ref.empty:
            ref = resample_prices(normalize_prices(ref), timeframe, now)
        if label == 'SPY': spy_df = ref
        else: sector_df = ref
    length_map = {"Daily": 30, "Weekly": 20, "Monthly": 14}
    chosen_length = length_map[timeframe]
    if len(df) < chosen_length:
        result = pd.DataFrame()
        result.attrs['reason'] = f"Need {chosen_length} completed {timeframe.lower()} bars; found {len(df)}"
        return result

    df['EMA10'] = df['Close'].ewm(span=10, adjust=False).mean()
    df['EMA30'] = df['Close'].ewm(span=30, adjust=False).mean()
    df['EMA_SPEED_ALIGNED'] = df['EMA10'] > df['EMA30']
    df['MA200'] = df['MA200_DAILY']

    # 2. ATR
    high_low = df['High'] - df['Low']
    high_cp = (df['High'] - df['Close'].shift(1)).abs()
    low_cp = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    df['ATR_CHOSEN'] = tr.rolling(window=chosen_length).mean()
    df['ATR14'] = tr.rolling(window=14).mean()  

    # 3. ADX / DMI
    if 'TR' not in df.columns:
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = (df['High'] - df['Close'].shift(1)).abs()
        df['L-PC'] = (df['Low'] - df['Close'].shift(1)).abs()
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)

    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    
    pos_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    neg_dm = ((down_move > up_move) & (down_move > 0)) * down_move
    
    alpha = 1 / chosen_length
    tr_smoothed = df['TR'].ewm(alpha=alpha, adjust=False).mean()
    pos_dm_smoothed = pos_dm.ewm(alpha=alpha, adjust=False).mean()
    neg_dm_smoothed = neg_dm.ewm(alpha=alpha, adjust=False).mean()
    
    atr_filled = tr_smoothed.replace(0, float('nan'))
    di_plus = (100 * (pos_dm_smoothed / atr_filled)).fillna(0)
    di_minus = (100 * (neg_dm_smoothed / atr_filled)).fillna(0)
    
    dm_sum = di_plus + di_minus
    dm_sum = dm_sum.replace(0, float('nan'))
    dx = (100 * (di_plus - di_minus).abs() / dm_sum)
    
    df['ADX'] = dx.ewm(alpha=alpha, adjust=False).mean().fillna(0)
    df['ADX_STRONG'] = (df['ADX'] > 20) & (df['ADX'] > df['ADX'].shift(1))

    # 4. Parabolic SAR
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

    # 5. Volume and Anchors
    df['MA50'] = df['Close'].rolling(window=50).mean()
    df['AvgVolume50'] = df['Volume'].rolling(window=50).mean()
    df['Price_Up'] = df['Close'] > df['Close'].shift(1)
    df['Price_Down'] = df['Close'] < df['Close'].shift(1)
    
    df['ACCUMULATION_DAY'] = df['Price_Up'] & (df['Volume'] > (df['AvgVolume50'] * 1.5))
    max_down_vol_10d = (df['Volume'] * df['Price_Down'].astype(int)).shift(1).rolling(window=10).max()
    df['POCKET_PIVOT'] = df['Price_Up'] & (df['Volume'] > max_down_vol_10d) & (df['Close'] > df['MA50'])

    # 6. Relative Strength
    rs_lookback = {"Daily": 63, "Weekly": 13, "Monthly": 3}.get(timeframe, 63)
    if spy_df is not None and not spy_df.empty:
        spy_ref = spy_df.copy()
        if isinstance(spy_ref.columns, pd.MultiIndex): spy_ref.columns = spy_ref.columns.get_level_values(0)
        spy_close = spy_ref[['Close']].rename(columns={'Close': 'Close_SPY'})
        merged = df[['Close']].merge(spy_close, left_index=True, right_index=True, how='left')
        if not merged.empty and 'Close_SPY' in merged.columns:
            df['RS_Ratio'] = merged['Close'] / merged['Close_SPY']
            df['RS_SCORE'] = df['RS_Ratio'].pct_change(periods=rs_lookback, fill_method=None) * 100
        else: df['RS_SCORE'] = np.nan
    else: df['RS_SCORE'] = np.nan

    if sector_df is not None and not sector_df.empty:
        sector_ref = sector_df.copy()
        if isinstance(sector_ref.columns, pd.MultiIndex): sector_ref.columns = sector_ref.columns.get_level_values(0)
        sector_close = sector_ref[['Close']].rename(columns={'Close': 'Close_Sector'})
        merged_sec = df[['Close']].merge(sector_close, left_index=True, right_index=True, how='left')
        if not merged_sec.empty and 'Close_Sector' in merged_sec.columns:
            df['RS_Sector_Ratio'] = merged_sec['Close'] / merged_sec['Close_Sector']
            df['RS_SECTOR_SCORE'] = df['RS_Sector_Ratio'].pct_change(periods=rs_lookback, fill_method=None) * 100
        else: df['RS_SECTOR_SCORE'] = np.nan
    else: df['RS_SECTOR_SCORE'] = np.nan

# 7. Volatility Stop (The Elastic Leash & 2-Day Rule)
    vstop_arr, trend_arr = [], []
    current_trend = 1
    current_stop = None
    
    days_below = 0
    days_above = 0
    
    for i in range(len(df)):
        close_p, high_p, low_p = df['Close'].iloc[i], df['High'].iloc[i], df['Low'].iloc[i]
        atr = df['ATR_CHOSEN'].iloc[i]
        if pd.isna(atr):
            vstop_arr.append(np.nan)
            trend_arr.append(current_trend)
            continue
        adx = df['ADX'].iloc[i]
        
        # Safe fallback if MA50 hasn't generated enough bars yet
        ma50 = df['MA50'].iloc[i] if not pd.isna(df['MA50'].iloc[i]) else close_p
        
        # --- THE ELASTIC MULTIPLIER ---
        if close_p > ma50:
            # Healthy Trend: Give it wide room to compound (3.0x)
            # If the ADX trend is very strong, give it even more room (3.5x)
            mult = 3.5 if adx >= 25 else 3.0
        else:
            # Broken Trend: Snap the leash tight immediately (2.0x)
            mult = 2.0 
            
        if current_stop is None:
            current_stop = close_p - atr * mult
        if current_trend == 1:
            current_stop = max(current_stop, high_p - (atr * mult))
            if close_p < current_stop:
                days_below += 1
                if days_below >= 2: # 2-Day Confirmation Rule
                    current_trend, current_stop = -1, low_p + (atr * mult)
                    days_below = 0
            else:
                days_below = 0
        else:
            current_stop = min(current_stop, low_p + (atr * mult))
            if close_p > current_stop:
                days_above += 1
                if days_above >= 2: # 2-Day Confirmation Rule
                    current_trend, current_stop = 1, high_p - (atr * mult)
                    days_above = 0
            else:
                days_above = 0
                
        vstop_arr.append(current_stop)
        trend_arr.append(current_trend)
        
    df['VSTOP_LINE'] = vstop_arr
    df['VSTOP_TREND'] = trend_arr
    
    # 8. Clean Signals (Fixing the VectorBT double-trigger bug)
    df['VSTOP_BUY_SIGNAL'] = (df['VSTOP_TREND'] == 1) & (df['VSTOP_TREND'].shift(1) == -1)
    df['VSTOP_SELL_SIGNAL'] = (df['VSTOP_TREND'] == -1) & (df['VSTOP_TREND'].shift(1) == 1)

    # 9. Guardrail: Zero Falling Knives
    # Isolate all potential bullish entry triggers
    base_bull_triggers = df['POCKET_PIVOT'] | df['ACCUMULATION_DAY'] | df['VSTOP_BUY_SIGNAL']
    
    # Strictly enforce that entries ONLY happen in an active primary uptrend
    primary_trend_filter = (df['Close'] > df['MA50']) & (df['Close'] > df['MA200_DAILY'])
    
    # Final Entry Signal for VectorBT 
    df['BREAKOUT_TRIGGERED'] = base_bull_triggers & primary_trend_filter
    
    return df
    
