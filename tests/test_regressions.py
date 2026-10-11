import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import helpers
import market_data
import news_agent
import notifications
import notifier
import portfolio_backtest
import sector_notifier
from storage import load_watchlist, merge_candidates, read_json, write_json

NOW = pd.Timestamp('2026-10-11T06:00:00Z')


def prices(count=756, end='2026-10-09'):
    close = np.linspace(50, 150, count)
    return pd.DataFrame({'Open': close, 'High': close + 1, 'Low': close - 1,
        'Close': close, 'Volume': np.full(count, 1000.)},
        index=pd.bdate_range(end=end, periods=count))


class MarketDataTests(unittest.TestCase):
    def test_early_close_is_completed_after_settlement_delay(self):
        self.assertEqual(market_data.last_completed_session('2026-11-27T18:16:00Z'), pd.Timestamp('2026-11-27'))

    def test_dst_and_winter_closes(self):
        self.assertEqual(market_data.last_completed_session('2026-03-09T20:16:00Z'), pd.Timestamp('2026-03-09'))
        self.assertEqual(market_data.last_completed_session('2026-01-09T20:30:00Z'), pd.Timestamp('2026-01-08'))

    def test_good_friday_week_finishes_thursday(self):
        df = prices(100, '2026-04-02')
        result = market_data.resample_prices(df, 'Weekly', '2026-04-02T20:30:00Z')
        self.assertEqual(result.index[-1], pd.Timestamp('2026-04-02'))

    def test_incomplete_month_is_removed(self):
        result = market_data.resample_prices(prices(), 'Monthly', NOW)
        self.assertEqual(result.index[-1], pd.Timestamp('2026-09-30'))

    def test_incomplete_week_is_removed(self):
        result = market_data.resample_prices(prices(300, '2026-10-07'), 'Weekly', '2026-10-07T22:00:00Z')
        self.assertEqual(result.index[-1], pd.Timestamp('2026-10-02'))

    def test_invalid_latest_prices_fail_freshness(self):
        df = prices()
        df.loc[df.index[-1], 'Close'] = np.nan
        with patch.object(market_data.yf, 'download', return_value=df):
            with self.assertRaises(market_data.DataQualityError):
                market_data.download_prices('TEST', now=NOW, attempts=1)

    def test_current_intraday_row_is_discarded(self):
        df = prices(300, '2026-10-09')
        with patch.object(market_data.yf, 'download', return_value=df):
            result = market_data.download_prices('TEST', now='2026-10-09T18:00:00Z', attempts=1)
        self.assertEqual(result.index[-1], pd.Timestamp('2026-10-08'))


class TechnicalTests(unittest.TestCase):
    def test_three_year_stock_has_weekly_indicators(self):
        result = helpers.calculate_technicals(prices(), 'Weekly', now=NOW)
        self.assertIn('VSTOP_TREND', result)
        self.assertLess(len(result), 200)

    def test_200_day_guardrail_blocks_entry(self):
        df = prices(400)
        close = np.r_[np.full(300, 200.), np.linspace(200, 50, 60), np.linspace(50, 130, 40)]
        for col, delta in [('Open', 0), ('Close', 0), ('High', 1), ('Low', -1)]:
            df[col] = close + delta
        row = helpers.calculate_technicals(df, 'Daily', now=NOW).iloc[-1]
        self.assertLess(row.Close, row.MA200_DAILY)
        self.assertFalse(row.BREAKOUT_TRIGGERED)

    def test_weekly_guardrail_uses_daily_average(self):
        raw = prices()
        result = helpers.calculate_technicals(raw, 'Weekly', now=NOW)
        self.assertAlmostEqual(result.MA200_DAILY.iloc[-1], raw.Close.tail(200).mean())

    def test_missing_benchmark_is_unknown(self):
        result = helpers.calculate_technicals(prices(), 'Daily', now=NOW)
        self.assertTrue(result.RS_SCORE.isna().all())

    def test_benchmark_generates_real_relative_strength(self):
        raw, spy = prices(), prices()
        for col, delta in [('Open', 0), ('Close', 0), ('High', 1), ('Low', -1)]:
            spy[col] = 100 + delta
        result = helpers.calculate_technicals(raw, 'Daily', spy_df=spy, now=NOW)
        self.assertGreater(result.RS_SCORE.iloc[-1], 0)

    def test_inputs_not_mutated(self):
        raw = prices()
        original = raw.copy()
        helpers.calculate_technicals(raw, 'Weekly', now=NOW)
        pd.testing.assert_frame_equal(raw, original)


