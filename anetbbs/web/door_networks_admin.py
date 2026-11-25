# anetbbs/web/door_networks_admin.py
"""Admin UI for door-network federation (Phase 4 of the door-games
roadmap, 2026-10-20): shared multi-BBS door catalogs a sysop can join
instead of hosting every door locally.

Only DoorParty is wired up with a real connection mechanism here --
confirmed directly from its own setup docs (see
anetbbs/features/doorparty_import.py's module docstring). BBSLink and
CombatNet are real, confirmed-to-exist networks (ENiGMA 1/2 ships
modules for both) but their wire-level connection mechanism was NOT
confirmed this session -- ENiGMA's own docs page lists the credential
KEYS each needs (BBSLink: sysCode/authCode/schemeCode; CombatNet:
bbsTag/password) but not how a session is actually brokered, and that
detail is gated behind each service's own sysop registration. Building
a credentials-only admin page for two services with nothing real to
connect them to yet would be dead weight -- deliberately NOT built
until that research gate clears (either real ENiGMA module source, or
Jerry's own registration + whatever connection instructions the
service provides).

Routes:
    GET   /admin/door-networks/              — DoorParty settings + game list
    POST  /admin/door-networks/doorparty/settings
    POST  /admin/door-networks/doorparty/add-game
    POST  /admin/door-networks/doorparty/game/<id>/delete
"""

from flask import (Blueprint, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import login_required

from ..models import db, Game
from ..features.doorparty_import import build_doorparty_game_kwargs
from .access_control import require_admin_or_403 as _admin_required
from .admin import _write_env_keys

door_networks_admin_bp = Blueprint(
    'door_networks_admin', __name__, url_prefix='/admin/door-networks')


def _env_path():
    from .env_file import env_path
    return env_path()


@door_networks_admin_bp.route('/')
@login_required
def index():
    _admin_required()
    games = (Game.query.filter_by(game_type='door_rlogin')
            .filter(Game.slug.like('doorparty-%'))
            .order_by(Game.sort_order, Game.name).all())
    return render_template(
        'admin/door_networks.html',
        games=games,
        local_port=current_app.config.get('DOORPARTY_LOCAL_PORT', ''),
        bbs_tag=current_app.config.get('DOORPARTY_BBS_TAG', ''),
        shared_secret=current_app.config.get('DOORPARTY_SHARED_SECRET', ''),
        configured=bool(current_app.config.get('DOORPARTY_LOCAL_PORT')
                       and current_app.config.get('DOORPARTY_BBS_TAG')
                       and current_app.config.get('DOORPARTY_SHARED_SECRET')))


@door_networks_admin_bp.route('/doorparty/settings', methods=['POST'])
@login_required
def doorparty_settings():
    _admin_required()
    local_port = (request.form.get('local_port') or '').strip()
    bbs_tag = (request.form.get('bbs_tag') or '').strip()
    shared_secret = (request.form.get('shared_secret') or '').strip()

    if local_port:
        try:
            port_num = int(local_port)
            if not (1 <= port_num <= 65535):
                raise ValueError
        except ValueError:
            flash('Local port must be a number between 1 and 65535.', 'danger')
            return redirect(url_for('.index'))

    current_app.config['DOORPARTY_LOCAL_PORT'] = local_port
    current_app.config['DOORPARTY_BBS_TAG'] = bbs_tag
    current_app.config['DOORPARTY_SHARED_SECRET'] = shared_secret

    try:
        _write_env_keys(_env_path(), {
            'DOORPARTY_LOCAL_PORT': local_port,
            'DOORPARTY_BBS_TAG': bbs_tag,
            'DOORPARTY_SHARED_SECRET': shared_secret,
        })
    except Exception as e:
        flash(f'Settings updated in memory but could not save to .env: {e}', 'warning')
        return redirect(url_for('.index'))

    flash('DoorParty connection settings saved.', 'success')
    return redirect(url_for('.index'))


@door_networks_admin_bp.route('/doorparty/add-game', methods=['POST'])
@login_required
def doorparty_add_game():
    _admin_required()
    name = (request.form.get('name') or '').strip()
    door_code = (request.form.get('door_code') or '').strip()
    if not name:
        flash('Name is required.', 'danger')
        return redirect(url_for('.index'))

    local_port = current_app.config.get('DOORPARTY_LOCAL_PORT', '')
    bbs_tag = current_app.config.get('DOORPARTY_BBS_TAG', '')
    shared_secret = current_app.config.get('DOORPARTY_SHARED_SECRET', '')
    if not (local_port and bbs_tag and shared_secret):
        flash('Save your DoorParty connection settings first (local port, '
             'BBS tag, shared secret).', 'danger')
        return redirect(url_for('.index'))

    kwargs = build_doorparty_game_kwargs(
        name, door_code=door_code, local_port=local_port,
        bbs_tag=bbs_tag, shared_secret=shared_secret)
    if kwargs['slug'] is None:
        flash('Could not derive a slug from that name -- try something '
             'with at least one letter or number.', 'danger')
        return redirect(url_for('.index'))
    if Game.query.filter_by(slug=kwargs['slug']).first() is not None:
        flash(f'A game with slug "{kwargs["slug"]}" already exists.', 'danger')
        return redirect(url_for('.index'))

    db.session.add(Game(**kwargs))
    db.session.commit()
    flash(f'Added "{name}" via DoorParty.', 'success')
    return redirect(url_for('.index'))


@door_networks_admin_bp.route('/doorparty/game/<int:game_id>/delete', methods=['POST'])
@login_required
def doorparty_delete_game(game_id):
    _admin_required()
    game = Game.query.filter_by(id=game_id).filter(
        Game.slug.like('doorparty-%')).first_or_404()
    name = game.name
    db.session.delete(game)
    db.session.commit()
    flash(f'"{name}" removed.', 'success')
    return redirect(url_for('.index'))
