from datetime import datetime, timezone, timedelta
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import news_agent
import news_content as news

NOW = datetime(2026, 10, 11, 7, tzinfo=timezone.utc)


def entry(title='Chip capacity expansion announced - Source', url='https://example.com/article', date=None):
    return {'title': title, 'link': url, 'published': date or (NOW - timedelta(hours=2)).isoformat(),
            'source': {'title': 'Source', 'href': 'https://example.com'}}


class StorySelectionTests(unittest.TestCase):
    def test_old_undated_future_and_duplicate_stories_are_excluded(self):
        feeds = {'AI & Tech': [entry(), entry(url='https://other.example.com/same'),
            entry('Old', date='2024-01-01T00:00:00Z'),
            dict(entry('Undated'), published=''),
            entry('Future', date=(NOW + timedelta(days=1)).isoformat())]}
        selected, counts = news.select_stories(feeds, {}, NOW)
        self.assertEqual(len(selected), 1)
        self.assertEqual(counts['old_or_undated'], 3)
        self.assertEqual(counts['duplicates'], 1)

    def test_successful_delivery_prevents_repeat_on_next_brief(self):
        feeds = {'AI': [entry()]}
        stories, _ = news.select_stories(feeds, {}, NOW)
        state = news.remember_sent({}, stories, NOW)
        selected, _ = news.select_stories(feeds, state, NOW + timedelta(hours=24))
        self.assertEqual(selected, [])

    def test_backlog_is_bounded_even_after_long_outage(self):
        selected, _ = news.select_stories({'AI': [entry(date=(NOW - timedelta(days=4)).isoformat())]},
                                        {'last_sent': (NOW - timedelta(days=20)).isoformat()}, NOW)
        self.assertEqual(selected, [])

    def test_similar_event_across_topics_is_not_repeated(self):
        feeds = {'AI': [entry('Company opens new chip factory in Texas - First')],
                 'Markets': [entry('Company opens new chip factory in Texas - Second', url='https://example.com/two')]}
        stories, _ = news.select_stories(feeds, {}, NOW)
        self.assertEqual(len(stories), 1)

    def test_original_five_topics_remain_even_without_news(self):
        body, links = news.render_topics([], news_agent.TOPICS_AND_FEEDS)
        self.assertEqual(body.count('<h3>'), 5)
        for topic in news_agent.TOPICS_AND_FEEDS:
            import html
            self.assertIn(f'<h3>{html.escape(topic)}</h3>', body)
        self.assertEqual(links, {})
        self.assertIn('No fresh', news.restore_source_links(body, links, allow_empty=True))


class LinkTests(unittest.TestCase):
    def test_all_25_links_and_entities_survive_mime(self):
        links = {f'LINK_ID_{i}': f'https://example.com/{i}?a=1&b=two' for i in range(1, 26)}
        body = ''.join(f'<a href="{key}">source</a>' for key in links)
        with patch.dict('os.environ', {'USER_EMAIL': 'test@example.invalid', 'APP_PASSWORD': 'dummy'}), \
             patch.object(news_agent.smtplib, 'SMTP') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {}
            news_agent.send_email(body, links)
            message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        class Links(HTMLParser):
            def __init__(self): super().__init__(); self.links = []
            def handle_starttag(self, tag, attrs):
                if tag == 'a': self.links.append(dict(attrs)['href'])
        parser = Links(); parser.feed(parsed.get_body(preferencelist=('html',)).get_content())
        self.assertEqual(parser.links, list(links.values()))
        self.assertIsNotNone(parsed.get_body(preferencelist=('plain',)))

    def test_additional_malformed_or_invented_href_is_rejected(self):
        for href in ['LINK_ID_typo', 'https://invented.example.com/story', 'javascript:alert(1)', '']:
            body = '<a href="LINK_ID_1">valid</a><a href="' + href + '">bad</a>'
            with self.assertRaises(ValueError):
                news.restore_source_links(body, {'LINK_ID_1': 'https://example.com/story'})

    def test_active_html_is_rejected(self):
        with self.assertRaises(ValueError):
            news.restore_source_links('<script>alert(1)</script>', {}, allow_empty=True)

    def test_private_destination_and_credentials_are_rejected(self):
        for url in ['http://127.0.0.1/x', 'http://169.254.169.254/latest', 'http://host.local/x',
                    'https://user:pass@example.com/x', 'file:///etc/passwd', 'https://example.com:8443/x']:
            self.assertFalse(news.public_url(url))
        with patch.object(news.socket, 'getaddrinfo', return_value=[(None, None, None, None, ('10.0.0.1', 80))]):
            self.assertFalse(news.public_destination('https://example.com/story'))

    def test_google_200_is_not_claimed_as_publisher_verified(self):
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = [b'<html>Google News</html>']
        with patch.object(news, 'public_destination', return_value=True), patch.object(news.requests, 'get', return_value=response):
            result = news.check_link({'url': 'https://news.google.com/rss/articles/abc', 'source_url': 'https://example.com'})
        self.assertEqual(result['status'], 'redirect')

    def test_verified_publisher_redirect_and_broken_link_are_distinct(self):
        redirect = Mock(status_code=302, headers={'Location': 'https://example.com/story'})
        ok = Mock(status_code=200, headers={})
        with patch.object(news, 'public_destination', return_value=True), patch.object(news.requests, 'get', side_effect=[redirect, ok]):
            result = news.check_link({'url': 'https://news.google.com/rss/articles/abc', 'source_url': 'https://example.com'})
        self.assertEqual(result, {'url': 'https://example.com/story', 'status': 'publisher'})
        with patch.object(news, 'public_destination', return_value=True), patch.object(news.requests, 'get', return_value=Mock(status_code=404)):
            result = news.check_link({'url': 'https://example.com/deleted'})
        self.assertEqual(result['status'], 'broken')

    def test_private_redirect_is_not_requested(self):
        response = Mock(status_code=302, headers={'Location': 'http://127.0.0.1/secret'})
        with patch.object(news, 'public_destination', side_effect=[True, False]), patch.object(news.requests, 'get', return_value=response) as get:
            news.check_link({'url': 'https://example.com/story'})
        self.assertEqual(get.call_count, 1)


class SummaryTests(unittest.TestCase):
    def test_invented_number_falls_back_to_original_headlines(self):
        with patch.object(news_agent, 'generate_summary', return_value='{"LINK_ID_1":{"theme":"Revenue","summary":"Revenue rose 99%."}}'):
            result = news_agent.summarize_stories([{'id': 'LINK_ID_1', 'title': 'Revenue increases', 'source': 'Example'}], '')
        self.assertEqual(result, {})

    def test_failed_smtp_does_not_advance_news_state(self):
        with tempfile.TemporaryDirectory() as directory, patch('storage.BASE_DIR', Path(directory)), \
             patch.dict('os.environ', {'USER_EMAIL': 'test@example.invalid', 'APP_PASSWORD': 'dummy'}), \
             patch.object(news_agent, 'build_briefing', return_value=('<p>No fresh stories</p>', {}, [])), \
             patch.object(news_agent, 'send_email', side_effect=RuntimeError('SMTP failed')):
            with self.assertRaises(RuntimeError):
                news_agent.run_briefing()
            self.assertFalse((Path(directory) / 'news_state.json').exists())


if __name__ == '__main__':
    unittest.main()
