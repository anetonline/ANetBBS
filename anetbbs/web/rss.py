"""RSS / Atom reader.

Browse feeds the sysop has subscribed at /admin/rss/. Per-user read
state via RssReadStatus. The background poller (anetbbs.rss.poller)
fetches items on a schedule.

Routes:
    GET  /rss/                — list of feeds + unread counts
    GET  /rss/all             — combined "river" of all items, newest first
    GET  /rss/<feed_id>       — items in one feed
    GET  /rss/item/<item_id>  — single item, marks as read
    POST /rss/<feed_id>/mark_read   — mark all items in feed as read
    POST /rss/mark_all_read   — mark every item in every feed as read
    GET  /r/<item_id>         — short-URL redirect to original article link
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from sqlalchemy import func
import bleach
from markupsafe import Markup

from ..models import db, RssFeed, RssItem, RssReadStatus

# Tags and attributes we allow through when rendering feed HTML bodies.
_ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    'p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'img', 'figure', 'figcaption', 'picture', 'source',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'blockquote', 'pre', 'code',
    'div', 'span', 'section', 'article', 'header', 'footer',
    'hr', 'sub', 'sup', 'small', 'strong', 'em', 'b', 'i',
    'del', 's', 'u', 'abbr', 'cite', 'q', 'mark',
]
_ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    'a':      ['href', 'title', 'rel', 'target'],
    'img':    ['src', 'alt', 'title', 'width', 'height', 'loading', 'class', 'style'],
    'source': ['src', 'srcset', 'type', 'media'],
    'td':     ['colspan', 'rowspan', 'align'],
    'th':     ['colspan', 'rowspan', 'align', 'scope'],
    '*':      ['class'],
}


def _sanitize_html(html):
    """Sanitize feed HTML for safe browser rendering. Returns a Markup string."""
    if not html:
        return Markup('')
    clean = bleach.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS,
                         strip=True, strip_comments=True)
    # Make bare links clickable
    clean = bleach.linkify(clean, skip_tags=['pre', 'code'])
    return Markup(clean)

rss_bp = Blueprint('rss', __name__, url_prefix='/rss')

# Short-URL redirect blueprint — no login required so external browsers work.
redirect_bp = Blueprint('redirect', __name__, url_prefix='/r')


@redirect_bp.route('/<int:item_id>')
def short_redirect(item_id):
    """Redirect /r/<item_id> → the original RSS article URL."""
    item = RssItem.query.get(item_id)
    if not item or not item.link:
        abort(404)
    return redirect(item.link, code=302)


def _unread_counts():
    """Return {feed_id: unread_count} for the current user, across all feeds."""
    if not current_user.is_authenticated:
        return {}
    # Items per feed minus items the user has marked read.
    total_per_feed = dict(
        db.session.query(RssItem.feed_id, func.count(RssItem.id))
        .group_by(RssItem.feed_id).all())
    read_per_feed = dict(
        db.session.query(RssItem.feed_id, func.count(RssReadStatus.id))
        .join(RssReadStatus, RssReadStatus.item_id == RssItem.id)
        .filter(RssReadStatus.user_id == current_user.id)
        .group_by(RssItem.feed_id).all())
    return {fid: total_per_feed.get(fid, 0) - read_per_feed.get(fid, 0)
            for fid in total_per_feed}


@rss_bp.route('/')
@login_required
def index():
    """List all active feeds with unread counts."""
    feeds = (RssFeed.query.filter_by(is_active=True)
             .order_by(RssFeed.sort_order, RssFeed.name).all())
    # NOTE: variable is `feed_unread` not `unread` — base.html does
    # `{% set unread = ... %}` for the PM badge counter, which would
    # shadow our context var inside child templates.
    feed_unread = _unread_counts()
    total_unread = sum(feed_unread.values())
    return render_template('rss/index.html',
                           feeds=feeds, feed_unread=feed_unread,
                           total_unread=total_unread)


@rss_bp.route('/all')
@login_required
def river():
    """Combined river — newest items across all active feeds.

    Filter mirrors evaluate_access(current_user, feed.min_access_level,
    bypass_admin=False) -- kept as a SQL predicate (paginated query, can't
    correctly filter after LIMIT/OFFSET) rather than a per-item Python
    call. bypass_admin=False here is a deliberate preservation of existing
    behavior, not a new decision: an admin below a feed's min_access_level
    was already excluded from this page before this file was touched, and
    RssFeed has no is_sysop_only column to gate on separately.
    """
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 30
    pagination = (RssItem.query
                  .join(RssFeed)
                  .filter(RssFeed.is_active.is_(True),
                          RssFeed.min_access_level <= current_user.access_level)
                  .order_by(RssItem.published_at.desc().nullslast())
                  .paginate(page=page, per_page=per_page, error_out=False))
    # Read-state lookup
    read_ids = set()
    if current_user.is_authenticated and pagination.items:
        item_ids = [i.id for i in pagination.items]
        read_ids = set(r[0] for r in db.session.query(RssReadStatus.item_id)
                       .filter(RssReadStatus.user_id == current_user.id,
                               RssReadStatus.item_id.in_(item_ids)).all())
    return render_template('rss/river.html',
                           pagination=pagination, read_ids=read_ids)


@rss_bp.route('/<int:feed_id>')
@login_required
def view_feed(feed_id):
    feed = RssFeed.query.get_or_404(feed_id)
    page = max(1, int(request.args.get('page', 1) or 1))
    pagination = (RssItem.query.filter_by(feed_id=feed.id)
                  .order_by(RssItem.published_at.desc().nullslast())
                  .paginate(page=page, per_page=30, error_out=False))
    read_ids = set()
    if pagination.items:
        item_ids = [i.id for i in pagination.items]
        read_ids = set(r[0] for r in db.session.query(RssReadStatus.item_id)
                       .filter(RssReadStatus.user_id == current_user.id,
                               RssReadStatus.item_id.in_(item_ids)).all())
    return render_template('rss/feed.html',
                           feed=feed, pagination=pagination, read_ids=read_ids)


@rss_bp.route('/item/<int:item_id>')
@login_required
def view_item(item_id):
    item = RssItem.query.get_or_404(item_id)
    # Mark as read (idempotent — UniqueConstraint catches dupes)
    if not RssReadStatus.query.filter_by(
            user_id=current_user.id, item_id=item.id).first():
        try:
            db.session.add(RssReadStatus(user_id=current_user.id,
                                         item_id=item.id))
            db.session.commit()
        except Exception:
            db.session.rollback()
    content_safe = _sanitize_html(item.content_html or item.summary or '')
    # Prev/next items within the same feed for navigation
    prev_item = (RssItem.query.filter(RssItem.feed_id == item.feed_id,
                                      RssItem.published_at > item.published_at)
                 .order_by(RssItem.published_at.asc()).first())
    next_item = (RssItem.query.filter(RssItem.feed_id == item.feed_id,
                                      RssItem.published_at < item.published_at)
                 .order_by(RssItem.published_at.desc()).first())
    return render_template('rss/item.html', item=item, content_safe=content_safe,
                           prev_item=prev_item, next_item=next_item)


@rss_bp.route('/<int:feed_id>/mark_read', methods=['POST'])
@login_required
def mark_feed_read(feed_id):
    feed = RssFeed.query.get_or_404(feed_id)
    # Find unread items in this feed and create read-status rows for each.
    unread = (db.session.query(RssItem.id)
              .outerjoin(RssReadStatus,
                         (RssReadStatus.item_id == RssItem.id) &
                         (RssReadStatus.user_id == current_user.id))
              .filter(RssItem.feed_id == feed.id)
              .filter(RssReadStatus.id.is_(None)).all())
    for (iid,) in unread:
        db.session.add(RssReadStatus(user_id=current_user.id, item_id=iid))
    db.session.commit()
    flash(f'Marked {len(unread)} item(s) read in {feed.name}.', 'success')
    return redirect(url_for('rss.view_feed', feed_id=feed.id))


@rss_bp.route('/mark_all_read', methods=['POST'])
@login_required
def mark_all_read():
    unread = (db.session.query(RssItem.id)
              .outerjoin(RssReadStatus,
                         (RssReadStatus.item_id == RssItem.id) &
                         (RssReadStatus.user_id == current_user.id))
              .filter(RssReadStatus.id.is_(None)).all())
    for (iid,) in unread:
        db.session.add(RssReadStatus(user_id=current_user.id, item_id=iid))
    db.session.commit()
    flash(f'Marked {len(unread)} items read.', 'success')
    return redirect(url_for('rss.index'))
