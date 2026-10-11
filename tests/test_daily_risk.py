import unittest
from unittest.mock import patch
from contextlib import ExitStack
from pathlib import Path
import tempfile
import numpy as np
import pandas as pd

import asset_profiles
import helpers
import notifier
from alpha_engine import check_overextension
from risk_policy import apply_daily_vstop, extension_pct, extension_warning
from signal_policy import add_signal_columns, context
from test_regressions import prices, NOW
from test_signal_policy import trend
from storage import read_json
from watchlist_history import update_trends
from forward_observations import record_and_evaluate


def risk_rows(closes, dates=None, ma50=90., adx=20.):
    dates = dates if dates is not None else pd.bdate_range('2026-10-01', periods=len(closes))
    close = np.array(closes, dtype=float)
    return pd.DataFrame({'Close': close, 'High': close + 1, 'Low': close - 1,
                         'MA50_DAILY': ma50, 'MA200_DAILY': 80., 'MA200_RISING': True,
                         'VSTOP_ATR': 2., 'VSTOP_ADX': adx}, index=pd.to_datetime(dates))


class DailyRiskTests(unittest.TestCase):
    def test_flip_needs_two_closes_in_both_directions(self):
        result = apply_daily_vstop(risk_rows([100, 94, 93, 99, 100]))
        self.assertEqual(list(result.VSTOP_TREND), [1, 1, -1, -1, 1])

    def test_recovery_or_touch_resets_confirmation(self):
        result = apply_daily_vstop(risk_rows([100, 94, 95, 94, 96, 94, 93]))
        self.assertEqual(list(result.VSTOP_TREND), [1, 1, 1, 1, 1, 1, -1])

    def test_gap_cannot_count_as_consecutive_sessions(self):
        frame = risk_rows([100, 94, 93], ['2026-10-05', '2026-10-06', '2026-10-08'])
        result = apply_daily_vstop(frame)
        self.assertEqual(list(result.VSTOP_TREND), [1, 1, 1])
        self.assertEqual(result.VSTOP_BREACH_SESSIONS.iloc[-1], 1)

    def test_exchange_holiday_does_not_break_confirmation(self):
        frame = risk_rows([100, 94, 93], ['2026-04-01', '2026-04-02', '2026-04-06'])
        self.assertEqual(apply_daily_vstop(frame).VSTOP_TREND.iloc[-1], -1)

    def test_multiplier_defends_at_ma50_and_never_loosens_stop(self):
        frame = risk_rows([100, 99, 102], ma50=99., adx=30.)
        result = apply_daily_vstop(frame)
        self.assertEqual(list(result.VSTOP_MULTIPLIER), [3.5, 2., 3.5])
        self.assertEqual(list(result.VSTOP_LINE), [94., 96., 96.])
        frame.loc[frame.index[1], 'Close'] = 98.
        self.assertEqual(apply_daily_vstop(frame).VSTOP_MULTIPLIER.iloc[1], 2.)
        self.assertEqual(apply_daily_vstop(risk_rows([100])).VSTOP_MULTIPLIER.iloc[0], 3.)

    def test_shield_suppresses_two_close_whipsaw_but_third_breach_exits(self):
        frame = risk_rows([100, 94, 93, 92])
        result = apply_daily_vstop(frame, pd.Series(True, index=frame.index))
        self.assertEqual(list(result.VSTOP_TREND), [1, 1, 1, -1])
        self.assertEqual(list(result.VSTOP_SHIELD_SUPPRESSED), [False, False, True, False])
        frame = risk_rows([100, 94, 93, 96])
        self.assertTrue(apply_daily_vstop(frame, pd.Series(True, index=frame.index)).VSTOP_TREND.eq(1).all())

    def test_shield_disables_on_ma50_ma200_or_metadata_failure(self):
        for field, value in [('MA50_DAILY', 93.), ('MA200_DAILY', 93.), ('MA200_RISING', False), ('metadata', False)]:
            frame = risk_rows([100, 94, 93])
            eligible = pd.Series(True, index=frame.index)
            if field == 'metadata':
                eligible.iloc[-1] = False
            else:
                frame.loc[frame.index[-1], field] = value
            self.assertEqual(apply_daily_vstop(frame, eligible).VSTOP_TREND.iloc[-1], -1, field)

    def test_missing_average_cannot_establish_risk_trend(self):
        frame = risk_rows([100], ma50=np.nan)
        self.assertTrue(pd.isna(apply_daily_vstop(frame).VSTOP_TREND.iloc[0]))

    def test_weekly_monthly_use_daily_ma50_and_daily_vstop(self):
        raw = prices()
        daily = helpers.calculate_technicals(raw, 'Daily', now=NOW)
        for timeframe in ['Weekly', 'Monthly']:
            result = helpers.calculate_technicals(raw, timeframe, now=NOW)
            date = result.index[-1]
            self.assertAlmostEqual(result.MA50.iloc[-1], raw.loc[:date].Close.tail(50).mean())
            self.assertNotAlmostEqual(result.MA50.iloc[-1], result.MA50_TIMEFRAME.iloc[-1])
            for field in ['VSTOP_LINE', 'VSTOP_TREND', 'VSTOP_MULTIPLIER']:
                self.assertEqual(result[field].iloc[-1], daily.loc[date, field])

    def test_partial_session_cannot_confirm_flip(self):
        raw = prices()
        changed = raw.copy()
        for field in ['Open', 'High', 'Low', 'Close']:
            changed.loc[changed.index[-1], field] = 1.
        now = '2026-10-09T18:00:00Z'
        pd.testing.assert_frame_equal(helpers.calculate_technicals(raw, 'Daily', now=now),
                                      helpers.calculate_technicals(changed, 'Daily', now=now))

    def test_all_entry_columns_are_strictly_ma50_gated(self):
        raw = prices(400)
        for field, delta in [('Open', 0), ('Close', 0), ('High', 1), ('Low', -1)]:
            raw[field] = np.r_[np.full(350, 100.), np.full(49, 80.), 85.] + delta
        raw.loc[raw.index[-1], 'Volume'] = 100000
        result = helpers.calculate_technicals(raw, 'Daily', now=NOW)
        for field in ['ACCUMULATION_DAY', 'POCKET_PIVOT', 'VSTOP_BUY_SIGNAL', 'BREAKOUT_TRIGGERED', 'PRICE_BREAKOUT_TRIGGERED', 'ENTRY_CANDIDATE']:
            self.assertFalse(result.loc[result.Close <= result.MA50, field].any(), field)
        frame = pd.DataFrame({'High': [90.] * 20 + [102.], 'Close': [89.] * 20 + [100.],
                              'MA50': 100., 'MA200_DAILY': 80., 'VSTOP_TREND': 1, 'BREAKOUT_TRIGGERED': True})
        result = add_signal_columns(frame)
        self.assertFalse(result.ENTRY_CANDIDATE.any())


