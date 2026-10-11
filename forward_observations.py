"""Freeze live observations and evaluate 20-session cohorts without backdating entries.

This is a signal-cohort study, not a simulated portfolio or the historical exit system.
"""
from datetime import datetime, timezone
import pandas as pd
from market_data import market_schedule
from storage import read_json, write_json


def record_and_evaluate(snapshot, daily_frames, spy, path, now=None):
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    state = read_json(path, {'schema_version': 1, 'observations': {}})
    observations = state['observations']
    for signal in snapshot['signals']:
        if not signal['fresh_period']:
            continue
        strategies = [signal['timeframe']]
        if signal['direction'] != 'buy':
            continue
        if signal['timeframe'] == 'Daily' and signal['route'] == 'alert':
            strategies.append('Aligned daily')
        for strategy in strategies:
            key = f'{snapshot["policy"]}:{strategy}:{signal["id"]}'
            observations.setdefault(key, {'strategy': strategy, 'ticker': signal['ticker'],
                'signal_date': signal['date'], 'recorded_at': now.isoformat(),
                'policy': snapshot['policy'], 'status': 'awaiting_future_prices'})
    for item in observations.values():
        if item['status'] == 'complete':
            continue
        frame = daily_frames.get(item['ticker'])
        if frame is None or frame.empty:
            continue
        recorded = pd.Timestamp(item['recorded_at'])
        schedule = market_schedule(str(recorded.date()), str((recorded + pd.Timedelta(days=70)).date()))
        # The first eligible open must occur AFTER the observation was recorded.
        future = schedule.loc[schedule.market_open > recorded].head(20)
        if len(future) < 20 or future.market_close.iloc[-1] > now:
            continue
        dates = pd.DatetimeIndex(future.index).tz_localize(None)
        if not dates.isin(frame.index).all() or not dates.isin(spy.index).all():
            continue
        stock = frame.loc[dates]
        benchmark = spy.loc[dates]
        cost_factor = (1 - .001) * (1 - .0005) / ((1 + .001) * (1 + .0005))
        item.update(status='complete', entry_date=str(dates[0].date()), end_date=str(dates[-1].date()),
            return_20_sessions_pct=(float(stock.Close.iloc[-1] / stock.Open.iloc[0]) * cost_factor - 1) * 100,
            spy_return_20_sessions_pct=(float(benchmark.Close.iloc[-1] / benchmark.Open.iloc[0]) * cost_factor - 1) * 100,
            adverse_excursion_pct=(float(stock.Low.min() / stock.Open.iloc[0]) - 1) * 100)
    state['updated_at'] = now.isoformat()
    state['method'] = 'First open after observation, 20-session close; fees 0.10% and slippage 0.05% each side. Cohorts may overlap; no portfolio allocation or stops.'
    write_json(path, state)
    return state
