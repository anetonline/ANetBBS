"""Background RSS / Atom poller.

Periodically fetches every active RssFeed, parses with feedparser,
upserts items into RssItem (deduped by GUID).

The poller runs as a background daemon thread started from web_app.py
on app boot. Default interval is 1800 seconds (30 minutes) — tunable
via the ``RSS_POLL_INTERVAL`` env var.

For an immediate fetch (when sysop adds a new feed or hits "refresh"
in the admin UI), call :func:`fetch_one_now`.
"""
import os
import logging
import threading
from datetime import datetime
from html import unescape
import re

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread = None
_HTML_TAG_RE = re.compile(r'<[^>]+>')


def _strip_html(text):
    """Strip HTML tags + collapse whitespace. Preserves the readable
    content for the summary preview without leaving raw markup in the
    listing pages."""
    if not text:
        return ''
    cleaned = _HTML_TAG_RE.sub(' ', text)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _parse_published(entry):
    """Best-effort UTC datetime from a feedparser entry."""
    for field in ('published_parsed', 'updated_parsed', 'created_parsed'):
        t = getattr(entry, field, None)
        if t:
            try:
                return datetime(*t[:6])
            except (TypeError, ValueError):
                pass
    return None


def _import_one_feed(app, feed_id):
    """Fetch one feed and persist any new items. Returns count of inserted
    items. Updates feed.last_fetched_at and feed.last_error."""
    import feedparser
    from ..models import db, RssFeed, RssItem

    with app.app_context():
        feed = RssFeed.query.get(feed_id)
        if not feed or not feed.is_active:
            return 0
        url = feed.url
        try:
            parsed = feedparser.parse(url, request_headers={
                'User-Agent': 'ANetBBS RSS reader (+https://github.com/anetonline/anetbbs)'
            })
        except Exception as exc:  # pylint: disable=broad-except
            feed.last_error = f'fetch failed: {exc}'
            feed.last_fetched_at = datetime.utcnow()
            db.session.commit()
            logger.warning('RSS fetch failed for %s: %s', feed.name, exc)
            return 0

        if parsed.bozo and not parsed.entries:
            feed.last_error = f'parse failed: {parsed.bozo_exception!r}'
            feed.last_fetched_at = datetime.utcnow()
            db.session.commit()
            logger.warning('RSS parse failed for %s: %s', feed.name,
                           parsed.bozo_exception)
            return 0

        # Pull site_url from the parsed feed if not set
        if not feed.site_url:
            site_link = parsed.feed.get('link') or ''
            if site_link:
                feed.site_url = site_link[:500]

        added = 0
        for entry in parsed.entries[:200]:   # cap at 200 per fetch
            guid = (entry.get('id')
                    or entry.get('guid')
                    or entry.get('link')
                    or entry.get('title') or '')[:500]
            if not guid:
                continue
            if RssItem.query.filter_by(feed_id=feed.id, guid=guid).first():
                continue
            item = RssItem(
                feed_id=feed.id,
                guid=guid,
                title=(entry.get('title') or '')[:500],
                link=(entry.get('link') or '')[:1000],
                author=(entry.get('author') or '')[:200] or None,
                summary=_strip_html(entry.get('summary') or '')[:4000] or None,
                content_html=_get_content_html(entry),
                published_at=_parse_published(entry),
            )
            db.session.add(item)
            added += 1

        feed.last_fetched_at = datetime.utcnow()
        feed.last_error = None
        try:
            db.session.commit()
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            logger.warning('RSS commit failed for %s: %s', feed.name, exc)
            return 0
        if added:
            logger.info('RSS: %s — %d new item(s)', feed.name, added)
        return added


def _get_content_html(entry):
    """Extract the best-available HTML body from a feedparser entry."""
    # atom:content (full content) is preferred over summary
    contents = entry.get('content') or []
    if contents and isinstance(contents, list):
        for c in contents:
            value = c.get('value') if hasattr(c, 'get') else None
            if value:
                return value[:32000]   # cap at 32k
    # Fall back to summary if it's HTML-formatted (i.e., contains tags)
    summary = entry.get('summary') or ''
    if '<' in summary:
        return summary[:32000]
    return None


def fetch_one_now(feed_id):
    """Trigger an immediate fetch for one feed. Used by admin ‘refresh’
    button and the add-feed flow so users see items right away.
    Returns count of new items."""
    from flask import current_app
    try:
        app = current_app._get_current_object()
    except Exception:
        return 0
    return _import_one_feed(app, feed_id)


def _poll_loop(app, interval):
    logger.info('RSS poller loop started — interval %ds', interval)
    # Slight initial delay so first run doesn't race app startup.
    _stop_event.wait(15)
    while not _stop_event.is_set():
        try:
            from ..models import RssFeed
            with app.app_context():
                feed_ids = [f.id for f in RssFeed.query.filter_by(
                    is_active=True).all()]
            for fid in feed_ids:
                if _stop_event.is_set():
                    break
                try:
                    _import_one_feed(app, fid)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception('RSS poller: feed %d crashed: %s',
                                     fid, exc)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('RSS poller loop error: %s', exc)
        # Wait until next tick. Wakes early if app is shutting down.
        _stop_event.wait(interval)


def start_poller(app):
    """Start the RSS background poller. Idempotent — calling twice is safe."""
    global _thread
    if _thread and _thread.is_alive():
        return
    try:
        interval = int(os.environ.get('RSS_POLL_INTERVAL', '1800'))
    except ValueError:
        interval = 1800
    if interval < 60:
        interval = 60
    _stop_event.clear()
    _thread = threading.Thread(target=_poll_loop, args=(app, interval),
                                daemon=True, name='rss-poller')
    _thread.start()


def stop_poller():
    """Signal the poller thread to exit."""
    _stop_event.set()