class PersistenceAndDeliveryTests(unittest.TestCase):
    def test_existing_watchlist_metadata_survives_merge(self):
        data = {'AAPL': {'group': 'My holdings', 'custom': 'keep'}}
        added = merge_candidates(data, {'AAPL': {'group': 'Sector Leaders', 'sector_etf': 'XLK'},
            'NEW': {'group': 'Sector Leaders', 'sector_etf': 'XLK'}})
        self.assertEqual(added, ['NEW'])
        self.assertEqual(data['AAPL']['group'], 'My holdings')
        self.assertEqual(data['AAPL']['custom'], 'keep')
        self.assertEqual(data['AAPL']['sector_etf'], 'XLK')

    def test_corrupt_watchlist_is_not_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'watchlist.json'
            path.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError):
                load_watchlist(path)
            self.assertEqual(path.read_text(), '{broken')

    def test_repeat_records_are_not_resent(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(notifications, 'send_telegram') as send:
            path = Path(directory) / 'state.json'
            records = [('signal:AAPL:Daily:2026-10-09:buy', 'Example signal')]
            notifications.send_records(records, path)
            notifications.send_records(records, path)
            self.assertEqual(send.call_count, 1)

    def test_failed_chunk_does_not_mark_undelivered_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            records = [('one', 'a' * 2000), ('two', 'b' * 2000)]
            with patch.object(notifications, 'send_telegram', side_effect=[None, RuntimeError('failure')]):
                with self.assertRaises(RuntimeError):
                    notifications.send_records(records, path)
            state = read_json(path)
            self.assertIn('one', state['delivered'])
            self.assertNotIn('two', state['delivered'])
            with patch.object(notifications, 'send_telegram') as send:
                notifications.send_records(records, path)
                self.assertEqual(send.call_count, 1)

    def test_telegram_rejection_propagates(self):
        response = Mock(status_code=400)
        response.json.return_value = {'ok': False}
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'dummy', 'TELEGRAM_CHAT_ID': 'dummy'}), patch.object(notifications.requests, 'post', return_value=response):
            with self.assertRaises(RuntimeError):
                notifications.send_telegram('test')

    def test_sector_scan_actually_saves_new_leaders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'watchlist.json'
            original = {'AAPL': {'group': 'My holdings'}}
            with patch.object(sector_notifier, 'WATCHLIST_FILE', path), \
                 patch.object(sector_notifier, 'load_watchlist', return_value=original), \
                 patch.object(sector_notifier, 'require_telegram_config'), \
                 patch.object(sector_notifier, 'calculate_momentum_score', return_value=1.), \
                 patch.object(sector_notifier, 'download_prices', return_value=prices()), \
                 patch.object(sector_notifier, 'rank_individual_stocks', side_effect=lambda stocks, *a: stocks[:2]), \
                 patch.object(sector_notifier, 'send_records'), \
                 patch.object(sector_notifier, 'last_completed_session', return_value=pd.Timestamp('2026-10-09')):
                sector_notifier.run_macro_rotation_pipeline()
            result = load_watchlist(path)
            self.assertIn('MSFT', result)
            self.assertEqual(result['MSFT']['sector_etf'], 'XLK')
            self.assertEqual(result['AAPL']['group'], 'My holdings')

    def test_scanner_reports_failures_and_exits_unsuccessfully(self):
        with patch.object(notifier, 'require_telegram_config'), \
             patch.object(notifier, 'load_watchlist', return_value={'BAD': {}}), \
             patch.object(notifier, 'download_prices', side_effect=[prices(), RuntimeError('stale')]), \
             patch.object(notifier, 'last_completed_session', return_value=pd.Timestamp('2026-10-09')), \
             patch.object(notifier, 'send_records') as send:
            with self.assertRaises(RuntimeError):
                notifier.run_automated_scanner()
            self.assertIn('Partial', send.call_args.args[0][-1][1])