class ExtensionTests(unittest.TestCase):
    def test_exact_metric_threshold_and_invalid_average(self):
        self.assertEqual(extension_pct(112.345, 100), ((112.345 - 100) / 100) * 100)
        self.assertEqual(extension_warning(extension_pct(112.345, 100)),
                         '⚠️ Extended — Wait for Pullback (12.34% above 50-DMA)')
        self.assertEqual(extension_warning(extension_pct(110, 100)), '')
        self.assertTrue(extension_warning(extension_pct(110.001, 100)))
        for ma in [0, None, np.nan, float('inf')]:
            self.assertIsNone(extension_pct(100, ma))

    def test_extended_breakout_still_gets_individual_alert_without_alignment(self):
        row = pd.Series({'Close': 125., 'MA50': 100., 'MA200_DAILY': 80., 'VSTOP_LINE': 110.})
        detail = context(row, row)
        self.assertEqual(detail['daily_ma50_distance_pct'], 25.)
        self.assertTrue(detail['extended'])
        self.assertEqual(notifier.route_signal('Daily', 'buy', trend('bearish'), None,
                         pd.Timestamp('2026-10-09'), price_breakout=True), 'alert')
        signal = {'context': detail, 'direction': 'buy', 'label': 'Confirmed price breakout', 'ticker': 'ABC',
                  'timeframe': 'Daily', 'date': '2026-10-09', 'fresh_period': True, 'reasons': ['resistance'],
                  'weekly_trend': 'bearish', 'monthly_trend': 'unknown', 'weekly_asof': '2026-10-09',
                  'monthly_asof': 'unavailable', 'rs_spy': None, 'rs_sector': None, 'sector_rank': 'N/A'}
        self.assertIn('⚠️ Extended — Wait for Pullback (25.00% above 50-DMA)', notifier.format_signal(signal, 'GREEN'))

    def test_legacy_extension_api_uses_same_warning_with_daily_anchors(self):
        frame = pd.DataFrame({'Close': [112.], 'MA50': [100.], 'MA200_DAILY': [80.]})
        result = check_overextension(frame)
        self.assertEqual(result['dist_50'], 12.)
        self.assertTrue(result['is_extended'])
        self.assertIn('12.00%', result['warning'])


