"""Track automated selection and trend changes separately from manual watchlist fields."""
from datetime import datetime, timezone
from storage import read_json, write_json, BASE_DIR

HISTORY_FILE = BASE_DIR / 'watchlist_history.json'


def update_selection(source, candidates, added, asof, complete=True, path=None):
    path = path or HISTORY_FILE
    state = read_json(path, {'stocks': {}, 'events': []})
    stamp = datetime.now(timezone.utc).isoformat()
    stocks = state.setdefault('stocks', {})
    selected = set(candidates)
    for ticker, metadata in candidates.items():
        item = stocks.setdefault(ticker, {'first_observed_at': stamp, 'sources': {}})
        if ticker in added:
            item.setdefault('added_at', stamp)
            item.setdefault('addition_reason', metadata.get('selection_reason', source))
            state.setdefault('events', []).append({'type': 'added', 'ticker': ticker, 'source': source,
                                                  'asof': asof, 'observed_at': stamp, 'reason': item['addition_reason']})
        previous = item.setdefault('sources', {}).get(source, {})
        item['sources'][source] = {'first_selected_asof': previous.get('first_selected_asof', asof),
            'last_selected_asof': asof, 'active': True, 'reason': metadata.get('selection_reason', source),
            'sector_etf': metadata.get('sector_etf')}
    # A partial provider failure cannot establish that a stock lost leadership.
    if complete:
        for ticker, item in stocks.items():
            membership = item.get('sources', {}).get(source)
            if ticker not in selected and membership and membership.get('active'):
                membership.update(active=False, inactive_asof=asof)
                state.setdefault('events', []).append({'type': 'selection_lost', 'ticker': ticker,
                    'source': source, 'asof': asof, 'observed_at': stamp})
    state['events'] = state.get('events', [])[-2000:]
    state['updated_at'] = stamp
    write_json(path, state)
    return state


def update_trends(statuses, asof, path=None):
    path = path or HISTORY_FILE
    state = read_json(path, {'stocks': {}, 'events': []})
    stamp = datetime.now(timezone.utc).isoformat()
    changes = []
    for ticker, trends in statuses.items():
        item = state.setdefault('stocks', {}).setdefault(ticker, {'first_observed_at': stamp, 'sources': {}})
        previous = item.get('trends', {})
        for timeframe in ['Weekly', 'Monthly']:
            old, new = previous.get(timeframe), trends.get(timeframe, 'unknown')
            if old in {'bullish', 'mixed', 'bearish'} and new in {'bullish', 'mixed', 'bearish'} and old != new:
                event = {'type': 'trend_changed', 'ticker': ticker, 'timeframe': timeframe,
                         'from': old, 'to': new, 'asof': asof, 'observed_at': stamp}
                state.setdefault('events', []).append(event)
                changes.append(event)
        item['trends'] = {**previous, **{key: value for key, value in trends.items() if value != 'unknown'}}
        item['trend_asof'] = asof
    state['events'] = state.get('events', [])[-2000:]
    state['updated_at'] = stamp
    write_json(path, state)
    return changes
