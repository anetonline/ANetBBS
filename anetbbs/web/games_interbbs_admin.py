"""Admin UI for InterBBS Game Score Sharing.

Routes:
    GET   /admin/games-interbbs/            — status + settings
    POST  /admin/games-interbbs/settings    — InterBBS sharing on/off + network
"""
from __future__ import annotations

import os

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from ..models import db, GameScore, EchomailNetwork, ScheduledEvent
from .admin import _write_env_keys

games_interbbs_admin_bp = Blueprint(
    'games_interbbs_admin', __name__, url_prefix='/admin/games-interbbs')

PER_PAGE = 30


def _admin_required():
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        abort(403)


def _env_path():
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', '.env'))


@games_interbbs_admin_bp.route('/')
@login_required
def index():
    _admin_required()
    page = request.args.get('page', 1, type=int)
    pagination = (GameScore.query.filter(GameScore.origin_bbs.isnot(None))
                  .order_by(GameScore.achieved_at.desc())
                  .paginate(page=page, per_page=PER_PAGE, error_out=False))
    return render_template('admin/games_interbbs.html',
                           pagination=pagination,
                           interbbs_enabled=current_app.config.get('GAMES_INTERBBS_ENABLED', False),
                           interbbs_network_id=current_app.config.get('GAMES_INTERBBS_NETWORK_ID'),
                           # BinkP only -- see wall_admin.py's identical
                           # filter for why (QWK areas are numeric
                           # conferences, not symbolic tags).
                           networks=EchomailNetwork.query.filter_by(
                               is_active=True, network_type='binkp'
                           ).order_by(EchomailNetwork.name).all())


@games_interbbs_admin_bp.route('/settings', methods=['POST'])
@login_required
def settings():
    _admin_required()
    interbbs_enabled = request.form.get('interbbs_enabled') == 'on'
    network_id = (request.form.get('interbbs_network_id') or '').strip()
    if interbbs_enabled and not network_id:
        flash('Pick a network before enabling InterBBS score sharing.', 'danger')
        return redirect(url_for('.index'))
    if interbbs_enabled:
        network = EchomailNetwork.query.get(int(network_id))
        if network is None or network.network_type != 'binkp':
            flash('InterBBS score sharing only works over a BinkP network -- QWK '
                  'areas are identified by conference number, not by name, so '
                  'this area could never actually receive QWK traffic.', 'danger')
            return redirect(url_for('.index'))

    current_app.config['GAMES_INTERBBS_ENABLED'] = interbbs_enabled
    current_app.config['GAMES_INTERBBS_NETWORK_ID'] = network_id or None

    env_updates = {
        'GAMES_INTERBBS_ENABLED': 'true' if interbbs_enabled else 'false',
        'GAMES_INTERBBS_NETWORK_ID': network_id,
    }

    if interbbs_enabled:
        # Fairness lock: different installs can configure different
        # CASINO_*_START values, so a "$50,000 peak" isn't earned
        # under identical odds on two different BBSes. Enabling score
        # sharing always resets the four casino starting balances to
        # the shared standard, regardless of what the sysop had them
        # set to before -- see interbbs_sync.py's
        # CASINO_INTERBBS_STANDARD_STARTS and admin.py's settings()
        # route, which auto-disables this feature again if a sysop
        # later changes any of the four away from the standard.
        from ..echomail.interbbs_sync import CASINO_INTERBBS_STANDARD_STARTS
        for key, val in CASINO_INTERBBS_STANDARD_STARTS.items():
            current_app.config[key] = val
        env_updates.update(CASINO_INTERBBS_STANDARD_STARTS)

    try:
        _write_env_keys(_env_path(), env_updates)
    except Exception as e:
        flash(f'Settings updated in memory but could not save to .env: {e}', 'warning')
        return redirect(url_for('.index'))

    if interbbs_enabled:
        _ensure_scores_sync_event()
        # Create the ANET_GAMESCORES area right now, not lazily on the
        # first local score submission after enabling -- see
        # wall_admin.py's identical fix for why (confirmed missing
        # live once already: enabling the feature alone created
        # nothing at all).
        try:
            from ..echomail.interbbs_sync import ensure_special_area, GAMES_AREA_TAG
            ensure_special_area(network, GAMES_AREA_TAG)
        except Exception as e:
            flash(f'Settings saved, but could not create the ANET_GAMESCORES area yet: {e}', 'warning')
            return redirect(url_for('.index'))
        flash('Game score InterBBS sharing enabled. Casino starting balances '
              'have been reset to the shared standard values.', 'success')
    else:
        flash('Game score InterBBS sharing disabled.', 'success')
    return redirect(url_for('.index'))


def _ensure_scores_sync_event():
    """Idempotent get-or-create of the ScheduledEvent that drives inbound
    InterBBS score sync — same reasoning as wall_admin.py's
    _ensure_wall_sync_event()."""
    existing = ScheduledEvent.query.filter_by(handler_key='sync_scores_inbound').first()
    if existing:
        return
    db.session.add(ScheduledEvent(
        name='InterBBS Game Scores: import inbound scores',
        handler_key='sync_scores_inbound',
        params_json='{}',
        schedule_json='{"kind": "interval", "minutes": 15}',
    ))
    db.session.commit()
