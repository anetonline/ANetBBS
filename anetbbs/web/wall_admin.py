"""Admin UI for the Graffiti Wall.

Routes:
    GET   /admin/wall/                  — list posts (paginated)
    POST  /admin/wall/<id>/delete       — soft-delete a post
    POST  /admin/wall/clear             — soft-delete ALL posts
"""
from __future__ import annotations

import os

from flask import (Blueprint, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import login_required

from ..models import db, WallPost, EchomailNetwork
from ..features.wall import WALL_SCHEMES, WALL_SCHEME_LABELS
from .admin import _write_env_keys

wall_admin_bp = Blueprint('wall_admin', __name__, url_prefix='/admin/wall')

PER_PAGE = 30


from .access_control import require_admin_or_403 as _admin_required


def _env_path():
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', '.env'))


@wall_admin_bp.route('/')
@login_required
def index():
    _admin_required()
    page = request.args.get('page', 1, type=int)
    show_deleted = request.args.get('deleted', '0') == '1'
    q = WallPost.query
    if not show_deleted:
        q = q.filter_by(is_deleted=False)
    pagination = q.order_by(WallPost.created_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False)
    current_scheme = current_app.config.get('WALL_COLOR_SCHEME', 'cyan')
    return render_template('admin/wall.html',
                           pagination=pagination,
                           show_deleted=show_deleted,
                           current_scheme=current_scheme,
                           schemes=WALL_SCHEME_LABELS,
                           interbbs_enabled=current_app.config.get('WALL_INTERBBS_ENABLED', False),
                           interbbs_network_id=current_app.config.get('WALL_INTERBBS_NETWORK_ID'),
                           # BinkP only -- QWK areas are identified by
                           # numeric conference number end to end (see
                           # qwk.py/qwk_hub_ftp.py), so a symbolic tag
                           # like ANET_WALL can never actually receive
                           # real QWK traffic no matter how the EchoArea
                           # row is created. Offering a QWK network here
                           # would silently never work.
                           networks=EchomailNetwork.query.filter_by(
                               is_active=True, network_type='binkp'
                           ).order_by(EchomailNetwork.name).all())


@wall_admin_bp.route('/settings', methods=['POST'])
@login_required
def settings():
    _admin_required()
    scheme = request.form.get('color_scheme', 'cyan')
    if scheme not in WALL_SCHEMES:
        scheme = 'cyan'

    interbbs_enabled = request.form.get('interbbs_enabled') == 'on'
    network_id = (request.form.get('interbbs_network_id') or '').strip()
    if interbbs_enabled and not network_id:
        flash('Pick a network before enabling InterBBS Wall sharing.', 'danger')
        return redirect(url_for('.index'))
    if interbbs_enabled:
        network = EchomailNetwork.query.get(int(network_id))
        if network is None or network.network_type != 'binkp':
            flash('InterBBS Wall only works over a BinkP network -- QWK areas '
                  'are identified by conference number, not by name, so this '
                  'area could never actually receive QWK traffic.', 'danger')
            return redirect(url_for('.index'))

    current_app.config['WALL_COLOR_SCHEME'] = scheme
    current_app.config['WALL_INTERBBS_ENABLED'] = interbbs_enabled
    current_app.config['WALL_INTERBBS_NETWORK_ID'] = network_id or None

    try:
        _write_env_keys(_env_path(), {
            'WALL_COLOR_SCHEME': scheme,
            'WALL_INTERBBS_ENABLED': 'true' if interbbs_enabled else 'false',
            'WALL_INTERBBS_NETWORK_ID': network_id,
        })
    except Exception as e:
        flash(f'Settings updated in memory but could not save to .env: {e}', 'warning')
        return redirect(url_for('.index'))

    if interbbs_enabled:
        _ensure_wall_sync_event()
        # Create the ANET_WALL area right now, not lazily on the first
        # local post -- a sysop enabling this should see the area
        # immediately (to verify it, check AreaFix went out, etc.), and
        # inbound sync needs somewhere to write into even before anyone
        # here has posted anything locally. Confirmed missing live:
        # enabling the feature alone created nothing at all.
        try:
            from ..echomail.interbbs_sync import ensure_special_area, WALL_AREA_TAG
            ensure_special_area(network, WALL_AREA_TAG)
        except Exception as e:
            flash(f'Settings saved, but could not create the ANET_WALL area yet: {e}', 'warning')
            return redirect(url_for('.index'))

    flash(f'Wall settings saved (color: "{WALL_SCHEME_LABELS.get(scheme, scheme)}", '
          f'InterBBS: {"on" if interbbs_enabled else "off"}).', 'success')
    return redirect(url_for('.index'))


def _ensure_wall_sync_event():
    """Idempotent get-or-create of the ScheduledEvent that drives inbound
    InterBBS Wall sync — a sysop enabling the feature shouldn't need a
    separate manual trip to Admin -> Scheduled Events to make it actually
    work."""
    from ..models import ScheduledEvent
    existing = ScheduledEvent.query.filter_by(handler_key='sync_wall_inbound').first()
    if existing:
        return
    db.session.add(ScheduledEvent(
        name='InterBBS Wall: import inbound posts',
        handler_key='sync_wall_inbound',
        params_json='{}',
        schedule_json='{"kind": "interval", "minutes": 15}',
    ))
    db.session.commit()


@wall_admin_bp.route('/<int:post_id>/delete', methods=['POST'])
@login_required
def delete(post_id):
    _admin_required()
    post = WallPost.query.get_or_404(post_id)
    post.is_deleted = True
    db.session.commit()
    flash(f'Wall post #{post_id} deleted.', 'success')
    return redirect(url_for('.index'))


@wall_admin_bp.route('/restore/<int:post_id>', methods=['POST'])
@login_required
def restore(post_id):
    _admin_required()
    post = WallPost.query.get_or_404(post_id)
    post.is_deleted = False
    db.session.commit()
    flash(f'Wall post #{post_id} restored.', 'success')
    return redirect(url_for('.index', deleted=1))


@wall_admin_bp.route('/clear', methods=['POST'])
@login_required
def clear_all():
    _admin_required()
    count = WallPost.query.filter_by(is_deleted=False).update({'is_deleted': True})
    db.session.commit()
    flash(f'Cleared {count} wall posts.', 'warning')
    return redirect(url_for('.index'))
