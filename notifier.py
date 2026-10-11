"""Completed-period signals, weekly-led alerts and a compact daily setup digest."""
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from alpha_engine import check_market_regime
from helpers import calculate_technicals
from market_data import download_prices, last_completed_session
from notifications import require_telegram_config, send_records
from storage import BASE_DIR, ALERT_STATE_FILE, load_watchlist, read_json, write_json
from signal_policy import POLICY_VERSION, aligned, context, reasons, signal_label, trend_at, row_at, number
from watchlist_history import update_trends
from earnings_context import upcoming_earnings, describe_earnings

SNAPSHOT_FILE = BASE_DIR / 'scan_snapshot.json'


def percentage(value):
    value = number(value)
    return 'N/A' if value is None else f'{value:+.2f}%'


def format_signal(signal, regime):
    detail = signal['context']
    price = lambda value: 'N/A' if value is None else f'USD {value:.2f}'
    icon = '🔴' if signal['direction'] == 'sell' else '🟢'
    text = f"{icon} {signal['label']} — {signal['ticker']} | {signal['timeframe']} close {signal['date']}\n"
    if not signal['fresh_period']:
        text += 'Last completed-period setup; not a fresh signal from today.\n'
    text += f"Trigger: {', '.join(signal['reasons'])}\n"
    text += f"Signal close {price(detail['close'])} | Market regime: {regime}\n"
    text += f"Weekly: {signal['weekly_trend']} ({signal['weekly_asof']}) | Monthly: {signal['monthly_trend']} ({signal['monthly_asof']})\n"
    text += f"Prior 20-bar resistance {price(detail['breakout_level'])} | distance {percentage(detail['breakout_distance_pct'])}\n"
    text += f"VSTOP {price(detail['vstop'])} | distance below signal close {percentage(detail['stop_distance_pct'])}\n"
    text += f"Latest daily close vs 50-day MA: {percentage(detail['daily_ma50_distance_pct'])}"
    if detail['extended']:
        text += ' | ⚠️ Extended'
    text += f"\nRS vs SPY: {percentage(signal['rs_spy'])} | sector: {percentage(signal['rs_sector'])}"
    text += f"\nWatchlist sector rank: {signal['sector_rank']}"
    text += '\n' + describe_earnings(signal.get('earnings', {}))
    return text


def route_signal(timeframe, direction, weekly, monthly, date):
    if timeframe != 'Daily' or direction == 'sell':
        return 'alert'
    return 'alert' if aligned(weekly, monthly, date) else 'digest'


