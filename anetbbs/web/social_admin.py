# anetbbs/web/social_admin.py
"""
Sysop review queue for the auto-social-posting system
(features/social_queue.py queues rows here; this blueprint is the only
place anything actually gets posted). Nothing in features/social_queue.py
ever calls the platform clients directly -- posting only ever happens
from the approve() view below, in response to a real sysop click.

Routes:
    GET  /admin/social/            -- queue (pending first, then recent history)
    GET  /admin/social/<id>/image  -- serve the rendered PNG
    POST /admin/social/<id>/save   -- edit the caption text
    POST /admin/social/<id>/approve -- post to every configured+enabled platform
    POST /admin/social/<id>/skip   -- mark skipped, never posted
"""
import json
import logging
import os

from flask import (Blueprint, abort, current_app, flash, jsonify,
                   redirect, render_template, request, send_file, url_for)
from flask_login import login_required

from ..models import SocialPost, db
from .access_control import require_admin_or_403

logger = logging.getLogger(__name__)
social_admin_bp = Blueprint('social_admin', __name__, url_prefix='/admin/social')


@social_admin_bp.route('/')
@login_required
def index():
    require_admin_or_403()
    pending = (SocialPost.query.filter_by(status='pending')
              .order_by(SocialPost.created_at.desc()).all())
    history = (SocialPost.query.filter(SocialPost.status != 'pending')
              .order_by(SocialPost.created_at.desc()).limit(30).all())
    configured = {
        'bluesky': bool(current_app.config.get('BLUESKY_HANDLE') and
                        current_app.config.get('BLUESKY_APP_PASSWORD')),
        'mastodon': bool(current_app.config.get('MASTODON_INSTANCE_URL') and
                         current_app.config.get('MASTODON_ACCESS_TOKEN')),
    }
    return render_template('social_admin/index.html', pending=pending,
                           history=history, configured=configured)


@social_admin_bp.route('/<int:post_id>/image')
@login_required
def image(post_id):
    require_admin_or_403()
    post = SocialPost.query.get_or_404(post_id)
    if not post.image_path or not os.path.isfile(post.image_path):
        abort(404)
    return send_file(post.image_path, mimetype='image/png')


@social_admin_bp.route('/<int:post_id>/save', methods=['POST'])
@login_required
def save(post_id):
    require_admin_or_403()
    post = SocialPost.query.get_or_404(post_id)
    if post.status != 'pending':
        abort(400)
    text = (request.form.get('text') or '').strip()
    if not text:
        flash('Post text cannot be empty.', 'danger')
        return redirect(url_for('social_admin.index'))
    post.text = text
    db.session.commit()
    flash('Saved.', 'success')
    return redirect(url_for('social_admin.index'))


@social_admin_bp.route('/<int:post_id>/skip', methods=['POST'])
@login_required
def skip(post_id):
    require_admin_or_403()
    post = SocialPost.query.get_or_404(post_id)
    if post.status == 'pending':
        post.status = 'skipped'
        db.session.commit()
    return redirect(url_for('social_admin.index'))


@social_admin_bp.route('/<int:post_id>/approve', methods=['POST'])
@login_required
def approve(post_id):
    """Post to every platform with credentials configured. Runs
    synchronously -- the sysop clicking this button is deliberately
    the trigger for the real, external, irreversible action, so they
    see the result (success + link, or a clear error) right away."""
    require_admin_or_403()
    post = SocialPost.query.get_or_404(post_id)
    if post.status != 'pending':
        abort(400)

    image_bytes = None
    if post.image_path and os.path.isfile(post.image_path):
        with open(post.image_path, 'rb') as f:
            image_bytes = f.read()

    results = {}
    any_attempted = False
    any_success = False

    cfg = current_app.config
    if cfg.get('BLUESKY_HANDLE') and cfg.get('BLUESKY_APP_PASSWORD'):
        any_attempted = True
        from ..features import social_bluesky
        ok, detail = social_bluesky.post(
            cfg['BLUESKY_HANDLE'], cfg['BLUESKY_APP_PASSWORD'],
            post.text, image_bytes, post.trigger_label or '')
        results['bluesky'] = detail if ok else f'error: {detail}'
        any_success = any_success or ok

    if cfg.get('MASTODON_INSTANCE_URL') and cfg.get('MASTODON_ACCESS_TOKEN'):
        any_attempted = True
        from ..features import social_mastodon
        ok, detail = social_mastodon.post(
            cfg['MASTODON_INSTANCE_URL'], cfg['MASTODON_ACCESS_TOKEN'],
            post.text, image_bytes, post.trigger_label or '')
        results['mastodon'] = detail if ok else f'error: {detail}'
        any_success = any_success or ok

    if not any_attempted:
        flash('No platform is configured — set Bluesky and/or Mastodon '
             'credentials first.', 'danger')
        return redirect(url_for('social_admin.index'))

    import datetime as _dt
    post.result_json = json.dumps(results)
    post.status = 'posted' if any_success else 'failed'
    post.posted_at = _dt.datetime.utcnow()
    db.session.commit()

    if any_success:
        flash('Posted: ' + ', '.join(f'{k}: {v}' for k, v in results.items()), 'success')
    else:
        flash('All platforms failed: ' + ', '.join(f'{k}: {v}' for k, v in results.items()),
             'danger')
    return redirect(url_for('social_admin.index'))
