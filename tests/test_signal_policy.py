from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import pandas as pd

import signal_policy as policy
import notifier
import notifications
import earnings_context
from storage import read_json
from watchlist_history import update_selection, update_trends


def trend(value, date='2026-10-09'):
    return pd.DataFrame({'TREND_STATE': [value]}, index=pd.to_datetime([date]))


class SignalPolicyTests(unittest.TestCase):
    def test_resistance_excludes_current_high_and_is_not_repeated(self):
        frame = pd.DataFrame({'High': [100.] * 20 + [200., 210.], 'Close': [95.] * 20 + [110., 205.],
            'MA50': 80., 'MA200_DAILY': 70., 'VSTOP_TREND': 1, 'BREAKOUT_TRIGGERED': False})
        result = policy.add_signal_columns(frame)
        self.assertEqual(result.BREAKOUT_LEVEL.iloc[20], 100.)
        self.assertTrue(result.PRICE_BREAKOUT_TRIGGERED.iloc[20])
        self.assertFalse(result.PRICE_BREAKOUT_TRIGGERED.iloc[21])

    def test_accumulation_is_not_mislabelled_price_breakout(self):
        row = pd.Series({'PRICE_BREAKOUT_TRIGGERED': False, 'ACCUMULATION_DAY': True})
        self.assertEqual(policy.signal_label(row), 'Momentum setup')
        self.assertEqual(policy.reasons(row), ['accumulation'])

    def test_daily_alert_requires_known_bullish_weekly_and_monthly(self):
        date = pd.Timestamp('2026-10-09')
        self.assertEqual(notifier.route_signal('Daily', 'buy', trend('bullish'), trend('bullish', '2026-09-30'), date), 'alert')
        for monthly in [None, trend('unknown'), trend('bearish'), trend('mixed')]:
            self.assertEqual(notifier.route_signal('Daily', 'buy', trend('bullish'), monthly, date), 'digest')
        self.assertEqual(notifier.route_signal('Weekly', 'sell', trend('bearish'), trend('bullish'), date), 'alert')

    def test_future_weekly_close_cannot_align_earlier_daily_signal(self):
        self.assertFalse(policy.aligned(trend('bullish', '2026-10-09'), trend('bullish', '2026-09-30'), pd.Timestamp('2026-10-08')))

    def test_stop_above_close_is_not_shown_as_positive_stop_distance(self):
        row = pd.Series({'Close': 100., 'VSTOP_LINE': 110., 'BREAKOUT_LEVEL': 105.})
        detail = policy.context(row, None)
        self.assertIsNone(detail['stop_distance_pct'])
        self.assertLess(detail['breakout_distance_pct'], 0)


