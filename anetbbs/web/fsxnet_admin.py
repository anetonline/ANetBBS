"""Admin UI for fsxNet IBOL/IBLC (real wire-format InterBBS Oneliners +
Last Callers -- see anetbbs/echomail/fsxnet_sync.py).

Routes:
    GET   /admin/fsxnet/                — list oneliners + last callers (paginated), settings form
    POST  /admin/fsxnet/settings        — enable/disable + network/area config
    POST  /admin/fsxnet/oneliner/<id>/delete
    POST  /admin/fsxnet/lastcaller/<id>/delete
"""
from __future__ import annotations

import os

from flask import (Blueprint, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import login_required

from ..models import db, EchomailNetwork, FsxnetOneliner, FsxnetLastCaller
from .access_control import require_admin_or_403 as _admin_required
from .admin import _write_env_keys

fsxnet_admin_bp = Blueprint('fsxnet_admin', __name__, url_prefix='/admin/fsxnet')

PER_PAGE = 30


def _env_path():
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', '.env'))


@fsxnet_admin_bp.route('/')
@login_required
def index():
    _admin_required()
    oneliner_page = request.args.get('oneliner_page', 1, type=int)
    lastcaller_page = request.args.get('lastcaller_page', 1, type=int)
    oneliners = FsxnetOneliner.query.order_by(FsxnetOneliner.created_at.desc()).paginate(
        page=oneliner_page, per_page=PER_PAGE, error_out=False)
    lastcallers = FsxnetLastCaller.query.order_by(FsxnetLastCaller.created_at.desc()).paginate(
        page=lastcaller_page, per_page=PER_PAGE, error_out=False)
    return render_template(
        'admin/fsxnet.html',
        oneliners=oneliners,
        lastcallers=lastcallers,
        ibol_enabled=current_app.config.get('FSXNET_IBOL_ENABLED', False),
        iblc_enabled=current_app.config.get('FSXNET_IBLC_ENABLED', False),
        network_id=current_app.config.get('FSXNET_NETWORK_ID'),
        area_tag=current_app.config.get('FSXNET_AREA_TAG', 'FSX_DAT'),
        system_name=current_app.config.get('FSXNET_SYSTEM_NAME', ''),
        telnet_port=current_app.config.get('FSXNET_TELNET_PORT', ''),
        hide_sysop=current_app.config.get('FSXNET_HIDE_SYSOP', False),
        # BinkP only -- same reasoning as Wall/Last Callers InterBBS:
        # QWK areas are identified by numeric conference number end to
        # end, so a symbolic tag like FSX_DAT could never actually
        # receive real QWK traffic no matter how the EchoArea is made.
        networks=EchomailNetwork.query.filter_by(
            is_active=True, network_type='binkp'
        ).order_by(EchomailNetwork.name).all())


@fsxnet_admin_bp.route('/settings', methods=['POST'])
@login_required
def settings():
    _admin_required()
    ibol_enabled = request.form.get('ibol_enabled') == 'on'
    iblc_enabled = request.form.get('iblc_enabled') == 'on'
    network_id = (request.form.get('network_id') or '').strip()
    area_tag = (request.form.get('area_tag') or '').strip() or 'FSX_DAT'
    system_name = (request.form.get('system_name') or '').strip()
    telnet_port = (request.form.get('telnet_port') or '').strip()
    hide_sysop = request.form.get('hide_sysop') == 'on'

    if (ibol_enabled or iblc_enabled) and not network_id:
        flash('Pick a network before enabling fsxNet IBOL/IBLC sharing.', 'danger')
        return redirect(url_for('.index'))

    network = None
    if network_id:
        network = EchomailNetwork.query.get(int(network_id))
        if network is None or network.network_type != 'binkp':
            flash('fsxNet IBOL/IBLC only works over a BinkP network -- QWK '
                  'areas are identified by conference number, not by name, '
                  'so this area could never actually receive QWK traffic.', 'danger')
            return redirect(url_for('.index'))

    if telnet_port:
        try:
            port_num = int(telnet_port)
            if not (1 <= port_num <= 65535):
                raise ValueError
        except ValueError:
            flash('Telnet port must be a number between 1 and 65535.', 'danger')
            return redirect(url_for('.index'))

    current_app.config['FSXNET_IBOL_ENABLED'] = ibol_enabled
    current_app.config['FSXNET_IBLC_ENABLED'] = iblc_enabled
    current_app.config['FSXNET_NETWORK_ID'] = network_id or None
    current_app.config['FSXNET_AREA_TAG'] = area_tag
    current_app.config['FSXNET_SYSTEM_NAME'] = system_name
    current_app.config['FSXNET_TELNET_PORT'] = telnet_port
    current_app.config['FSXNET_HIDE_SYSOP'] = hide_sysop

    try:
        _write_env_keys(_env_path(), {
            'FSXNET_IBOL_ENABLED': 'true' if ibol_enabled else 'false',
            'FSXNET_IBLC_ENABLED': 'true' if iblc_enabled else 'false',
            'FSXNET_NETWORK_ID': network_id,
            'FSXNET_AREA_TAG': area_tag,
            'FSXNET_SYSTEM_NAME': system_name,
            'FSXNET_TELNET_PORT': telnet_port,
            'FSXNET_HIDE_SYSOP': 'true' if hide_sysop else 'false',
        })
    except Exception as e:
        flash(f'Settings updated in memory but could not save to .env: {e}', 'warning')
        return redirect(url_for('.index'))

    if (ibol_enabled or iblc_enabled) and network is not None:
        # Create the FSX_DAT area right now, not lazily on the first
        # local post -- same reasoning as Wall/Last Callers InterBBS:
        # a sysop enabling this should see the area immediately, and
        # inbound sync needs somewhere to read from even before any
        # local activity.
        try:
            from ..echomail.interbbs_sync import ensure_special_area
            ensure_special_area(network, area_tag)
        except Exception as e:
            flash(f'Settings saved, but could not create the {area_tag} area yet: {e}', 'warning')
            return redirect(url_for('.index'))
        _ensure_fsxnet_sync_event()

    flash(f'fsxNet settings saved (IBOL: {"on" if ibol_enabled else "off"}, '
          f'IBLC: {"on" if iblc_enabled else "off"}).', 'success')
    return redirect(url_for('.index'))


def _ensure_fsxnet_sync_event():
    """Idempotent get-or-create of the ScheduledEvent that drives
    inbound fsxNet IBOL/IBLC sync -- same reasoning as Wall's
    _ensure_wall_sync_event(): a sysop enabling the feature shouldn't
    need a separate manual trip to Admin -> Scheduled Events."""
    from ..models import ScheduledEvent
    existing = ScheduledEvent.query.filter_by(handler_key='sync_fsxnet_inbound').first()
    if existing:
        return
    db.session.add(ScheduledEvent(
        name='fsxNet IBOL/IBLC: import inbound data',
        handler_key='sync_fsxnet_inbound',
        params_json='{}',
        schedule_json='{"kind": "interval", "minutes": 15}',
    ))
    db.session.commit()


@fsxnet_admin_bp.route('/oneliner/<int:oneliner_id>/delete', methods=['POST'])
@login_required
def delete_oneliner(oneliner_id):
    _admin_required()
    row = FsxnetOneliner.query.get_or_404(oneliner_id)
    db.session.delete(row)
    db.session.commit()
    flash(f'Oneliner #{oneliner_id} deleted.', 'success')
    return redirect(url_for('.index'))


@fsxnet_admin_bp.route('/lastcaller/<int:lastcaller_id>/delete', methods=['POST'])
@login_required
def delete_lastcaller(lastcaller_id):
    _admin_required()
    row = FsxnetLastCaller.query.get_or_404(lastcaller_id)
    db.session.delete(row)
    db.session.commit()
    flash(f'Last-caller entry #{lastcaller_id} deleted.', 'success')
    return redirect(url_for('.index'))
