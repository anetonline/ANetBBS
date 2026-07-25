"""Admin UI for Last Callers.

Routes:
    GET   /admin/lastcallers/            — list recent callers (paginated)
    POST  /admin/lastcallers/settings    — InterBBS sharing on/off + network
"""
from __future__ import annotations

import os

from flask import (Blueprint, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import login_required

from ..models import db, CallerLog, EchomailNetwork, ScheduledEvent
from .admin import _write_env_keys

lastcallers_admin_bp = Blueprint('lastcallers_admin', __name__, url_prefix='/admin/lastcallers')

PER_PAGE = 30
DISPLAY_COUNT_CHOICES = (5, 10, 15, 20, 25, 30, 50, 100)


from .access_control import require_admin_or_403 as _admin_required


def _env_path():
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', '.env'))


@lastcallers_admin_bp.route('/')
@login_required
def index():
    _admin_required()
    page = request.args.get('page', 1, type=int)
    pagination = CallerLog.query.order_by(CallerLog.started_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False)
    return render_template('admin/lastcallers.html',
                           pagination=pagination,
                           interbbs_enabled=current_app.config.get('LASTCALLERS_INTERBBS_ENABLED', False),
                           interbbs_network_id=current_app.config.get('LASTCALLERS_INTERBBS_NETWORK_ID'),
                           hide_sysop=current_app.config.get('LASTCALLERS_HIDE_SYSOP', False),
                           display_count=current_app.config.get('LASTCALLERS_DISPLAY_COUNT', 20),
                           # BinkP only -- see wall_admin.py's identical
                           # filter for why (QWK areas are numeric
                           # conferences, not symbolic tags).
                           networks=EchomailNetwork.query.filter_by(
                               is_active=True, network_type='binkp'
                           ).order_by(EchomailNetwork.name).all())


@lastcallers_admin_bp.route('/settings', methods=['POST'])
@login_required
def settings():
    _admin_required()
    interbbs_enabled = request.form.get('interbbs_enabled') == 'on'
    network_id = (request.form.get('interbbs_network_id') or '').strip()
    hide_sysop = request.form.get('hide_sysop') == 'on'
    display_count = request.form.get('display_count', type=int) or 20
    if display_count not in DISPLAY_COUNT_CHOICES:
        display_count = min(DISPLAY_COUNT_CHOICES, key=lambda c: abs(c - display_count))
    if interbbs_enabled and not network_id:
        flash('Pick a network before enabling InterBBS Last Callers sharing.', 'danger')
        return redirect(url_for('.index'))
    if interbbs_enabled:
        network = EchomailNetwork.query.get(int(network_id))
        if network is None or network.network_type != 'binkp':
            flash('InterBBS Last Callers only works over a BinkP network -- QWK '
                  'areas are identified by conference number, not by name, so '
                  'this area could never actually receive QWK traffic.', 'danger')
            return redirect(url_for('.index'))

    current_app.config['LASTCALLERS_INTERBBS_ENABLED'] = interbbs_enabled
    current_app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = network_id or None
    current_app.config['LASTCALLERS_HIDE_SYSOP'] = hide_sysop
    current_app.config['LASTCALLERS_DISPLAY_COUNT'] = display_count

    try:
        _write_env_keys(_env_path(), {
            'LASTCALLERS_INTERBBS_ENABLED': 'true' if interbbs_enabled else 'false',
            'LASTCALLERS_INTERBBS_NETWORK_ID': network_id,
            'LASTCALLERS_HIDE_SYSOP': 'true' if hide_sysop else 'false',
            'LASTCALLERS_DISPLAY_COUNT': str(display_count),
        })
    except Exception as e:
        flash(f'Settings updated in memory but could not save to .env: {e}', 'warning')
        return redirect(url_for('.index'))

    if interbbs_enabled:
        _ensure_lastcallers_sync_event()
        # Create the ANET_LASTCALLERS area right now, not lazily on the
        # first local login after enabling -- see wall_admin.py's
        # identical fix for why (confirmed missing live: enabling the
        # feature alone created nothing at all).
        try:
            from ..echomail.interbbs_sync import ensure_special_area, LASTCALLERS_AREA_TAG
            ensure_special_area(network, LASTCALLERS_AREA_TAG)
        except Exception as e:
            flash(f'Settings saved, but could not create the ANET_LASTCALLERS area yet: {e}', 'warning')
            return redirect(url_for('.index'))

    flash(f'Last Callers InterBBS sharing {"enabled" if interbbs_enabled else "disabled"}. '
          f'Sysop logins are {"hidden from" if hide_sysop else "shown in"} the Last Callers displays.',
          'success')
    return redirect(url_for('.index'))


def _ensure_lastcallers_sync_event():
    """Idempotent get-or-create of the ScheduledEvent that drives inbound
    InterBBS Last Callers sync — same reasoning as wall_admin.py's
    _ensure_wall_sync_event()."""
    existing = ScheduledEvent.query.filter_by(handler_key='sync_lastcallers_inbound').first()
    if existing:
        return
    db.session.add(ScheduledEvent(
        name='InterBBS Last Callers: import inbound entries',
        handler_key='sync_lastcallers_inbound',
        params_json='{}',
        schedule_json='{"kind": "interval", "minutes": 15}',
    ))
    db.session.commit()
