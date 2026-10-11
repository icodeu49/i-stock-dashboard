"""Controlled entry-frequency comparison; no orders and no parameter fitting."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import pandas as pd

from helpers import calculate_technicals
from market_data import download_prices, last_completed_session
from signal_policy import POLICY_VERSION
from storage import BASE_DIR, load_watchlist, write_json

STRATEGIES = ['Daily', 'Weekly', 'Monthly', 'Aligned daily']
DEFAULTS = {'initial_capital': 100000., 'allocation': 10000., 'max_positions': 10,
            'fee': .001, 'slippage': .0005, 'stop_fraction': .08, 'max_hold_sessions': 63}


def prepare_signals(raw, spy, now=None):
    frames = {tf: calculate_technicals(raw, tf, spy_df=spy, now=now) for tf in ['Daily', 'Weekly', 'Monthly']}
    result = raw.copy()
    for name in ['Daily', 'Weekly', 'Monthly']:
        frame = frames[name]
        entries = pd.Series(False, index=raw.index) if frame.empty else frame.ENTRY_CANDIDATE.reindex(raw.index, fill_value=False)
        result[name] = entries.astype(bool)
    aligned = pd.Series(True, index=raw.index)
    for name in ['Weekly', 'Monthly']:
        frame = frames[name]
        aligned &= False if frame.empty else frame.TREND_STATE.reindex(raw.index, method='ffill').eq('bullish')
    result['Aligned daily'] = result.Daily & aligned
    daily = frames['Daily']
    result['exit'] = False if daily.empty else daily.VSTOP_SELL_SIGNAL.reindex(raw.index, fill_value=False)
    result['score'] = float('-inf') if daily.empty else daily.RS_SCORE.reindex(raw.index).fillna(float('-inf'))
    # A closing signal/state is available only for the following trading open.
    for name in STRATEGIES + ['exit']:
        result[name] = result[name].shift(1, fill_value=False).astype(bool)
    result['score'] = result.score.shift(1).fillna(float('-inf'))
    return result


def simulate(data, timeline, strategy, config=None):
    config = {**DEFAULTS, **(config or {})}
    cash, positions, trades, curve = config['initial_capital'], {}, [], []
    fee, slip = config['fee'], config['slippage']
    timeline = pd.DatetimeIndex(timeline)

    def close_position(ticker, date, price, reason):
        nonlocal cash
        position = positions.pop(ticker)
        fill = float(price) * (1 - slip)
        net = position['shares'] * fill * (1 - fee)
        cash += net
        trades.append({'ticker': ticker, 'entry_date': str(position['entry_date'].date()),
            'exit_date': str(date.date()), 'entry_fill': position['entry_fill'], 'exit_fill': fill,
            'cost': position['cost'], 'pnl': net - position['cost'],
            'return_pct': (net / position['cost'] - 1) * 100, 'sessions_held': position['age'], 'reason': reason})

    for date in timeline:
        closed = set()
        for ticker in list(positions):
            position = positions[ticker]
            position['age'] += 1
            if date not in data[ticker].index:
                continue
            row = data[ticker].loc[date]
            stop = position['entry_fill'] * (1 - config['stop_fraction'])
            if row.Open <= stop:
                close_position(ticker, date, row.Open, 'gap through fixed stop')
            elif bool(row['exit']) or position['age'] >= config['max_hold_sessions']:
                close_position(ticker, date, row.Open, 'daily VSTOP / maximum holding period')
            elif row.Low <= stop:
                close_position(ticker, date, stop, 'fixed stop')
            if ticker not in positions:
                closed.add(ticker)
        candidates = [(float(frame.loc[date, 'score']), ticker) for ticker, frame in data.items()
                      if date in frame.index and bool(frame.loc[date, strategy]) and ticker not in positions and ticker not in closed]
        candidates.sort(key=lambda item: (-item[0], item[1]))
        for _, ticker in candidates:
            if len(positions) >= config['max_positions'] or cash < config['allocation']:
                break
            row = data[ticker].loc[date]
            fill = float(row.Open) * (1 + slip)
            budget = config['allocation']
            positions[ticker] = {'shares': budget / (fill * (1 + fee)), 'cost': budget,
                                 'entry_fill': fill, 'entry_date': date, 'age': 1}
            cash -= budget
            stop = fill * (1 - config['stop_fraction'])
            if row.Low <= stop:
                close_position(ticker, date, stop, 'entry-session fixed stop')
        # Terminal liquidation makes costs and unclosed trade reporting comparable.
        if date == timeline[-1]:
            for ticker in list(positions):
                close_position(ticker, date, data[ticker].loc[:date, 'Close'].iloc[-1], 'period-end valuation')
        equity = cash + sum(position['shares'] * float(data[ticker].loc[:date, 'Close'].iloc[-1])
                            for ticker, position in positions.items())
        curve.append({'date': str(date.date()), 'equity': equity, 'positions': len(positions)})
    values = pd.Series([config['initial_capital']] + [point['equity'] for point in curve], dtype=float)
    ending = float(values.iloc[-1])
    years = max((timeline[-1] - timeline[0]).days / 365.25, 1 / 365.25) if len(timeline) else 0
    metrics = {'total_return_pct': (ending / config['initial_capital'] - 1) * 100,
        'cagr_pct': ((ending / config['initial_capital']) ** (1 / years) - 1) * 100 if years else None,
        'max_drawdown_pct': float((values / values.cummax() - 1).min() * 100),
        'trades': len(trades), 'period_end_valuations': sum(item['reason'] == 'period-end valuation' for item in trades),
        'win_rate_pct': sum(item['pnl'] > 0 for item in trades) / len(trades) * 100 if trades else None,
        'average_trade_return_pct': sum(item['return_pct'] for item in trades) / len(trades) if trades else None,
        'ending_value': ending, 'small_sample': len(trades) < 30}
    return metrics, trades, curve


def benchmark_return(spy, timeline, config=None):
    config = {**DEFAULTS, **(config or {})}
    frame = spy.reindex(timeline).dropna()
    if frame.empty:
        return None
    purchase = float(frame.Open.iloc[0]) * (1 + config['slippage']) * (1 + config['fee'])
    sale = float(frame.Close.iloc[-1]) * (1 - config['slippage']) * (1 - config['fee'])
    return (sale / purchase - 1) * 100


def run_comparison(years=5, holdout_months=18, output=None):
    output = Path(output or BASE_DIR / 'reports' / 'strategy_comparison')
    output.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    end = last_completed_session(now)
    start, split = end - pd.DateOffset(years=years), end - pd.DateOffset(months=holdout_months)
    if start >= split:
        raise ValueError('Holdout must be shorter than the full comparison period')
    watchlist, data, failures, fingerprints = load_watchlist(), {}, {}, {}
    spy = download_prices('SPY', now=now)
    for ticker in sorted(watchlist):
        try:
            raw = download_prices(ticker, now=now)
            data[ticker] = prepare_signals(raw, spy, now=now)
            fingerprints[ticker] = {'sha256': hashlib.sha256(raw.to_csv().encode()).hexdigest(),
                                    'first_date': str(raw.index[0].date()), 'rows': len(raw)}
        except Exception as exc:
            failures[ticker] = type(exc).__name__
    if not data:
        raise RuntimeError('No valid stock histories for comparison')
    windows = {'development': spy.index[(spy.index >= start) & (spy.index < split)],
               'historical_holdout': spy.index[(spy.index >= split) & (spy.index <= end)]}
    if any(len(window) < 20 for window in windows.values()):
        raise RuntimeError('Not enough benchmark sessions in both comparison windows')
    rows, trade_rows, curve_rows = [], [], []
    for window, timeline in windows.items():
        for strategy in STRATEGIES:
            metrics, trades, curve = simulate(data, timeline, strategy)
            metrics.update(window=window, strategy=strategy, start=str(timeline[0].date()), end=str(timeline[-1].date()),
                           spy_return_pct=benchmark_return(spy, timeline))
            rows.append(metrics)
            trade_rows += [{'window': window, 'strategy': strategy, **item} for item in trades]
            curve_rows += [{'window': window, 'strategy': strategy, **item} for item in curve]
    report = {'generated_at': now.isoformat(), 'asof': str(end.date()), 'policy': POLICY_VERSION,
        'code_sha': os.getenv('GITHUB_SHA', 'local'), 'configuration': DEFAULTS,
        'watchlist_symbols': sorted(watchlist), 'valid_symbols': sorted(data), 'failures': failures,
        'coverage': 'partial' if failures else 'complete', 'data_fingerprints': fingerprints,
        'holdout_start': str(split.date()), 'results': rows,
        'limitations': [
            'Current watchlist only: selection and survivorship bias; historical watchlist membership is not reconstructed.',
            'Parameters are fixed before this run, with no fitting or optimisation. The historical holdout is not future evidence.',
            'Mega-cap shielding is excluded: point-in-time size observations did not exist for the historical window. Current sizes are never backfilled.',
            'Adjusted daily OHLCV; exact intraday paths and real execution liquidity are unavailable.',
            'Common exits: daily VSTOP at next open, 8% fixed stop, or maximum 63 sessions; final positions liquidated for valuation.',
            'Each window starts flat with equal capital. Fees 0.10% and slippage 0.05% per side; taxes excluded.',
            'SPY is a fully invested adjusted-price benchmark; strategy cash and exposure can differ.',
            'Small-sample flags are descriptive, not a statistical guarantee. Do not infer a proven winner from this comparison.']}
    write_json(output / 'summary.json', report)
    write_json(BASE_DIR / 'strategy_report.json', report)
    pd.DataFrame(rows).to_csv(output / 'metrics.csv', index=False)
    pd.DataFrame(trade_rows).to_csv(output / 'trades.csv', index=False)
    pd.DataFrame(curve_rows).to_csv(output / 'equity.csv', index=False)
    text = ['# Entry-frequency comparison', '', f'US close: {end.date()} · Coverage: {report["coverage"]}', '',
            '| Window | Strategy | Return | Max drawdown | Trades | SPY return |', '|---|---|---:|---:|---:|---:|']
    for row in rows:
        text.append(f'| {row["window"]} | {row["strategy"]} | {row["total_return_pct"]:.2f}% | {row["max_drawdown_pct"]:.2f}% | {row["trades"]} | {row["spy_return_pct"]:.2f}% |')
    text += ['', *['- ' + item for item in report['limitations']]]
    (output / 'report.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
    for row in rows:
        print(f'{row["window"]} | {row["strategy"]}: return {row["total_return_pct"]:+.2f}%, drawdown {row["max_drawdown_pct"]:.2f}%, trades {row["trades"]}')
    print(f'Comparison coverage: {len(data)}/{len(watchlist)} symbols; {len(failures)} unavailable.')
    if failures:
        print('::warning::Comparison is partial; excluded symbols are listed in the report.')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--years', type=int, default=5)
    parser.add_argument('--holdout-months', type=int, default=18)
    parser.add_argument('--output')
    args = parser.parse_args()
    run_comparison(args.years, args.holdout_months, args.output)
