from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

import strategy_comparison as comparison
import forward_observations
from storage import read_json, write_json


def prepared(opens, lows=None, entries=None):
    index = pd.bdate_range('2026-10-05', periods=len(opens))
    result = pd.DataFrame({'Open': opens, 'Close': opens, 'Low': lows or opens,
        'High': np.array(opens) + 1, 'Volume': 1000., 'score': 1., 'exit': False}, index=index)
    for name in comparison.STRATEGIES:
        result[name] = entries or [False] * len(opens)
    return result


class ComparisonTests(unittest.TestCase):
    def test_flat_trade_pays_costs_on_both_sides(self):
        frame = prepared([100., 100., 100.], entries=[True, False, False])
        metrics, trades, _ = comparison.simulate({'ABC': frame}, frame.index, 'Daily')
        expected = 10000 * ((1 - .0005) * (1 - .001) / ((1 + .0005) * (1 + .001)) - 1)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]['pnl'], expected)
        self.assertLess(metrics['total_return_pct'], 0)

    def test_gap_through_stop_fills_at_open_not_unavailable_stop(self):
        frame = prepared([100., 80., 90.], entries=[True, False, False])
        _, trades, _ = comparison.simulate({'ABC': frame}, frame.index, 'Daily')
        self.assertEqual(trades[0]['reason'], 'gap through fixed stop')
        self.assertAlmostEqual(trades[0]['exit_fill'], 80 * (1 - .0005))

    def test_entry_session_stop_is_modelled(self):
        frame = prepared([100., 105.], lows=[80., 100.], entries=[True, False])
        _, trades, _ = comparison.simulate({'ABC': frame}, frame.index, 'Daily')
        self.assertEqual(trades[0]['reason'], 'entry-session fixed stop')
        self.assertEqual(trades[0]['entry_date'], trades[0]['exit_date'])

    def test_cash_and_position_limits_use_previously_known_ranking(self):
        first = prepared([100., 101.], entries=[True, False])
        second = first.copy(); second['score'] = 2.
        _, trades, _ = comparison.simulate({'LOW': first, 'HIGH': second}, first.index, 'Daily', {'max_positions': 1})
        self.assertEqual([item['ticker'] for item in trades], ['HIGH'])

    def test_closing_signals_and_trend_alignment_execute_next_session(self):
        raw = prepared([100.] * 6)
        raw.index = pd.bdate_range('2026-10-05', periods=6)
        def technical(data, timeframe, **kwargs):
            if timeframe == 'Daily':
                return pd.DataFrame({'ENTRY_CANDIDATE': True, 'RS_SCORE': 1.}, index=raw.index)
            date = '2026-10-09' if timeframe == 'Weekly' else '2026-09-30'
            return pd.DataFrame({'ENTRY_CANDIDATE': [True], 'VSTOP_SELL_SIGNAL': [False], 'TREND_STATE': ['bullish']}, index=pd.to_datetime([date]))
        with patch.object(comparison, 'calculate_technicals', side_effect=technical):
            result = comparison.prepare_signals(raw, raw)
        self.assertFalse(result.loc['2026-10-09', 'Weekly'])
        self.assertTrue(result.loc['2026-10-12', 'Weekly'])
        self.assertFalse(result.loc['2026-10-09', 'Aligned daily'])
        self.assertTrue(result.loc['2026-10-12', 'Aligned daily'])

    def test_empty_signals_return_cash_without_fake_win_rate(self):
        frame = prepared([100., 105.])
        metrics, trades, _ = comparison.simulate({'ABC': frame}, frame.index, 'Daily')
        self.assertEqual(metrics['total_return_pct'], 0)
        self.assertIsNone(metrics['win_rate_pct'])
        self.assertEqual(trades, [])

    def test_new_evaluation_window_starts_flat(self):
        frame = prepared([100., 100., 110., 120.], entries=[True, False, False, False])
        metrics, trades, _ = comparison.simulate({'ABC': frame}, frame.index[2:], 'Daily')
        self.assertEqual(metrics['ending_value'], comparison.DEFAULTS['initial_capital'])
        self.assertFalse(trades)

    def test_full_report_has_separate_windows_and_all_four_strategies(self):
        dates = pd.bdate_range(end='2026-10-09', periods=2000)
        close = np.linspace(50., 150., len(dates))
        raw = pd.DataFrame({'Open': close, 'Close': close, 'High': close + 1, 'Low': close - 1, 'Volume': 1000.}, index=dates)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(comparison, 'BASE_DIR', Path(directory)), \
             patch.object(comparison, 'load_watchlist', return_value={'ABC': {}}), \
             patch.object(comparison, 'download_prices', return_value=raw), \
             patch.object(comparison, 'last_completed_session', return_value=pd.Timestamp('2026-10-09')):
            report = comparison.run_comparison(output=Path(directory) / 'reports')
            self.assertEqual(len(report['results']), 8)
            self.assertEqual({item['strategy'] for item in report['results']}, set(comparison.STRATEGIES))
            self.assertEqual({item['window'] for item in report['results']}, {'development', 'historical_holdout'})
            self.assertTrue((Path(directory) / 'reports' / 'equity.csv').exists())
            self.assertTrue((Path(directory) / 'strategy_report.json').exists())
            self.assertIn('survivorship', ' '.join(report['limitations']))


class ForwardTests(unittest.TestCase):
    def schedule(self, start, end):
        dates = pd.bdate_range(start, end)
        return pd.DataFrame({'market_open': (dates + pd.Timedelta(hours=13, minutes=30)).tz_localize('UTC'),
                             'market_close': (dates + pd.Timedelta(hours=20)).tz_localize('UTC')}, index=dates)

    def test_observation_is_immutable_and_future_prices_are_required(self):
        snapshot = {'policy': 'test', 'signals': [{'id': 'one', 'ticker': 'ABC', 'date': '2026-10-09',
            'timeframe': 'Weekly', 'direction': 'buy', 'fresh_period': True, 'route': 'alert'}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(forward_observations, 'market_schedule', side_effect=self.schedule):
            path = Path(directory) / 'forward.json'
            first = forward_observations.record_and_evaluate(snapshot, {}, pd.DataFrame(), path, '2026-10-11T07:00:00Z')
            second = forward_observations.record_and_evaluate(snapshot, {}, pd.DataFrame(), path, '2026-10-12T07:00:00Z')
            item = next(iter(second['observations'].values()))
            self.assertEqual(item['recorded_at'], '2026-10-11T07:00:00+00:00')
            self.assertEqual(item['status'], 'awaiting_future_prices')

    def test_recorded_after_market_open_cannot_receive_that_open(self):
        dates = pd.bdate_range('2026-10-12', periods=25)
        frame = pd.DataFrame({'Open': 100., 'Close': 110., 'Low': 95.}, index=dates)
        initial = {'observations': {'one': {'ticker': 'ABC', 'strategy': 'Daily', 'status': 'awaiting_future_prices',
                   'recorded_at': '2026-10-12T15:00:00+00:00'}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(forward_observations, 'market_schedule', side_effect=self.schedule):
            path = Path(directory) / 'forward.json'; write_json(path, initial)
            result = forward_observations.record_and_evaluate({'signals': []}, {'ABC': frame}, frame, path, '2026-11-20T22:00:00Z')
            item = result['observations']['one']
            self.assertEqual(item['entry_date'], '2026-10-13')
            self.assertEqual(item['status'], 'complete')
            self.assertLess(item['return_20_sessions_pct'], 10.)


if __name__ == '__main__':
    unittest.main()
