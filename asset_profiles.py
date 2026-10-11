"""Dated, provider-reported size classifications; never inferred from ticker names."""
from datetime import datetime, timezone
import math
import pandas as pd
import yfinance as yf

MEGA_CAP_USD = 200_000_000_000


def is_mega_cap(observation):
    try:
        cap = float(observation.get('market_cap', 0))
        return (observation.get('status') == 'available' and observation.get('currency') == 'USD'
                and observation.get('quote_type') == 'EQUITY' and math.isfinite(cap) and cap >= MEGA_CAP_USD)
    except (TypeError, ValueError):
        return False


def asset_profile(ticker, session, cache):
    """Freeze one observation per completed session. Failures use ordinary stops.

    Profiles apply only to the observed session, never to earlier price history.
    Retaining previous observations makes subsequent full-history scans reproducible.
    """
    profile = cache.setdefault(ticker, {'observations': {}})
    observations = profile.setdefault('observations', {})
    if session in observations:
        return profile
    observation = {'observed_at': datetime.now(timezone.utc).isoformat(), 'source': 'Yahoo Finance',
                   'status': 'unavailable', 'market_cap': None, 'currency': None, 'quote_type': None}
    try:
        info = yf.Ticker(ticker).get_info()
        cap = float(info.get('marketCap') or 0)
        if math.isfinite(cap) and cap > 0 and info.get('currency') and info.get('quoteType'):
            observation.update(status='available', market_cap=cap, currency=info['currency'], quote_type=info['quoteType'])
    except Exception as exc:
        observation['error_type'] = type(exc).__name__
    observations[session] = observation
    return profile


def mega_cap_mask(index, profile=None):
    observations = (profile or {}).get('observations', {})
    return pd.Series([is_mega_cap(observations.get(str(date.date()), {})) for date in index], index=index)
