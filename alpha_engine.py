import numpy as np
import pandas as pd

def check_overextension(df):
    """
    PILLAR 4: THE RUBBER BAND FILTER
    Measures how stretched the current price is from primary moving averages.
    Returns a dictionary with the extension status and raw metrics.
    """
    if df.empty or len(df) < 200:
        return {"is_extended": False, "dist_50": 0.0, "dist_200": 0.0, "warning": "Insufficient Data"}

    # Use existing MAs if present, otherwise calculate them
    ma50 = df['MA50'].iloc[-1] if 'MA50' in df.columns else df['Close'].rolling(50).mean().iloc[-1]
    ma200 = df['MA200'].iloc[-1] if 'MA200' in df.columns else df['Close'].rolling(200).mean().iloc[-1]
    
    current_price = df['Close'].iloc[-1]

    # Calculate percentage distance from MAs
    dist_50 = ((current_price - ma50) / ma50) * 100
    dist_200 = ((current_price - ma200) / ma200) * 100

    # Institutional "Late-Stage" Thresholds:
    # Dangerously extended if > 25% above 50-day or > 50% above 200-day
    is_extended = (dist_50 > 25.0) or (dist_200 > 50.0)

    return {
        "is_extended": is_extended,
        "dist_50": round(dist_50, 2),
        "dist_200": round(dist_200, 2)
    }


def check_vcp_contraction(df):
    """
    PILLAR 1: VOLATILITY CONTRACTION PATTERN (VCP)
    Scans the last 6 months of action. Looks for a deep base that progressively
    tightens from left to right, accompanied by extreme volume dry-up.
    """
    if df.empty or len(df) < 120:
        return {"has_vcp": False, "tightness": 0.0, "vol_dryup": False}

    # Isolate the last 6 months (approx 120 trading days)
    recent_data = df.tail(120).copy()
    
    # 1. Measure the Macro Base (Left Side of the Pattern)
    base_high = recent_data['High'].max()
    base_low = recent_data['Low'].min()
    macro_drawdown = ((base_high - base_low) / base_high) * 100

    # 2. Measure the Micro Contraction (Right Side of the Pattern)
    # The last 15 days should be extremely tight compared to the macro base
    tight_data = recent_data.tail(15)
    tight_high = tight_data['High'].max()
    tight_low = tight_data['Low'].min()
    micro_drawdown = ((tight_high - tight_low) / tight_high) * 100

    # 3. Measure Volume Dry-up
    # Average volume over the tight period should be notably less than the 50-day avg
    avg_vol_50 = recent_data['Volume'].tail(50).mean()
    avg_vol_tight = tight_data['Volume'].mean()
    vol_dryup = avg_vol_tight < (avg_vol_50 * 0.8) # Volume contracted by at least 20%

    # VCP Logic Gate: 
    # Macro base must be valid (15% to 40% deep)
    # Micro base must be tightly coiled (< 8% range)
    # Volume must be drying up
    is_vcp = (15.0 <= macro_drawdown <= 45.0) and (micro_drawdown <= 8.0) and vol_dryup

    return {
        "has_vcp": is_vcp,
        "base_depth": round(macro_drawdown, 2),
        "tight_range": round(micro_drawdown, 2),
        "vol_dryup": vol_dryup
    }

def check_market_regime(spy_df):
    """
    PILLAR 2: MARKET REGIME TRAFFIC LIGHT
    Evaluates the S&P 500 to determine if the macro environment is safe for breakouts.
    Returns: "GREEN" (Risk On), "YELLOW" (Caution), or "RED" (Risk Off/Cash)
    """
    if spy_df.empty or len(spy_df) < 200:
        return "YELLOW"

    # Calculate Macro Trend Lines for the Market
    spy_close = spy_df['Close'].iloc[-1]
    spy_ma50 = spy_df['Close'].rolling(50).mean().iloc[-1]
    spy_ma200 = spy_df['Close'].rolling(200).mean().iloc[-1]

    # Is the 50-day trending above the 200-day? (Golden/Death Cross)
    trend_up = spy_ma50 > spy_ma200

    # Is the current price above the 50-day?
    price_up = spy_close > spy_ma50

    if trend_up and price_up:
        return "GREEN"
    elif not trend_up and not price_up:
        return "RED"
    else:
        return "YELLOW"