def run_automated_scanner():
    require_telegram_config()
    watchlist = load_watchlist()
    session = last_completed_session().date().isoformat()
    spy = download_prices('SPY')
    regime = check_market_regime(spy)
    sectors, failures, results, limited = {}, [], {}, []
    for etf in sorted({item.get('sector_etf') for item in watchlist.values()} - {None, ''}):
        try:
            sectors[etf] = download_prices(etf)
        except Exception as exc:
            failures.append(f'{etf}: {exc}')
    for ticker, metadata in watchlist.items():
        try:
            raw = download_prices(ticker)
            for timeframe in ['Daily', 'Weekly', 'Monthly']:
                frame = calculate_technicals(raw, timeframe, spy_df=spy, sector_df=sectors.get(metadata.get('sector_etf')))
                if frame.empty:
                    limited.append(f'{ticker}/{timeframe}')
                    continue
                results[(ticker, timeframe)] = frame
        except Exception as exc:
            failures.append(f'{ticker}: {exc}')
            print(f'Scan error: {ticker}: {exc}')
    groups = defaultdict(list)
    for (ticker, timeframe), frame in results.items():
        sector = watchlist[ticker].get('sector_etf')
        score = frame.RS_SCORE.iloc[-1]
        if sector and pd.notna(score):
            groups[(sector, timeframe)].append((ticker, score))
    rankings = {}
    for (_, timeframe), peers in groups.items():
        peers.sort(key=lambda pair: pair[1], reverse=True)
        for rank, (ticker, _) in enumerate(peers, 1):
            rankings[(ticker, timeframe)] = f'{rank}/{len(peers)}'
    signals, statuses = [], {}
    earnings_path = SNAPSHOT_FILE.with_name('earnings_cache.json')
    earnings_cache = read_json(earnings_path, {})
    for ticker in watchlist:
        statuses[ticker] = {tf: trend_at(results.get((ticker, tf))) for tf in ['Daily', 'Weekly', 'Monthly']}
    # Weekly alerts lead; monthly remains context and its own explicitly dated signals.
    for timeframe in ['Weekly', 'Monthly', 'Daily']:
        for ticker in watchlist:
            frame = results.get((ticker, timeframe))
            if frame is None:
                continue
            row = frame.iloc[-1]
            buy = bool(row.get('ENTRY_CANDIDATE', row.BREAKOUT_TRIGGERED))
            sell = timeframe in ['Weekly', 'Monthly'] and bool(row.VSTOP_SELL_SIGNAL)
            if not buy and not sell:
                continue
            date = frame.index[-1].date().isoformat()
            kind = 'sell' if sell else 'buy'
            weekly, monthly = results.get((ticker, 'Weekly')), results.get((ticker, 'Monthly'))
            asof = lambda df: 'unavailable' if df is None or df.empty else df.index[-1].date().isoformat()
            signal = {'id': f'signal:{ticker}:{timeframe}:{date}:{kind}', 'ticker': ticker, 'timeframe': timeframe,
                'date': date, 'direction': kind, 'label': signal_label(row, sell),
                'reasons': ['bearish VSTOP turn'] if sell else reasons(row),
                'fresh_period': date == session, 'weekly_trend': statuses[ticker]['Weekly'],
                'monthly_trend': statuses[ticker]['Monthly'], 'weekly_asof': asof(weekly), 'monthly_asof': asof(monthly),
                'route': route_signal(timeframe, kind, weekly, monthly, pd.Timestamp(date)),
                'context': context(row, row_at(results.get((ticker, 'Daily')))),
                'rs_spy': number(row.RS_SCORE), 'rs_sector': number(row.RS_SECTOR_SCORE),
                'sector_rank': rankings.get((ticker, timeframe), 'N/A')}
            signal['earnings'] = upcoming_earnings(ticker, cache=earnings_cache)
            signals.append(signal)
    write_json(earnings_path, earnings_cache)
    changes = update_trends(statuses, session, path=SNAPSHOT_FILE.with_name('watchlist_history.json'))
    scanned = len({ticker for ticker, _ in results})
    if not scanned:
        failures.append('No stocks have sufficient valid history for analysis')
    snapshot = {'schema_version': 1, 'policy': POLICY_VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'session': session, 'coverage': 'partial' if failures else 'complete', 'scanned': scanned,
        'watchlist_count': len(watchlist), 'regime': regime, 'failures': failures, 'limited': limited,
        'signals': signals, 'trends': statuses, 'changes': changes}
    write_json(SNAPSHOT_FILE, snapshot)
    delivered = read_json(ALERT_STATE_FILE, {'delivered': {}}).get('delivered', {})
    records = [(item['id'], format_signal(item, regime)) for item in signals if item['route'] == 'alert']
    pending_digest = [item for item in signals if item['route'] == 'digest' and item['id'] not in delivered]
    acknowledgements = {}
    for start in range(0, len(pending_digest), 12):
        batch = pending_digest[start:start + 12]
        keys = [item['id'] for item in batch]
        digest = hashlib.sha256('|'.join(keys).encode()).hexdigest()[:12]
        key = f'daily-digest:{session}:{digest}'
        text = f'📋 Daily setups — US close {session}\nWeekly/monthly bullish alignment is not confirmed.\n'
        for item in batch:
            text += f"{item['ticker']}: {item['label']} | W {item['weekly_trend']}, M {item['monthly_trend']}\n"
        records.append((key, text.rstrip()))
        acknowledgements[key] = keys
    history = read_json(SNAPSHOT_FILE.with_name('watchlist_history.json'), {'events': []})
    for item in history.get('events', []):
        if item.get('type') == 'trend_changed' and item['to'] == 'bearish' and item['asof'] >= (pd.Timestamp(session) - pd.Timedelta(days=7)).date().isoformat():
            key = f"trend-change:{item['asof']}:{item['ticker']}:{item['timeframe']}:{item['to']}"
            records.append((key, f"⚠️ Watchlist deterioration — {item['ticker']} | {item['timeframe']}\n"
                                 f"{item['from']} → {item['to']} | US close {item['asof']}. Stock remains on your watchlist."))
    digest = hashlib.sha256('|'.join(failures + limited).encode()).hexdigest()[:12]
    status = f"{'⚠️ Partial' if failures else '✅ Completed'} stock scan — US close {session}\n"
    status += f'Analysed {scanned}/{len(watchlist)} stocks. Market regime: {regime}.\n'
    status += f"{sum(s['route'] == 'alert' for s in signals)} individual-alert candidates; {sum(s['route'] == 'digest' for s in signals)} daily digest setups. Previously delivered signals are suppressed."
    if limited:
        status += f'\nInsufficient history in {len(limited)} stock/timeframe combinations (see workflow log).'
        print('Insufficient history:', ', '.join(limited))
    if failures:
        status += '\nData failures: ' + '; '.join(failures)[:1500]
    records.append((f'scan-status:{session}:{POLICY_VERSION}:{digest}', status))
    send_records(records, acknowledgements=acknowledgements)
    print(f"Snapshot: {len(signals)} signals; {len(changes)} trend changes; coverage {snapshot['coverage']}.")
    if failures:
        raise RuntimeError(f'Scan incomplete: {len(failures)} data failures; valid alerts were delivered')


if __name__ == '__main__':
    run_automated_scanner()
