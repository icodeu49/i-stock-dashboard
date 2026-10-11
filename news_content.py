"""Dated source stories, conservative link checks and deterministic email rendering."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser
import requests

UTC = timezone.utc
LINK_LABELS = {
    'publisher': 'Publisher page reachable',
    'redirect': 'Google News redirect; publisher not verified',
    'blocked': 'Access restricted or paywall possible',
    'unverified': 'Destination could not be verified',
    'broken': 'Source link unavailable',
}


def public_url(url):
    """Reject credentials, malformed URLs and local destinations before any request."""
    if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\s\\<>\x00-\x1f]', url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
        if parts.scheme not in {'http', 'https'} or parts.username or parts.password or parts.port not in {None, 80, 443}:
            return False
        if '.' not in host or host.lower().endswith(('.localhost', '.local', '.internal')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return bool(re.fullmatch(r'[A-Za-z0-9.-]+', host))
    except ValueError:
        return False


def public_destination(url):
    if not public_url(url):
        return False
    host = urlsplit(url).hostname
    try:
        addresses = socket.getaddrinfo(host, None)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False


def canonical_key(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith('utm_') and k not in {'oc', 'gclid', 'fbclid'}]
    clean = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip('/'), urlencode(query), ''))
    return hashlib.sha256(clean.encode()).hexdigest()[:24]


def title_key(title):
    # RSS titles commonly append the publisher; do not treat that as a new story.
    title = title.rsplit(' - ', 1)[0].casefold()
    return ' '.join(re.findall(r'\w+', title))


def same_story(a, b):
    if not a or not b:
        return False
    left, right = set(a.split()), set(b.split())
    overlap = len(left & right) / max(1, min(len(left), len(right)))
    return a == b or SequenceMatcher(None, a, b).ratio() >= .83 or (min(len(left), len(right)) >= 6 and overlap >= .8)


def parse_date(value):
    if not value:
        return None
    try:
        result = parsedate_to_datetime(str(value))
    except (ValueError, TypeError, OverflowError):
        try:
            result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def fetch_feed(topic, url):
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=(5, 15), headers={'User-Agent': 'i-stock-dashboard/2.0'})
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if not feed.entries:
                raise ValueError('empty feed')
            return feed.entries[:30]
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise RuntimeError(f'News feed unavailable: {topic}') from None
    return []


def select_stories(feeds, state, now=None, per_topic=2):
    now = now or datetime.now(UTC)
    # Allow a weekend/outage catch-up, but never pass old search hits as new news.
    previous = parse_date(state.get('last_sent'))
    cutoff = max(now - timedelta(hours=72), previous) if previous else now - timedelta(hours=48)
    seen = [item for item in state.get('seen', {}).values()
            if (parse_date(item.get('sent_at')) or datetime.min.replace(tzinfo=UTC)) >= now - timedelta(days=7)]
    used_titles = [item.get('title_key', '') for item in seen]
    used_urls = {item.get('url_key') for item in seen}
    selected, counts = [], {'old_or_undated': 0, 'duplicates': 0, 'invalid_links': 0}
    for topic, entries in feeds.items():
        eligible = []
        for entry in entries:
            title, url = str(entry.get('title', '')).strip(), entry.get('link', '')
            date = parse_date(entry.get('published'))
            if not title or date is None or date < cutoff or date > now + timedelta(minutes=5):
                counts['old_or_undated'] += 1
                continue
            if not public_url(url):
                counts['invalid_links'] += 1
                continue
            source = entry.get('source', {})
            source = source if isinstance(source, dict) else {}
            # Prefer primary institutions and financially relevant developments within each original topic.
            host = urlsplit(source.get('href', '')).hostname or ''
            primary = int(host.endswith(('.gov', '.edu')) or host in {'reuters.com', 'www.reuters.com', 'apnews.com'})
            material = sum(word in title.casefold() for word in ('earnings', 'guidance', 'revenue', 'capacity', 'contract', 'chip', 'memory', 'power', 'rate', 'inflation'))
            eligible.append((primary, material, date, {'topic': topic, 'title': title,
                'url': url, 'source': source.get('title', 'Source'), 'source_url': source.get('href', ''),
                'published': date.isoformat(), 'title_key': title_key(title), 'url_key': canonical_key(url)}))
        eligible.sort(key=lambda item: item[:3], reverse=True)
        taken = 0
        for _, _, _, story in eligible:
            if story['url_key'] in used_urls or any(same_story(story['title_key'], old) for old in used_titles):
                counts['duplicates'] += 1
                continue
            if taken >= per_topic:
                break
            story['id'] = f'LINK_ID_{len(selected) + 1}'
            selected.append(story)
            used_titles.append(story['title_key'])
            used_urls.add(story['url_key'])
            taken += 1
    return selected, counts


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])


def check_link(story):
    original, current = story['url'], story['url']
    source_host = urlsplit(story.get('source_url', '')).hostname or ''
    try:
        for _ in range(6):
            if not public_destination(current):
                return {'url': original, 'status': 'unverified'}
            response = requests.get(current, timeout=(4, 8), allow_redirects=False, stream=True,
                                    headers={'User-Agent': 'Mozilla/5.0 (compatible; StockBriefing/2.0)'})
            try:
                code = response.status_code
                if code in {301, 302, 303, 307, 308}:
                    target = response.headers.get('Location')
                    if not target:
                        return {'url': original, 'status': 'broken'}
                    current = urljoin(current, target)
                    continue
                if code in {404, 410}:
                    return {'url': original, 'status': 'broken'}
                if code in {401, 402, 403, 429, 451}:
                    return {'url': original, 'status': 'blocked'}
                if code != 200:
                    return {'url': original, 'status': 'unverified'}
                host = urlsplit(current).hostname or ''
                if host != 'news.google.com':
                    expected = source_host.removeprefix('www.')
                    actual = host.removeprefix('www.')
                    if expected and (actual == expected or actual.endswith('.' + expected)) and urlsplit(current).path not in {'', '/'}:
                        return {'url': current, 'status': 'publisher'}
                    return {'url': original, 'status': 'unverified'}
                # A 200 response from Google alone does not verify the publisher.
                content = bytearray()
                for chunk in response.iter_content(8192):
                    content.extend(chunk)
                    if len(content) >= 200_000:
                        break
                parser = PageLinks()
                parser.feed(bytes(content).decode('utf-8', errors='replace'))
                targets = [urljoin(current, link) for link in parser.links]
                matches = [link for link in targets if source_host and public_url(link)
                           and (urlsplit(link).hostname or '').removeprefix('www.') == source_host.removeprefix('www.')
                           and urlsplit(link).path not in {'', '/'}]
                if len(set(matches)) == 1:
                    current = matches[0]
                    continue
                return {'url': original, 'status': 'redirect'}
            finally:
                response.close()
    except (requests.RequestException, OSError, ValueError):
        pass
    return {'url': original, 'status': 'unverified'}


def check_links(stories):
    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = list(pool.map(check_link, stories))
    for story, result in zip(stories, checks):
        story['link_check'] = result
    return stories


class SafeEmail(HTMLParser):
    """Only known source anchors and inert formatting can reach the email."""
    tags = {'h2', 'h3', 'ul', 'li', 'strong', 'b', 'p', 'br', 'small', 'hr', 'em'}

    def __init__(self, link_map):
        super().__init__(convert_charrefs=True)
        self.link_map, self.parts, self.anchors = link_map, [], 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            key = attrs.get('href', '')
            if key not in self.link_map or not public_url(self.link_map[key]):
                raise ValueError('Email contains an unknown or invalid source anchor')
            self.parts.append(f'<a href="{html.escape(self.link_map[key], quote=True)}">')
            self.anchors += 1
        elif tag in self.tags:
            self.parts.append(f'<{tag}>')
        else:
            raise ValueError('Email contains unsupported HTML')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.tags | {'a'} and tag not in {'br', 'hr'}:
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if 'LINK_ID_' in data:
            raise ValueError('Unresolved source placeholder outside an anchor')
        self.parts.append(html.escape(data))


def restore_source_links(content, link_map, allow_empty=False):
    parser = SafeEmail(link_map)
    parser.feed(content)
    parser.close()
    if not parser.anchors and not (allow_empty and not link_map):
        raise ValueError('Briefing contains no source links')
    return ''.join(parser.parts)


def render_topics(stories, topics, notes=None, warnings=None):
    notes, warnings = notes or {}, warnings or []
    parts = ['<p>Headline-based briefing. Publication times are UTC; interpretation beyond the supplied headlines is excluded.</p>']
    if warnings:
        parts.append('<p><strong>Coverage:</strong> ' + html.escape('; '.join(warnings)) + '</p>')
    links = {}
    for topic in topics:
        parts.append(f'<h3>{html.escape(topic)}</h3><ul>')
        selected = [story for story in stories if story['topic'] == topic]
        if not selected:
            parts.append('<li>No fresh, non-repeated headlines available for this topic.</li>')
        for story in selected:
            note = notes.get(story['id'], {})
            theme = note.get('theme', 'Source headline')
            summary = note.get('summary', story['title'])
            check = story.get('link_check', {'url': story['url'], 'status': 'unverified'})
            parts.append(f'<li><strong>{html.escape(theme)}:</strong> {html.escape(summary)} ')
            if check['status'] != 'broken':
                links[story['id']] = check['url']
                parts.append(f'<a href="{story["id"]}">({html.escape(story["source"])})</a>')
            else:
                parts.append(f'({html.escape(story["source"])})')
            date = parse_date(story['published']).strftime('%d %b %Y %H:%M UTC')
            parts.append(f'<br><small>Published {date} · {html.escape(LINK_LABELS[check["status"]])}</small><br><br></li>')
        parts.append('</ul>')
    return ''.join(parts), links


def remember_sent(state, stories, now=None):
    now = now or datetime.now(UTC)
    state = dict(state)
    seen = {key: value for key, value in state.get('seen', {}).items()
            if (parse_date(value.get('sent_at')) or datetime.min.replace(tzinfo=UTC)) >= now - timedelta(days=7)}
    for story in stories:
        seen[story['url_key']] = {'url_key': story['url_key'], 'title_key': story['title_key'], 'sent_at': now.isoformat()}
    state.update(last_sent=now.isoformat(), seen=seen)
    return state
