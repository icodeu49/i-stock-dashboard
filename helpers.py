# helpers.py
import pandas as pd
import yfinance as yf
import numpy as np
import requests 

def calculate_sector_rank(target_ticker, target_score, gics_sector, timeframe="Daily"):
    """
    Scrapes S&P 500 peers for a sector, calculates their RS scores, 
    and returns the target ticker's exact leaderboard rank.
    """
    try:
        # 1. Scrape live S&P 500 table from Wikipedia using a Fake Browser ID
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        # Fetch the webpage with the headers to bypass the 403 Forbidden block
        response = requests.get(url, headers=headers)
        tables = pd.read_html(response.text)
        sp500_df = tables[0]
        
        # 2. Isolate tickers in the matching sector
        peer_tickers = sp500_df[sp500_df['GICS Sector'] == gics_sector]['Symbol'].tolist()
        peer_tickers = [t.replace('.', '-') for t in peer_tickers]


        # Remove target ticker if it's already in the list to avoid duplicates
        if target_ticker in peer_tickers:
            peer_tickers.remove(target_ticker)
            
        if not peer_tickers:
            return "N/A"
            
        # 3. Bulk download close prices for peers + target ticker
        all_tickers = peer_tickers + [target_ticker]
        # Download 6 months to ensure we have enough data for lookbacks
        data = yf.download(all_tickers, period="6m", interval="1d", progress=False, multi_level_index=False)
        
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
            
        close_prices = data['Close'].dropna(how='all')
        
        # Resample if Weekly or Monthly
        if timeframe == "Weekly":
            close_prices = close_prices.resample('W').last().dropna()
        elif timeframe == "Monthly":
            close_prices = close_prices.resample('ME').last().dropna()
            
        # 4. Calculate RS Score for every peer
        # We need SPY to calculate the RS Ratio (Stock / SPY)
        spy = yf.download("SPY", period="6m", interval="1d", progress=False, multi_level_index=False)
        if isinstance(spy.columns, pd.MultiIndex): spy.columns = spy.columns.get_level_values(0)
        spy_close = spy['Close'].resample('W' if timeframe=="Weekly" else 'ME' if timeframe=="Monthly" else 'D').last()
        
        rs_lookback = {"Daily": 63, "Weekly": 13, "Monthly": 3}.get(timeframe, 63)
        leaderboard = {}
        
        for ticker in all_tickers:
            if ticker in close_prices.columns:
                stock_series = close_prices[ticker].dropna()
                # Align with SPY
                merged = pd.DataFrame({'Stock': stock_series, 'SPY': spy_close}).dropna()
                if len(merged) > rs_lookback:
                    ratio = merged['Stock'] / merged['SPY']
                    pct_chg = ratio.pct_change(periods=min(rs_lookback, len(ratio)-1)).iloc[-1] * 100
                    if not pd.isna(pct_chg):
                        leaderboard[ticker] = pct_chg

        # Overwrite our target stock with its official processed score
        leaderboard[target_ticker] = target_score
        
        # 5. Sort Leaderboard and find Rank
        sorted_leaderboard = sorted(leaderboard.items(), key=lambda x: x[1], reverse=True)
        rank = [item[0] for item in sorted_leaderboard].index(target_ticker) + 1
        total_peers = len(sorted_leaderboard)
        
        return f"🏆 #{rank} of {total_peers}"
        
    except Exception as e:
        print(f"⚠️ Leaderboard ranking failed for {target_ticker}: {e}")
        return "N/A"