class NewsTests(unittest.TestCase):
    def test_link_ids_do_not_collide(self):
        result = news_agent.restore_links('<a href="LINK_ID_1">one</a><a href="LINK_ID_10">ten</a>',
            {'LINK_ID_1': 'https://example.com/first', 'LINK_ID_10': 'https://example.com/tenth'})
        self.assertIn('href="https://example.com/tenth"', result)
        self.assertNotIn('first0', result)

    def test_unknown_link_rejected(self):
        with self.assertRaises(ValueError):
            news_agent.restore_links('<a href="LINK_ID_999">bad</a>', {})

    def test_model_temporary_failures_retried(self):
        class TemporaryFailure(Exception):
            code = 503
        with patch.object(news_agent, '_generate_summary', side_effect=[TemporaryFailure(), TemporaryFailure(), 'ok']) as generate, patch.object(news_agent.time, 'sleep'):
            self.assertEqual(news_agent.generate_summary('news', 'style'), 'ok')
            self.assertEqual(generate.call_count, 3)

    def test_fallback_escapes_headlines_and_preserves_links(self):
        result = news_agent.fallback_digest('Title: A < B\nLinkID: LINK_ID_10\nSource: Example\n')
        self.assertIn('A &lt; B', result)
        self.assertIn('href="LINK_ID_10"', result)

    def test_email_failure_propagates(self):
        server = Mock()
        server.__enter__ = Mock(return_value=server)
        server.__exit__ = Mock(return_value=False)
        server.send_message.side_effect = RuntimeError('SMTP failed')
        with patch.dict('os.environ', {'USER_EMAIL': 'test@example.invalid', 'APP_PASSWORD': 'dummy'}), patch.object(news_agent.smtplib, 'SMTP', return_value=server):
            with self.assertRaises(RuntimeError):
                news_agent.send_email('<a href="LINK_ID_1">test</a>', {'LINK_ID_1': 'https://example.com'})

    def test_missing_recipient_fails_before_any_email_send(self):
        with patch.dict('os.environ', {'USER_EMAIL': '', 'APP_PASSWORD': 'dummy'}), patch.object(news_agent.smtplib, 'SMTP') as smtp:
            with self.assertRaises(RuntimeError):
                news_agent.send_email('<a href="LINK_ID_1">test</a>', {'LINK_ID_1': 'https://example.com'})
            smtp.assert_not_called()



class BacktestTests(unittest.TestCase):
    def test_signals_execute_at_next_session_open_and_return_three_values(self):
        dates = pd.bdate_range('2026-10-05', periods=4)
        raw = pd.DataFrame({'Open': [100., 120., 130., 150.], 'Close': [110., 125., 140., 155.]}, index=dates)
        signals = pd.DataFrame({'Close': [110., 125., 140., 155.],
            'MA50': 80., 'MA200_DAILY': 90.,
            'VSTOP_BUY_SIGNAL': [True, False, False, False],
            'VSTOP_SELL_SIGNAL': [False, False, True, False]}, index=dates)
        with patch.object(portfolio_backtest, 'download_prices', return_value=raw) as download, patch.object(portfolio_backtest, 'calculate_technicals', return_value=signals):
            summary, trades, curve = portfolio_backtest.run_backtest_simulation('AAPL', 100)
        self.assertEqual(trades.iloc[0]['Entry Date'], '2026-10-06')
        self.assertEqual(trades.iloc[0]['Exit Date'], '2026-10-08')
        self.assertAlmostEqual(summary['Net Profit ($)'], 1000.)
        self.assertEqual([call.args[0] for call in download.call_args_list], ['SPY', 'AAPL'])


class SectorMomentumTests(unittest.TestCase):
    def test_return_windows_count_intervals_not_rows(self):
        df = prices(64)
        with patch.object(sector_notifier, 'download_prices', return_value=df):
            score = sector_notifier.calculate_momentum_score('XLK')
        close = df.Close
        expected = sum(weight * (close.iloc[-1] / close.iloc[-n-1] - 1) * 100
            for n, weight in [(5, .4), (21, .4), (63, .2)])
        self.assertAlmostEqual(score, expected)


if __name__ == '__main__':
    unittest.main()
