"""An independent supplement; never rewrites the original five news sections."""
from datetime import datetime, timedelta, timezone
import html
import re
from urllib.parse import urlsplit
import yfinance as yf

from market_data import last_completed_session
from news_content import select_stories, check_links, render_topics, parse_date

HEADING = '<hr><h2>Your Watchlist &amp; Confirmed Signals</h2>'


def render_comparison(report, forward):
    parts = ['<h3>Signal assessment</h3>']
    if report.get('results'):
        parts.append(f'<p>Historical comparison through {html.escape(report["asof"])} '
                     f'(coverage: {html.escape(report.get("coverage", "unknown"))}). Historical holdout from '
                     f'{html.escape(report.get("holdout_start", "unknown"))}:</p><ul>')
        for item in report['results']:
            if item['window'] == 'historical_holdout':
                small = ' · small sample' if item.get('small_sample') else ''
                parts.append(f'<li>{html.escape(item["strategy"])}: {item["total_return_pct"]:+.1f}% return, '
                             f'{item["max_drawdown_pct"]:.1f}% maximum drawdown, {item["trades"]} trades{small}.</li>')
        parts.append('</ul><p>Same costs and exits; current-watchlist selection bias remains. Historical results do not establish a superior future strategy.</p>')
    else:
        parts.append('<p>Historical comparison has not completed yet.</p>')
    observations = list(forward.get('observations', {}).values())
    mature = [item for item in observations if item.get('status') == 'complete']
    parts.append(f'<p>Forward study: {len(observations)} recorded signal/strategy observations; {len(mature)} have 20 subsequent trading sessions. '
                 'This study starts from the first market open after each observation and is separate from the historical portfolio test.</p>')
    return ''.join(parts)


