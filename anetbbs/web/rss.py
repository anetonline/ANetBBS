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
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import func

from ..models import db, RssFeed, RssItem, RssReadStatus

rss_bp = Blueprint('rss', __name__, url_prefix='/rss')


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
    """Combined river — newest items across all active feeds."""
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 30
    pagination = (RssItem.query
                  .join(RssFeed)
                  .filter(RssFeed.is_active.is_(True))
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
    return render_template('rss/item.html', item=item)


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
