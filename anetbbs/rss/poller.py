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
import socket
import threading
from datetime import datetime, timedelta
from html import unescape
import re

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread = None
_HTML_TAG_RE = re.compile(r'<[^>]+>')

# Real gap found in a security/performance audit: feedparser.parse()
# has no timeout= parameter at all (confirmed against its real
# signature) and no connect/read deadline was ever applied around it
# -- a slow, unresponsive, or deliberately stalling feed server could
# hang this call indefinitely. Since _poll_loop below fetches feeds
# ONE AT A TIME on a single dedicated background thread, one hung feed
# blocked every OTHER active feed's refresh for as long as the hang
# lasted, with no upper bound at all. Bounding this caps the worst
# case for a full poll cycle at num_feeds * _FEED_FETCH_TIMEOUT
# instead of potentially forever.
_FEED_FETCH_TIMEOUT = 20


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
    from urllib.parse import urlparse
    from ..core.net_safety import resolve_safe_destination
    from ..models import db, RssFeed, RssItem

    with app.app_context():
        feed = RssFeed.query.get(feed_id)
        if not feed or not feed.is_active:
            return 0
        url = feed.url

        # Real gap found in a security/performance audit: feedparser.parse()
        # used to be called directly on the raw feed.url with no scheme/
        # host validation at all -- feedparser treats a bare path or a
        # file:// URI as a LOCAL FILE to read, not a network fetch
        # (confirmed empirically both forms are read straight off disk),
        # and had no restriction against an http(s) URL targeting an
        # internal-only address either. Reachable via the feed-URL admin
        # config field (including an already-compromised admin session).
        # Same reasoning as web_terminal.py's own SSRF guard (round 1's
        # Critical fix) -- reused here via the shared core.net_safety
        # helper extracted this round rather than a second copy.
        parsed_url = urlparse(url or '')
        if parsed_url.scheme not in ('http', 'https'):
            feed.last_error = 'feed URL must be a plain http(s):// URL'
            feed.last_fetched_at = datetime.utcnow()
            db.session.commit()
            logger.warning('RSS fetch refused for %s: non-http(s) URL', feed.name)
            return 0
        host = parsed_url.hostname
        if not host:
            feed.last_error = 'feed URL has no host'
            feed.last_fetched_at = datetime.utcnow()
            db.session.commit()
            return 0
        port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
        _family, _sockaddr, ssrf_err = resolve_safe_destination(host, port)
        if ssrf_err:
            feed.last_error = f'refused: {ssrf_err}'
            feed.last_fetched_at = datetime.utcnow()
            db.session.commit()
            logger.warning('RSS fetch refused for %s: %s', feed.name, ssrf_err)
            return 0

        try:
            # feedparser.parse() has no timeout= parameter of its own
            # (checked its real signature) -- socket.setdefaulttimeout()
            # is the standard, widely-used workaround for exactly this
            # gap. It's a PROCESS-GLOBAL setting, not thread-local, so
            # this saves/restores whatever was there before rather than
            # assuming it was unset, and restores it immediately after
            # this one call in a finally -- the window where it's
            # active is bounded to this fetch alone.
            _prev_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(_FEED_FETCH_TIMEOUT)
            try:
                parsed = feedparser.parse(url, request_headers={
                    'User-Agent': 'ANetBBS RSS reader (+https://github.com/anetonline/anetbbs)'
                })
            finally:
                socket.setdefaulttimeout(_prev_timeout)
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
            html = _get_content_html(entry)
            item = RssItem(
                feed_id=feed.id,
                guid=guid,
                title=(entry.get('title') or '')[:500],
                link=(entry.get('link') or '')[:1000],
                author=(entry.get('author') or '')[:200] or None,
                summary=_strip_html(entry.get('summary') or '')[:4000] or None,
                content_html=html,
                image_url=_extract_image_url(entry, html),
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
    contents = entry.get('content') or []
    if contents and isinstance(contents, list):
        for c in contents:
            value = c.get('value') if hasattr(c, 'get') else None
            if value:
                return value[:32000]
    summary = entry.get('summary') or ''
    if '<' in summary:
        return summary[:32000]
    return None


def _extract_image_url(entry, content_html):
    """Return the first image URL associated with this feed entry, or None."""
    # 1. media:thumbnail (e.g. YouTube, Flickr)
    thumbs = getattr(entry, 'media_thumbnail', None) or []
    for t in thumbs:
        url = (t.get('url') if hasattr(t, 'get') else None) or ''
        if url:
            return url[:1000]
    # 2. media:content with image/* type
    media = getattr(entry, 'media_content', None) or []
    for m in media:
        if not hasattr(m, 'get'):
            continue
        if m.get('type', '').startswith('image/'):
            url = m.get('url', '') or ''
            if url:
                return url[:1000]
    # 3. enclosures
    for enc in getattr(entry, 'enclosures', []):
        if not hasattr(enc, 'get'):
            continue
        if enc.get('type', '').startswith('image/'):
            url = enc.get('href', '') or enc.get('url', '') or ''
            if url:
                return url[:1000]
    # 4. first <img src> in HTML body
    if content_html:
        m = re.search(r'<img[^>]+src=["\']([^"\']{8,})', content_html, re.IGNORECASE)
        if m:
            return m.group(1)[:1000]
    return None


def _prune_old_items(app):
    """Delete RssItem rows older than the retention window.

    Real gap found in a security/performance audit: nothing ever
    pruned RssItem -- every poll cycle (default every 30 minutes) can
    add up to 200 new items per active feed, deduped by (feed_id,
    guid) so no duplicate re-inserts, but old items just accumulate
    forever. Over a long-running install with several active feeds,
    this table grows unboundedly, slowing its own queries/indexes down
    over time along with routine backups. RSS_ITEM_RETENTION_DAYS
    (env var, default 90) bounds it -- same env-var-override
    convention as RSS_POLL_INTERVAL above.

    RssReadStatus.item_id has ondelete='CASCADE' declared in the model,
    but that's only enforced by the DB engine itself if SQLite foreign-
    key enforcement is turned on (PRAGMA foreign_keys=ON) -- this
    project never sets that -- AND a bulk .delete() query bypasses
    SQLAlchemy's own ORM-level relationship cascade regardless (that
    only fires for objects deleted via db.session.delete(obj), not a
    bulk query). Deletes matching RssReadStatus rows explicitly first
    so pruning an item can't leave orphaned read-marker rows behind.
    """
    from ..models import db, RssItem, RssReadStatus
    try:
        days = int(os.environ.get('RSS_ITEM_RETENTION_DAYS', '90'))
    except ValueError:
        days = 90
    if days <= 0:
        return
    cutoff = datetime.utcnow() - timedelta(days=days)
    with app.app_context():
        try:
            stale_ids = [
                row[0] for row in
                db.session.query(RssItem.id).filter(
                    db.or_(
                        db.and_(RssItem.published_at.isnot(None),
                               RssItem.published_at < cutoff),
                        db.and_(RssItem.published_at.is_(None),
                               RssItem.fetched_at < cutoff),
                    )
                ).all()
            ]
            if not stale_ids:
                return
            (RssReadStatus.query
             .filter(RssReadStatus.item_id.in_(stale_ids))
             .delete(synchronize_session=False))
            (RssItem.query
             .filter(RssItem.id.in_(stale_ids))
             .delete(synchronize_session=False))
            db.session.commit()
            logger.info('RSS: pruned %d item(s) older than %d days',
                       len(stale_ids), days)
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            logger.warning('RSS item pruning failed: %s', exc)


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
            if not _stop_event.is_set():
                _prune_old_items(app)
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