def calculate_technicals(df, timeframe="Weekly", spy_df=None, sector_df=None):
    """
    Pure mathematical technical analysis engine. 
    Contains absolutely no UI logic so it can run smoothly in GitHub Actions.
    """
    if df.empty:
        return df
        
    # ─── HARD DISAGREEMENT AUDIT PRINT ─────────────────────────────────────
    # This forces GitHub to tell us EXACTLY what data rows and columns it sees
    if timeframe == "Monthly":
        print(f"🚨 [DEBUG SHAPE] Analyzing Monthly Frame. Total rows available: {len(df)}")
        print(f"🚨 [DEBUG COLUMNS] Raw column footprint: {list(df.columns)}")
        print(f"🚨 [DEBUG SAMPLE] Latest row values:\n{df.tail(1)}")
    # ─────────────────────────────────────────────────────────────────────────

    # Flatten out yfinance MultiIndex layers completely if they exist
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    # ─── FORCE TIME TIMEFRAME COMPRESSION ──────────────────────────────────
    # This guarantees that even if yfinance sends daily rows, we compress them cleanly
    df = df.copy()
    
    if timeframe == "Weekly":
        # 1. Compress main stock
        df = df.resample('W').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
        # 2. Compress the benchmarks so their dates match perfectly
        if spy_df is not None and not spy_df.empty:
            spy_df = spy_df.resample('W').agg({'Close': 'last'}).dropna()
        if sector_df is not None and not sector_df.empty:
            sector_df = sector_df.resample('W').agg({'Close': 'last'}).dropna()
            
    elif timeframe == "Monthly":
        # 1. Compress main stock
        df = df.resample('ME').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
        # 2. Compress the benchmarks so their dates match perfectly
        if spy_df is not None and not spy_df.empty:
            spy_df = spy_df.resample('ME').agg({'Close': 'last'}).dropna()
        if sector_df is not None and not sector_df.empty:
            sector_df = sector_df.resample('ME').agg({'Close': 'last'}).dropna()
    # ─────────────────────────────────────────────────────────────────────────

    if timeframe == "Monthly":
        print(f"🚨 [DEBUG SHAPE AFTER COMPRESSION] Real monthly bars: {len(df)}")


    # Lower the bar requirement for macro timeframes 
    min_bars = 14 if timeframe == "Monthly" else 50
    if len(df) < min_bars:
        print(f"⚠️ Insufficient bars for {timeframe} calculation. Have {len(df)}, need {min_bars}.")
        return df


    length_map = {"Daily": 30, "Weekly": 20, "Monthly": 14}
    chosen_length = length_map.get(timeframe, 14)

    # 1. EMAs
    df['EMA10'] = df['Close'].ewm(span=10, adjust=False).mean()
    df['EMA30'] = df['Close'].ewm(span=30, adjust=False).mean()
    df['EMA_SPEED_ALIGNED'] = df['EMA10'] > df['EMA30']

    # 2. ATR
    high_low = df['High'] - df['Low']
    high_cp = (df['High'] - df['Close'].shift(1)).abs()
    low_cp = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    df['ATR_CHOSEN'] = tr.rolling(window=chosen_length).mean()
    df['ATR14'] = tr.rolling(window=14).mean()  

    # =========================================================================
    # # 3. ADX / DMI (HARDENED WILDER SMOOTHING ALIGNMENT)
    # =========================================================================
    # 1. Calculate True Range (TR) if not already defined
    if 'TR' not in df.columns:
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = (df['High'] - df['Close'].shift(1)).abs()
        df['L-PC'] = (df['Low'] - df['Close'].shift(1)).abs()
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)

    # 2. Raw Directional Movement
    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    
    pos_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    neg_dm = ((down_move > up_move) & (down_move > 0)) * down_move
    
    # 3. Wilder's Smoothing Allocation via Alpha = 1 / chosen_length
    alpha = 1 / chosen_length
    tr_smoothed = df['TR'].ewm(alpha=alpha, adjust=False).mean()
    pos_dm_smoothed = pos_dm.ewm(alpha=alpha, adjust=False).mean()
    neg_dm_smoothed = neg_dm.ewm(alpha=alpha, adjust=False).mean()
    
    # 4. Calculate Directional Indicators (+DI, -DI)
    atr_filled = tr_smoothed.replace(0, float('nan'))
    di_plus = 100 * (pos_dm_smoothed / atr_filled).fillna(0)
    di_minus = 100 * (neg_dm_smoothed / atr_filled).fillna(0)
    
    # 5. Directional Movement Index (DX) and final Wilder's ADX
    dm_sum = di_plus + di_minus
    dm_sum = dm_sum.replace(0, float('nan'))
    dx = 100 * (di_plus - di_minus).abs() / dm_sum
    
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
    df['MA200'] = df['Close'].rolling(window=200).mean()
    df['AvgVolume50'] = df['Volume'].rolling(window=50).mean()
    df['Price_Up'] = df['Close'] > df['Close'].shift(1)
    df['Price_Down'] = df['Close'] < df['Close'].shift(1)
    
    df['ACCUMULATION_DAY'] = df['Price_Up'] & (df['Volume'] > (df['AvgVolume50'] * 1.5))
    max_down_vol_10d = (df['Volume'] * df['Price_Down'].astype(int)).rolling(window=10).max()
    df['POCKET_PIVOT'] = df['Price_Up'] & (df['Volume'] > max_down_vol_10d) & (df['Close'] > df['MA50'])

    # ─── DYNAMIC MOMENTUM LOOKBACK (1 Quarter Equivalent) ───
    rs_lookback = {"Daily": 63, "Weekly": 13, "Monthly": 3}.get(timeframe, 63)

    # 6. Relative Strength Score vs Benchmark (SPY)
    if spy_df is not None and not spy_df.empty:
        spy_ref = spy_df.copy()
        if isinstance(spy_ref.columns, pd.MultiIndex): 
            spy_ref.columns = spy_ref.columns.get_level_values(0)
            
        spy_close = spy_ref[['Close']].rename(columns={'Close': 'Close_SPY'})
        merged = df[['Close']].merge(spy_close, left_index=True, right_index=True, how='left')
        
        if not merged.empty and 'Close_SPY' in merged.columns:
            df['RS_Ratio'] = merged['Close'] / merged['Close_SPY']
            df['RS_SCORE'] = df['RS_Ratio'].pct_change(periods=min(rs_lookback, len(df)-1), fill_method=None) * 100
        else: 
            df['RS_SCORE'] = 0.0
    else: 
        df['RS_SCORE'] = 0.0

    # 6b. Relative Strength Score vs Specific Sector ETF
    if sector_df is not None and not sector_df.empty:
        sector_ref = sector_df.copy()
        if isinstance(sector_ref.columns, pd.MultiIndex): 
            sector_ref.columns = sector_ref.columns.get_level_values(0)
            
        sector_close = sector_ref[['Close']].rename(columns={'Close': 'Close_Sector'})
        merged_sec = df[['Close']].merge(sector_close, left_index=True, right_index=True, how='left')
        
        if not merged_sec.empty and 'Close_Sector' in merged_sec.columns:
            df['RS_Sector_Ratio'] = merged_sec['Close'] / merged_sec['Close_Sector']
            df['RS_SECTOR_SCORE'] = df['RS_Sector_Ratio'].pct_change(periods=min(rs_lookback, len(df)-1), fill_method=None) * 100
        else: 
            df['RS_SECTOR_SCORE'] = 0.0
    else: 
        df['RS_SECTOR_SCORE'] = 0.0

    # 7. Volatility Stop (VSTOP) ─── UPDATED MULTIPLIER TO 2.0 ───
    vstop_arr, trend_arr = [], []
    current_trend, current_stop = 1, df['Close'].iloc[0] - (df['ATR_CHOSEN'].fillna(0).iloc[0] * 2.0)
    
    for i in range(len(df)):
        close_p, high_p, low_p = df['Close'].iloc[i], df['High'].iloc[i], df['Low'].iloc[i]
        atr = df['ATR_CHOSEN'].fillna(0).iloc[i]
        if current_trend == 1:
            current_stop = max(current_stop, high_p - (atr * 2.0))
            if close_p < current_stop:
                current_trend, current_stop = -1, low_p + (atr * 2.0)
        else:
            current_stop = min(current_stop, low_p + (atr * 2.0))
            if close_p > current_stop:
                current_trend, current_stop = 1, high_p - (atr * 2.0)
        vstop_arr.append(current_stop)
        trend_arr.append(current_trend)
        
    df['VSTOP_LINE'] = vstop_arr
    df['VSTOP_TREND'] = trend_arr
    df['VSTOP_BUY_SIGNAL'] = (df['VSTOP_TREND'] == 1) & (df['VSTOP_TREND'].shift(1) == -1)
    df['VSTOP_SELL_SIGNAL'] = (df['VSTOP_TREND'] == -1) & (df['VSTOP_TREND'].shift(1) == 1)

    df['BREAKOUT_TRIGGERED'] = df['POCKET_PIVOT'] | df['ACCUMULATION_DAY'] | df['VSTOP_BUY_SIGNAL'] | df['VSTOP_SELL_SIGNAL']
    return df