"""Admin CRUD for RSS feeds — sysop adds/edits/removes feeds."""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash)
from flask_login import login_required, current_user

from ..models import db, RssFeed, RssItem
from .access_control import require_admin as _admin_required

rss_admin_bp = Blueprint('rss_admin', __name__, url_prefix='/admin/rss')


@rss_admin_bp.route('/')
@login_required
@_admin_required
def index():
    feeds = RssFeed.query.order_by(RssFeed.sort_order, RssFeed.name).all()
    counts = {f.id: RssItem.query.filter_by(feed_id=f.id).count() for f in feeds}
    return render_template('rss_admin/index.html', feeds=feeds, counts=counts)


@rss_admin_bp.route('/add', methods=['GET', 'POST'])
@login_required
@_admin_required
def add():
    if request.method == 'POST':
        return _save(None)
    return render_template('rss_admin/edit.html', feed=None)


@rss_admin_bp.route('/<int:feed_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit(feed_id):
    feed = RssFeed.query.get_or_404(feed_id)
    if request.method == 'POST':
        return _save(feed)
    return render_template('rss_admin/edit.html', feed=feed)


def _save(feed):
    """Shared form-save for add/edit."""
    name = (request.form.get('name') or '').strip()
    url = (request.form.get('url') or '').strip()
    site_url = (request.form.get('site_url') or '').strip()
    description = (request.form.get('description') or '').strip()
    category = (request.form.get('category') or 'general').strip()
    sort_order = int(request.form.get('sort_order') or 0)
    is_active = request.form.get('is_active') == 'on'
    min_access_level = int(request.form.get('min_access_level') or 0)

    if not name or not url:
        flash('Name and URL are required.', 'danger')
        return redirect(request.referrer or url_for('rss_admin.index'))

    if feed is None:
        # Adding — check for URL collision
        if RssFeed.query.filter_by(url=url).first():
            flash('A feed with that URL already exists.', 'danger')
            return redirect(url_for('rss_admin.index'))
        feed = RssFeed(url=url)
        db.session.add(feed)

    feed.name = name
    feed.url = url
    feed.site_url = site_url or None
    feed.description = description or None
    feed.category = category or 'general'
    feed.sort_order = sort_order
    feed.is_active = is_active
    feed.min_access_level = min_access_level
    db.session.commit()
    flash(f'Feed "{name}" saved.', 'success')

    # Trigger an immediate fetch so the user sees items right away.
    try:
        from ..rss.poller import fetch_one_now
        fetch_one_now(feed.id)
    except Exception:
        pass

    return redirect(url_for('rss_admin.index'))


@rss_admin_bp.route('/<int:feed_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete(feed_id):
    feed = RssFeed.query.get_or_404(feed_id)
    name = feed.name
    db.session.delete(feed)   # cascades RssItem + RssReadStatus
    db.session.commit()
    flash(f'Feed "{name}" deleted.', 'success')
    return redirect(url_for('rss_admin.index'))


@rss_admin_bp.route('/<int:feed_id>/refresh', methods=['POST'])
@login_required
@_admin_required
def refresh(feed_id):
    """Manually trigger a fetch for one feed."""
    feed = RssFeed.query.get_or_404(feed_id)
    try:
        from ..rss.poller import fetch_one_now
        added = fetch_one_now(feed.id)
        flash(f'Refreshed "{feed.name}" — {added} new item(s).', 'success')
    except Exception as exc:  # pylint: disable=broad-except
        flash(f'Refresh failed: {exc}', 'danger')
    return redirect(url_for('rss_admin.index'))