class ProfileTests(unittest.TestCase):
    def test_classification_is_dated_and_requires_usd_equity_200bn(self):
        base = {'status': 'available', 'market_cap': 200_000_000_000, 'currency': 'USD', 'quote_type': 'EQUITY'}
        self.assertTrue(asset_profiles.is_mega_cap(base))
        for key, value in [('market_cap', 199_999_999_999), ('market_cap', np.nan), ('currency', 'GBp'), ('quote_type', 'ETF'), ('status', 'unavailable')]:
            self.assertFalse(asset_profiles.is_mega_cap(dict(base, **{key: value})))
        dates = pd.to_datetime(['2026-10-08', '2026-10-09', '2026-10-12'])
        mask = asset_profiles.mega_cap_mask(dates, {'observations': {'2026-10-09': base}})
        self.assertEqual(list(mask), [False, True, False])

    def test_metadata_failure_never_blocks_price_calculations_and_is_frozen(self):
        cache = {}
        with patch.object(asset_profiles.yf, 'Ticker', create=True, side_effect=RuntimeError('unavailable')) as ticker:
            first = asset_profiles.asset_profile('ABC', '2026-10-09', cache)
            second = asset_profiles.asset_profile('ABC', '2026-10-09', cache)
        self.assertEqual(ticker.call_count, 1)
        self.assertEqual(first, second)
        self.assertFalse(asset_profiles.mega_cap_mask(pd.to_datetime(['2026-10-09']), first).iloc[0])

    def test_provider_market_cap_is_cached_without_guessing_from_watchlist_group(self):
        with patch.object(asset_profiles.yf, 'Ticker', create=True) as ticker:
            ticker.return_value.get_info.return_value = {'marketCap': 250e9, 'currency': 'USD', 'quoteType': 'EQUITY'}
            result = asset_profiles.asset_profile('ABC', '2026-10-09', {})
        self.assertTrue(asset_profiles.is_mega_cap(result['observations']['2026-10-09']))


