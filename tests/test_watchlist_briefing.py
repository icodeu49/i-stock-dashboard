from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import news_agent
import news_content
import watchlist_briefing as briefing
from storage import read_json

NOW = datetime(2026, 10, 11, 7, tzinfo=timezone.utc)


def snapshot():
    return {'session': '2026-10-09', 'generated_at': '2026-10-11T06:30:00Z', 'scanned': 1,
        'coverage': 'complete', 'signals': [{'id': 'ABC:Weekly:2026-10-09', 'ticker': 'ABC',
            'timeframe': 'Weekly', 'date': '2026-10-09', 'route': 'alert', 'label': 'Momentum setup',
            'context': {'close': 100.}, 'reasons': ['pocket pivot'], 'fresh_period': True}]}


class SupplementTests(unittest.TestCase):
    def test_original_topic_content_remains_unchanged_before_separate_section(self):
        original, _ = news_content.render_topics([], news_agent.TOPICS_AND_FEEDS)
        with patch.object(briefing, 'fetch_company_news', return_value=[]):
            extra, links, stories, ids = briefing.build_supplement({'ABC': {}}, snapshot(), {}, {}, [], NOW)
        combined = original + extra
        self.assertEqual(combined[:combined.index('<hr>')], original)
        self.assertIn('<h2>Your Watchlist &amp; Confirmed Signals</h2>', combined)
        self.assertIn('Momentum setup', extra)
        self.assertNotIn('Confirmed price breakout', extra)
        self.assertEqual(ids, ['ABC:Weekly:2026-10-09'])

    def test_stale_snapshot_is_not_presented_as_current_signal(self):
        old = snapshot(); old['session'] = '2026-10-08'
        body, ids = briefing.render_scan_context({'ABC': {}}, old, {}, {}, NOW)
        self.assertIn('current snapshot unavailable', body)
        self.assertNotIn('Momentum setup', body)
        self.assertEqual(ids, [])

    def test_previous_email_signals_are_suppressed(self):
        body, ids = briefing.render_scan_context({'ABC': {}}, snapshot(), {}, {'signal_ids': ['ABC:Weekly:2026-10-09']}, NOW)
        self.assertEqual(ids, [])
        self.assertIn('No new qualifying signals', body)

    def test_company_news_requires_identity_match(self):
        search = Mock(quotes=[{'symbol': 'ABC', 'shortname': 'Alpha Devices Inc.'}], news=[
            {'title': 'Other company reports earnings', 'link': 'https://example.com/wrong',
             'providerPublishTime': 1791702000},
            {'title': 'Alpha Devices expands production', 'link': 'https://example.com/right',
             'providerPublishTime': 1791702000, 'relatedTickers': ['ABC'], 'publisher': 'Example'}])
        with patch.object(briefing.yf, 'Search', create=True, return_value=search):
            entries = briefing.fetch_company_news('ABC')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['link'], 'https://example.com/right')

    def test_short_ambiguous_ticker_does_not_match_unrelated_headline(self):
        search = Mock(quotes=[], news=[{'title': 'U turn in economic policy', 'link': 'https://example.com/wrong',
                                      'providerPublishTime': 1791702000}])
        with patch.object(briefing.yf, 'Search', create=True, return_value=search):
            self.assertEqual(briefing.fetch_company_news('U'), [])

    def test_supplement_failure_does_not_stop_original_briefing(self):
        with tempfile.TemporaryDirectory() as directory, patch('storage.BASE_DIR', Path(directory)), \
             patch.dict('os.environ', {'USER_EMAIL': 'test@example.invalid', 'APP_PASSWORD': 'dummy'}), \
             patch.object(news_agent, 'build_briefing', return_value=('<h3>AI &amp; Tech</h3><p>Original</p>', {}, [])), \
             patch.object(briefing, 'build_supplement', side_effect=RuntimeError('optional unavailable')), \
             patch.object(news_agent, 'send_email') as send:
            news_agent.run_briefing()
            self.assertTrue(send.call_args.args[0].startswith('<h3>AI &amp; Tech</h3><p>Original</p>'))
            self.assertIn('supplement unavailable', send.call_args.args[0])
            self.assertIn('last_sent', read_json(Path(directory) / 'news_state.json'))

    def test_rejected_supplement_cannot_acknowledge_undelivered_signals(self):
        with tempfile.TemporaryDirectory() as directory, patch('storage.BASE_DIR', Path(directory)), \
             patch.dict('os.environ', {'USER_EMAIL': 'test@example.invalid', 'APP_PASSWORD': 'dummy'}), \
             patch.object(news_agent, 'build_briefing', return_value=('<h3>AI &amp; Tech</h3><p>Original</p>', {}, [])), \
             patch.object(briefing, 'build_supplement', return_value=('<a href="LINK_ID_typo">Bad</a>', {}, [], ['unsent'])), \
             patch.object(news_agent, 'send_email') as send:
            news_agent.run_briefing()
            state = read_json(Path(directory) / 'news_state.json')
            self.assertEqual(state['signal_ids'], [])
            self.assertNotIn('LINK_ID_typo', send.call_args.args[0])
            self.assertIn('Original', send.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
