"""Shared, causal signal definitions for notifications and strategy comparisons."""
import math
import pandas as pd
from risk_policy import extension_pct, extension_warning

POLICY_VERSION = 'daily-risk-v2'
BREAKOUT_BARS = 20


def add_signal_columns(frame):
    frame = frame.copy()
    # Current bar is deliberately excluded from its own resistance threshold.
    frame['BREAKOUT_LEVEL'] = frame.High.shift(1).rolling(BREAKOUT_BARS).max()
    above = frame.Close > frame.BREAKOUT_LEVEL
    primary = (frame.Close > frame.MA50) & (frame.Close > frame.MA200_DAILY)
    frame['PRICE_BREAKOUT_TRIGGERED'] = above & ~above.shift(1, fill_value=False) & primary
    frame['BREAKOUT_TRIGGERED'] = frame['BREAKOUT_TRIGGERED'] & primary
    frame['SETUP_TRIGGERED'] = frame['BREAKOUT_TRIGGERED']
    frame['ENTRY_CANDIDATE'] = frame.SETUP_TRIGGERED | frame.PRICE_BREAKOUT_TRIGGERED
    known = frame[['Close', 'MA50', 'MA200_DAILY', 'VSTOP_TREND']].notna().all(axis=1)
    frame['TREND_STATE'] = 'unknown'
    frame.loc[known, 'TREND_STATE'] = 'mixed'
    frame.loc[known & primary & (frame.VSTOP_TREND == 1), 'TREND_STATE'] = 'bullish'
    frame.loc[known & ((frame.VSTOP_TREND == -1) | (frame.Close < frame.MA200_DAILY)), 'TREND_STATE'] = 'bearish'
    return frame


def row_at(frame, date=None):
    if frame is None or frame.empty:
        return None
    available = frame if date is None else frame.loc[:date]
    return None if available.empty else available.iloc[-1]


def trend_at(frame, date=None):
    row = row_at(frame, date)
    return 'unknown' if row is None else row.get('TREND_STATE', 'unknown')


def aligned(weekly, monthly, date=None):
    return trend_at(weekly, date) == 'bullish' and trend_at(monthly, date) == 'bullish'


def reasons(row):
    result = []
    for key, label in [('PRICE_BREAKOUT_TRIGGERED', '20-bar resistance breakout'),
                       ('POCKET_PIVOT', 'pocket pivot'), ('ACCUMULATION_DAY', 'accumulation'),
                       ('VSTOP_BUY_SIGNAL', 'bullish VSTOP turn')]:
        value = row.get(key, False)
        if pd.notna(value) and bool(value):
            result.append(label)
    return result


def signal_label(row, sell=False):
    if sell:
        return 'VSTOP breakdown'
    return 'Confirmed price breakout' if bool(row.get('PRICE_BREAKOUT_TRIGGERED', False)) else 'Momentum setup'


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def percent_distance(price, reference):
    price, reference = number(price), number(reference)
    return None if price is None or reference is None or reference <= 0 else (price / reference - 1) * 100


def context(row, daily_row):
    """Distances refer to the signal close, not an executable current quote."""
    close, stop = number(row.Close), number(row.get('VSTOP_LINE'))
    distance_50 = extension_pct(daily_row.Close, daily_row.get('MA50')) if daily_row is not None else None
    distance_200 = percent_distance(daily_row.Close, daily_row.get('MA200_DAILY')) if daily_row is not None else None
    return {'close': close, 'breakout_level': number(row.get('BREAKOUT_LEVEL')),
            'breakout_distance_pct': percent_distance(close, row.get('BREAKOUT_LEVEL')),
            'vstop': stop, 'stop_distance_pct': (close - stop) / close * 100 if close and stop and stop < close else None,
            'daily_ma50_distance_pct': distance_50, 'daily_ma200_distance_pct': distance_200,
            'extended': bool(extension_warning(distance_50)), 'extension_warning': extension_warning(distance_50),
            'vstop_multiplier': number(row.get('VSTOP_MULTIPLIER')),
            'vstop_confirm_sessions': number(row.get('VSTOP_CONFIRM_SESSIONS')),
            'vstop_shield_eligible': bool(row.get('VSTOP_SHIELD_ELIGIBLE', False))}
