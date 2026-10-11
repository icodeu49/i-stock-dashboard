import numpy as np
import pandas as pd

def check_overextension(df):
    """Warning only: never filter an entry or hide a breakout alert.

    Raw input must be daily; helpers output carries daily MA aliases on all views.
    """
    from risk_policy import extension_pct, extension_warning
    if df.empty:
        return {"is_extended": False, "dist_50": None, "dist_200": None, "warning": "Insufficient Data"}
    ma50 = df['MA50'].iloc[-1] if 'MA50' in df else df.Close.rolling(50).mean().iloc[-1]
    ma200 = df['MA200_DAILY'].iloc[-1] if 'MA200_DAILY' in df else df.Close.rolling(200).mean().iloc[-1]
    dist_50 = extension_pct(df.Close.iloc[-1], ma50)
    dist_200 = extension_pct(df.Close.iloc[-1], ma200)
    warning = extension_warning(dist_50)
    return {"is_extended": bool(warning), "dist_50": dist_50, "dist_200": dist_200,
            "warning": warning if dist_50 is not None else "Insufficient Data"}


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