def selected_symbols(watchlist, snapshot, now, limit=8):
    priorities = []
    signals = sorted(snapshot.get('signals', []), key=lambda item: (
        item.get('timeframe') != 'Weekly', item.get('label') != 'Confirmed price breakout', item.get('ticker', '')))
    for item in signals:
        ticker = item.get('ticker')
        if ticker in watchlist and ticker not in priorities:
            priorities.append(ticker)
    focus = priorities[:limit // 2]
    remaining = sorted(set(watchlist) - set(focus))
    if remaining:
        offset = (now.date().toordinal() * (limit - len(focus))) % len(remaining)
        rotated = remaining[offset:] + remaining[:offset]
        focus += rotated[:limit - len(focus)]
    return focus


def fetch_company_news(ticker):
    """Use provider ticker associations, or a resolved company name in the headline."""
    search = yf.Search(ticker, max_results=3, news_count=8, lists_count=0, timeout=10)
    quote = next((q for q in search.quotes if q.get('symbol') == ticker), {})
    name = quote.get('longname') or quote.get('shortname') or ''
    prefix = re.sub(r'[,\s]+(Inc\.?|Corporation|Corp\.?|Ltd\.?|plc).*$', '', name, flags=re.I).strip()
    entries = []
    for item in search.news:
        content = item.get('content', item)
        title = content.get('title', '')
        related = list(item.get('relatedTickers') or content.get('relatedTickers') or [])
        related += [s.get('symbol') for s in content.get('finance', {}).get('stockTickers', [])]
        ticker_match = len(ticker) >= 3 and re.search(r'(?<![A-Za-z])' + re.escape(ticker) + r'(?![A-Za-z])', title)
        name_match = len(prefix) >= 4 and prefix.casefold() in title.casefold()
        if ticker not in related and not ticker_match and not name_match:
            continue
        url = content.get('link') or content.get('canonicalUrl', {}).get('url') or content.get('clickThroughUrl', {}).get('url')
        date = content.get('pubDate')
        stamp = content.get('providerPublishTime')
        if not date and isinstance(stamp, (int, float)):
            date = datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
        if not url:
            continue
        provider = content.get('publisher') or content.get('provider', {}).get('displayName') or 'Yahoo Finance'
        parts = urlsplit(url)
        entries.append({'title': title, 'link': url, 'published': date,
                        'source': {'title': provider, 'href': f'{parts.scheme}://{parts.netloc}'}})
    return entries


def render_scan_context(watchlist, snapshot, history, state, now):
    parts = [HEADING]
    expected = last_completed_session(now).date().isoformat()
    snapshot_date = snapshot.get('session')
    generated = parse_date(snapshot.get('generated_at'))
    current = snapshot_date == expected and generated is not None and generated <= now + timedelta(minutes=5)
    if not current:
        parts.append(f'<p><strong>Scan coverage:</strong> current snapshot unavailable. Expected US close {expected}; '
                     f'available snapshot {html.escape(str(snapshot_date or "none"))}. No fresh signals are asserted.</p>')
        return ''.join(parts), []
    parts.append(f'<p>US close {snapshot_date} · {snapshot.get("scanned", 0)}/{len(watchlist)} stocks scanned · '
                 f'Market regime: {html.escape(snapshot.get("regime", "unknown"))} · '
                 f'Coverage: {html.escape(snapshot.get("coverage", "unknown"))}.</p>')
    if snapshot.get('failures'):
        parts.append('<p>Data gaps: ' + html.escape('; '.join(snapshot['failures'])[:800]) + '</p>')
    sent_ids = set(state.get('signal_ids', []))
    candidates = [item for item in snapshot.get('signals', []) if item.get('route') == 'alert' and item['id'] not in sent_ids]
    candidates.sort(key=lambda item: (item.get('timeframe') != 'Weekly', item.get('label') != 'Confirmed price breakout'))
    selected = candidates[:10]
    parts.append('<h3>Completed-period signals</h3><ul>')
    if not selected:
        parts.append('<li>No new qualifying signals since your previous briefing.</li>')
    for item in selected:
        close = item.get('context', {}).get('close')
        price = f'USD {close:.2f}' if isinstance(close, (int, float)) else 'unavailable'
        label = html.escape(item['label'])
        text = f"<strong>{html.escape(item['ticker'])} — {label}</strong>: {html.escape(item['timeframe'])} close {item['date']}, {price}. "
        text += 'Trigger: ' + html.escape(', '.join(item.get('reasons', []))) + '. '
        text += f"Weekly {html.escape(item.get('weekly_trend', 'unknown'))}; monthly {html.escape(item.get('monthly_trend', 'unknown'))}."
        if not item.get('fresh_period'):
            text += ' Earlier completed-period setup, newly included in this briefing.'
        earnings = item.get('earnings', {})
        if earnings.get('date'):
            text += f" Earnings calendar: {earnings['date']} (Yahoo; date may change)."
        parts.append(f'<li>{text}<br><br></li>')
    parts.append('</ul>')
    if len(candidates) > len(selected):
        parts.append(f'<p>{len(candidates) - len(selected)} additional dated signals are available in the scan snapshot.</p>')
    digest = [item for item in snapshot.get('signals', []) if item.get('route') == 'digest']
    if digest:
        parts.append('<p><strong>Daily setups without full alignment:</strong> ' + html.escape(', '.join(item['ticker'] for item in digest[:20])) +
                     f' ({len(digest)} total). These do not qualify for individual daily alerts.</p>')
    cutoff = parse_date(state.get('last_sent')) or now - timedelta(hours=48)
    events = [event for event in history.get('events', [])
              if (parse_date(event.get('observed_at')) or datetime.min.replace(tzinfo=timezone.utc)) > cutoff]
    parts.append('<h3>Watchlist changes</h3><ul>')
    if not events:
        parts.append('<li>No recorded additions or leadership/trend changes since the previous briefing.</li>')
    for event in events[-12:]:
        kind = event.get('type')
        if kind == 'added':
            detail = 'Added: ' + event.get('reason', event.get('source', 'automated selection'))
        elif kind == 'selection_lost':
            detail = f"No longer selected by {event.get('source')}; remains on your watchlist"
        else:
            detail = f"{event.get('timeframe')} trend: {event.get('from')} → {event.get('to')}"
        parts.append(f'<li><strong>{html.escape(event["ticker"])}</strong>: {html.escape(detail)} (US close {event["asof"]}).</li>')
    parts.append('</ul>')
    return ''.join(parts), [item['id'] for item in selected]


def build_supplement(watchlist, snapshot, history, state, original_stories, now):
    body, signal_ids = render_scan_context(watchlist, snapshot, history, state, now)
    symbols = selected_symbols(watchlist, snapshot, now)
    feeds, failures = {}, []
    for ticker in symbols:
        try:
            feeds[ticker] = fetch_company_news(ticker)
        except Exception:
            failures.append(ticker)
            feeds[ticker] = []
    # Original sections have first choice; matching stories are not repeated below.
    selection_state = {**state, 'seen': dict(state.get('seen', {}))}
    for story in original_stories:
        selection_state['seen'][story['url_key']] = {'url_key': story['url_key'], 'title_key': story['title_key'], 'sent_at': now.isoformat()}
    stories, _ = select_stories(feeds, selection_state, now, per_topic=1)
    stories = stories[:5]
    for index, story in enumerate(stories, len(original_stories) + 1):
        story['id'] = f'LINK_ID_{index}'
    check_links(stories)
    body += '<h3>Watchlist news</h3><p>Search covers ' + html.escape(', '.join(symbols)) + \
            f' ({len(symbols)} selected stocks of {len(watchlist)}; recent signals plus a rotating sample).</p>'
    if failures:
        body += '<p>Company-news lookup unavailable: ' + html.escape(', '.join(failures)) + '.</p>'
    links = {}
    if not stories:
        body += '<p>No fresh, non-repeated company headlines found in this selection.</p>'
    for story in stories:
        section, source_links = render_topics([story], [story['topic']])
        # Retain the shared, safe story renderer, with its source date and link status.
        section = section[section.index('<h3>'):]
        body += section
        links.update(source_links)
    print(f'Watchlist supplement: {len(signal_ids)} signals, {len(stories)} company headlines, {len(failures)} lookup gaps.')
    return body, links, stories, signal_ids
