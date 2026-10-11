"""Daily-session risk rules shared by every chart timeframe and notifier."""
import numpy as np
import pandas as pd

from market_data import market_schedule

EXTENSION_THRESHOLD_PCT = 10.0
ATR_LENGTH = 30
BASE_CONFIRM_SESSIONS = 2
SHIELDED_CONFIRM_SESSIONS = 3


def extension_pct(close, ma50):
    try:
        close, ma50 = float(close), float(ma50)
        if not np.isfinite(close) or not np.isfinite(ma50) or ma50 <= 0:
            return None
        return ((close - ma50) / ma50) * 100
    except (TypeError, ValueError):
        return None


def extension_warning(pct):
    if pct is not None and np.isfinite(pct) and pct > EXTENSION_THRESHOLD_PCT:
        return f'⚠️ Extended — Wait for Pullback ({pct:.2f}% above 50-DMA)'
    return ''


def daily_risk_inputs(frame):
    """Input must contain completed, normalized daily OHLCV bars."""
    frame = frame.copy()
    frame['MA50_DAILY'] = frame.Close.rolling(50).mean()
    frame['MA200_DAILY'] = frame.Close.rolling(200).mean()
    frame['MA200_RISING'] = frame.MA200_DAILY > frame.MA200_DAILY.shift(1)
    tr = pd.concat([frame.High - frame.Low, (frame.High - frame.Close.shift()).abs(),
                    (frame.Low - frame.Close.shift()).abs()], axis=1).max(axis=1)
    frame['VSTOP_ATR'] = tr.rolling(ATR_LENGTH).mean()
    up, down = frame.High.diff(), -frame.Low.diff()
    plus = up.where((up > down) & (up > 0), 0.)
    minus = down.where((down > up) & (down > 0), 0.)
    smooth_tr = tr.ewm(alpha=1 / ATR_LENGTH, adjust=False).mean().replace(0, np.nan)
    di_plus = 100 * plus.ewm(alpha=1 / ATR_LENGTH, adjust=False).mean() / smooth_tr
    di_minus = 100 * minus.ewm(alpha=1 / ATR_LENGTH, adjust=False).mean() / smooth_tr
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    frame['VSTOP_ADX'] = dx.ewm(alpha=1 / ATR_LENGTH, adjust=False).mean().fillna(0)
    return frame


def apply_daily_vstop(frame, mega_cap=None):
    """Two consecutive exchange closes; healthy mega-caps allow one extra close.

    Shielding is bounded: a third breach always exits. Losing MA50, the rising
    MA200, or verified eligibility restores the normal two-close rule immediately.
    Multipliers change on the current close; an existing trailing stop never loosens.
    """
    frame = frame.copy()
    if frame.empty:
        return frame
    eligible = pd.Series(False, index=frame.index) if mega_cap is None else mega_cap.reindex(frame.index, fill_value=False)
    sessions = market_schedule(str(frame.index[0].date()), str(frame.index[-1].date())).index
    positions = sessions.get_indexer(frame.index)
    trend, stop, below, above = 1, None, 0, 0
    previous_session = None
    output = []
    for i, row in enumerate(frame.itertuples()):
        # Missing sessions cannot be counted as consecutive closes.
        if previous_session is None or positions[i] < 0 or positions[i] != previous_session + 1:
            below = above = 0
        previous_session = positions[i] if positions[i] >= 0 else None
        close, atr, ma50 = row.Close, row.VSTOP_ATR, row.MA50_DAILY
        if not all(np.isfinite(v) for v in [close, atr, ma50]) or atr <= 0 or ma50 <= 0:
            below = above = 0
            output.append((np.nan, np.nan, np.nan, 0, 2, False, False))
            continue
        mult = (3.5 if row.VSTOP_ADX >= 25 else 3.0) if close > ma50 else 2.0
        shield = bool(eligible.iloc[i] and close > ma50 and close > row.MA200_DAILY
                      and row.MA200_RISING)
        required = SHIELDED_CONFIRM_SESSIONS if trend == 1 and shield else BASE_CONFIRM_SESSIONS
        if stop is None:
            stop = close - atr * mult
        suppressed = False
        if trend == 1:
            stop = max(stop, row.High - atr * mult)
            below = below + 1 if close < stop else 0
            breaches = below
            suppressed = shield and below == BASE_CONFIRM_SESSIONS
            if below >= required:
                trend, stop = -1, row.Low + atr * mult
                below = above = 0
        else:
            stop = min(stop, row.Low + atr * mult)
            above = above + 1 if close > stop else 0
            breaches = above
            if above >= BASE_CONFIRM_SESSIONS:
                trend, stop = 1, row.High - atr * mult
                below = above = 0
        output.append((stop, trend, mult, breaches, required, shield, suppressed))
    columns = ['VSTOP_LINE', 'VSTOP_TREND', 'VSTOP_MULTIPLIER', 'VSTOP_BREACH_SESSIONS',
               'VSTOP_CONFIRM_SESSIONS', 'VSTOP_SHIELD_ELIGIBLE', 'VSTOP_SHIELD_SUPPRESSED']
    frame[columns] = pd.DataFrame(output, index=frame.index, columns=columns)
    return frame
