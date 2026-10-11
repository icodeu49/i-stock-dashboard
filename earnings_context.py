"""Optional earnings context; calendar gaps never suppress price alerts."""
from datetime import datetime, timedelta, timezone
import pandas as pd
import yfinance as yf
from storage import BASE_DIR, read_json, write_json

CACHE_FILE = BASE_DIR / 'earnings_cache.json'


def upcoming_earnings(ticker, now=None, cache=None):
    now = now or datetime.now(timezone.utc)
    cache = {} if cache is None else cache
    prior = cache.get(ticker, {})
    try:
        checked = datetime.fromisoformat(prior.get('checked_at', ''))
    except ValueError:
        checked = None
    if checked and now - checked < timedelta(hours=20):
        return prior
    result = {'status': 'unavailable', 'date': None, 'checked_at': now.isoformat(), 'source': 'Yahoo Finance'}
    try:
        dates = yf.Ticker(ticker).get_earnings_dates(limit=4)
        if dates is not None and not dates.empty:
            today = now.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).date()
            future = sorted({pd.Timestamp(date).date() for date in dates.index if pd.Timestamp(date).date() >= today})
            if future:
                result.update(status='provider_reported', date=future[0].isoformat(),
                              within_7_days=(future[0] - today).days <= 7)
    except Exception:
        pass
    cache[ticker] = result
    return result


def describe_earnings(result):
    if result.get('date'):
        flag = ' ⚠️ within 7 days' if result.get('within_7_days') else ''
        return f"Earnings: {result['date']} (Yahoo calendar; date may change){flag}"
    return 'Earnings: date unavailable'