class HistoryTests(unittest.TestCase):
    def test_partial_selection_does_not_remove_leadership(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            update_selection('sector_scan', {'ABC': {'selection_reason': 'Leader'}}, ['ABC'], '2026-10-02', path=path)
            update_selection('sector_scan', {}, [], '2026-10-09', complete=False, path=path)
            self.assertTrue(read_json(path)['stocks']['ABC']['sources']['sector_scan']['active'])
            update_selection('sector_scan', {}, [], '2026-10-09', path=path)
            self.assertFalse(read_json(path)['stocks']['ABC']['sources']['sector_scan']['active'])
            self.assertEqual(read_json(path)['stocks']['ABC']['addition_reason'], 'Leader')

    def test_initial_observation_does_not_fabricate_addition_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            update_selection('sector_scan', {'OLD': {}}, [], '2026-10-09', path=path)
            self.assertNotIn('added_at', read_json(path)['stocks']['OLD'])

    def test_unknown_data_does_not_erase_last_valid_trend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            update_trends({'ABC': {'Weekly': 'bullish'}}, '2026-10-02', path=path)
            update_trends({'ABC': {'Weekly': 'unknown'}}, '2026-10-09', path=path)
            changes = update_trends({'ABC': {'Weekly': 'bearish'}}, '2026-10-09', path=path)
            self.assertEqual(changes[0]['from'], 'bullish')

    def test_digest_marks_individual_signals_only_after_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch.object(notifications, 'send_telegram', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):
                    notifications.send_records([('digest', 'summary')], path, {'digest': ['signal:A', 'signal:B']})
            self.assertFalse(path.exists())
            with patch.object(notifications, 'send_telegram'):
                notifications.send_records([('digest', 'summary')], path, {'digest': ['signal:A', 'signal:B']})
            self.assertEqual(set(read_json(path)['delivered']), {'digest', 'signal:A', 'signal:B'})


class EarningsTests(unittest.TestCase):
    def test_provider_date_label_and_missing_date_are_honest(self):
        dates = pd.DataFrame({'EPS Estimate': [1., 1.]}, index=pd.to_datetime(['2026-07-20', '2026-10-15']))
        with patch.object(earnings_context.yf, 'Ticker', create=True) as ticker:
            ticker.return_value.get_earnings_dates.return_value = dates
            result = earnings_context.upcoming_earnings('ABC', datetime(2026, 10, 11, tzinfo=timezone.utc))
        self.assertEqual(result['date'], '2026-10-15')
        self.assertTrue(result['within_7_days'])
        self.assertIn('may change', earnings_context.describe_earnings(result))
        with patch.object(earnings_context.yf, 'Ticker', create=True, side_effect=RuntimeError('provider failed')):
            result = earnings_context.upcoming_earnings('ABC')
        self.assertEqual(result['status'], 'unavailable')


class ScannerIntegrationTests(unittest.TestCase):
    def test_snapshot_and_routing_preserve_context_and_digest(self):
        def frame(raw, timeframe, **kwargs):
            date = '2026-09-30' if timeframe == 'Monthly' else '2026-10-09'
            return pd.DataFrame({'Close': [100.], 'MA50': [80.], 'MA200_DAILY': [70.],
                'VSTOP_LINE': [90.], 'VSTOP_SELL_SIGNAL': [False], 'BREAKOUT_TRIGGERED': [True],
                'ENTRY_CANDIDATE': [True], 'PRICE_BREAKOUT_TRIGGERED': [False],
                'POCKET_PIVOT': [True], 'RS_SCORE': [5.], 'RS_SECTOR_SCORE': [2.],
                'TREND_STATE': ['bearish' if timeframe == 'Monthly' else 'bullish'], 'BREAKOUT_LEVEL': [105.]},
                index=pd.to_datetime([date]))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(notifier, 'SNAPSHOT_FILE', Path(directory) / 'scan_snapshot.json'), \
             patch.object(notifier, 'ALERT_STATE_FILE', Path(directory) / 'alert_state.json'), \
             patch.object(notifier, 'load_watchlist', return_value={'ABC': {}}), \
             patch.object(notifier, 'require_telegram_config'), \
             patch.object(notifier, 'download_prices', return_value=pd.DataFrame({'Close': [100.]})), \
             patch.object(notifier, 'calculate_technicals', side_effect=frame), \
             patch.object(notifier, 'upcoming_earnings', return_value={'status': 'unavailable'}), \
             patch.object(notifier, 'last_completed_session', return_value=pd.Timestamp('2026-10-09')), \
             patch.object(notifier, 'send_records') as send:
            notifier.run_automated_scanner()
            snapshot = read_json(Path(directory) / 'scan_snapshot.json')
            self.assertEqual(len(snapshot['signals']), 3)
            daily = next(item for item in snapshot['signals'] if item['timeframe'] == 'Daily')
            self.assertEqual(daily['route'], 'digest')
            self.assertEqual(daily['label'], 'Momentum setup')
            self.assertEqual(daily['context']['stop_distance_pct'], 10.)
            self.assertIn('signal:ABC:Daily:2026-10-09:buy', next(iter(send.call_args.kwargs['acknowledgements'].values())))


if __name__ == '__main__':
    unittest.main()