class RiskIntegrationTests(unittest.TestCase):
    def scan(self, daily_close=125., daily_sell=False, price_breakout=True):
        def frame(raw, timeframe, **kwargs):
            date = '2026-09-30' if timeframe == 'Monthly' else '2026-10-09'
            return pd.DataFrame({'Close': [daily_close if timeframe == 'Daily' else 125.],
                'MA50': [100.], 'MA200_DAILY': [80.], 'VSTOP_LINE': [110.],
                'VSTOP_SELL_SIGNAL': [daily_sell and timeframe == 'Daily'],
                'BREAKOUT_TRIGGERED': [not daily_sell], 'ENTRY_CANDIDATE': [not daily_sell],
                'PRICE_BREAKOUT_TRIGGERED': [price_breakout and not daily_sell],
                'RS_SCORE': [5.], 'RS_SECTOR_SCORE': [2.], 'TREND_STATE': ['bearish'],
                'BREAKOUT_LEVEL': [120.]}, index=pd.to_datetime([date]))
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / 'scan_snapshot.json'
            mocks = {'SNAPSHOT_FILE': path, 'ALERT_STATE_FILE': Path(directory) / 'alerts.json'}
            for name, value in mocks.items():
                stack.enter_context(patch.object(notifier, name, value))
            for name, kwargs in {
                'load_watchlist': {'return_value': {'ABC': {}}},
                'require_telegram_config': {}, 'calculate_technicals': {'side_effect': frame},
                'download_prices': {'return_value': pd.DataFrame({'Close': [100.]})},
                'asset_profile': {'return_value': {'observations': {}}},
                'upcoming_earnings': {'return_value': {'status': 'unavailable'}},
                'last_completed_session': {'return_value': pd.Timestamp('2026-10-09')},
            }.items():
                stack.enter_context(patch.object(notifier, name, **kwargs))
            send = stack.enter_context(patch.object(notifier, 'send_records'))
            notifier.run_automated_scanner()
            return read_json(path), send.call_args

    def test_old_period_buys_cannot_bypass_latest_daily_gate(self):
        for close in [99., 100.]:
            snapshot, _ = self.scan(daily_close=close)
            self.assertFalse(snapshot['signals'])

    def test_extended_breakout_reaches_telegram_with_warning_despite_bearish_alignment(self):
        snapshot, sent = self.scan()
        self.assertEqual(len(snapshot['signals']), 3)
        self.assertTrue(all(item['route'] == 'alert' for item in snapshot['signals']))
        self.assertFalse(any(item['higher_timeframe_aligned'] for item in snapshot['signals']))
        messages = [text for key, text in sent.args[0] if key.startswith('signal:')]
        self.assertEqual(len(messages), 3)
        self.assertTrue(all('⚠️ Extended — Wait for Pullback (25.00% above 50-DMA)' in text for text in messages))

    def test_confirmed_daily_exit_is_sent_even_below_ma50(self):
        snapshot, _ = self.scan(daily_close=90., daily_sell=True)
        self.assertEqual([(s['timeframe'], s['direction'], s['route']) for s in snapshot['signals']],
                         [('Daily', 'sell', 'alert')])

    def test_extended_setup_digest_keeps_the_exact_warning(self):
        _, sent = self.scan(price_breakout=False)
        digest = next(text for key, text in sent.args[0] if key.startswith('daily-digest:'))
        self.assertIn('⚠️ Extended — Wait for Pullback (25.00% above 50-DMA)', digest)

    def test_policy_migration_is_not_reported_as_market_deterioration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            update_trends({'ABC': {'Weekly': 'bullish'}}, '2026-10-08', path, policy='old')
            changes = update_trends({'ABC': {'Weekly': 'bearish'}}, '2026-10-09', path, policy='new')
            self.assertFalse(changes)
            changes = update_trends({'ABC': {'Weekly': 'bullish'}}, '2026-10-12', path, policy='new')
            self.assertEqual(changes[0]['policy'], 'new')
            self.assertEqual(changes[0]['from'], 'bearish')

    def test_visible_daily_breakouts_are_not_mislabeled_aligned_in_forward_study(self):
        snapshot = {'policy': 'daily-risk-v2', 'signals': [{'id': 'new', 'ticker': 'ABC', 'date': '2026-10-09',
                    'timeframe': 'Daily', 'direction': 'buy', 'fresh_period': True, 'route': 'alert',
                    'higher_timeframe_aligned': False}]}
        with tempfile.TemporaryDirectory() as directory:
            state = record_and_evaluate(snapshot, {}, pd.DataFrame(), Path(directory) / 'forward.json',
                                        '2026-10-11T07:00:00Z')
        self.assertEqual([item['strategy'] for item in state['observations'].values()], ['Daily'])


if __name__ == '__main__':
    unittest.main()
