"""Admin UI for logon/logoff modules.

Routes:
    GET/POST  /admin/login-modules/              — list
    GET/POST  /admin/login-modules/new           — create
    GET/POST  /admin/login-modules/<id>/edit     — edit
    POST      /admin/login-modules/<id>/delete   — delete
    POST      /admin/login-modules/<id>/toggle   — enable/disable
"""
from __future__ import annotations

import json
import os

from flask import (Blueprint, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from ..models import db, LoginModule

login_modules_admin_bp = Blueprint(
    'login_modules_admin', __name__, url_prefix='/admin/login-modules')


from .access_control import require_admin_or_403 as _admin_required


MODULE_TYPES = [
    ('wall',        'Graffiti Wall'),
    ('lastcallers', 'Last Callers'),
    ('ansi',        'ANSI Screen (slot)'),
    ('shell',       'Shell Command'),
    ('door_native', 'Native Linux Door'),
    ('door_python', 'Python Door Module'),
]
EVENT_TYPES = [('logon', 'Logon'), ('logoff', 'Logoff')]


def _params_help(module_type: str) -> str:
    helps = {
        'wall':        'No params needed — leave as {}',
        'lastcallers': 'No params needed — leave as {}',
        'ansi':        '{"slot": "welcome"}  — any BbsAnsiScreen slot name',
        'shell':       '{"command": "/path/to/script.sh"}',
        'door_native': '{"path": "/path/to/door", "args": "--node $NODE"}',
        'door_python': '{"module": "anetbbs.doors.mything", "func": "run"}',
    }
    return helps.get(module_type, '{}')


def _env_path():
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', '.env'))


def _write_env_key(key: str, value: str) -> None:
    env_path = _env_path()
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                if line.startswith(f'{key}='):
                    lines.append(f'{key}={value}\n')
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f'{key}={value}\n')
    with open(env_path, 'w') as f:
        f.writelines(lines)


@login_modules_admin_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    _admin_required()
    logon_mods  = (LoginModule.query.filter_by(event_type='logon')
                   .order_by(LoginModule.sort_order, LoginModule.id).all())
    logoff_mods = (LoginModule.query.filter_by(event_type='logoff')
                   .order_by(LoginModule.sort_order, LoginModule.id).all())
    fast_logon_enabled = current_app.config.get('FAST_LOGON_ENABLED', False)
    if isinstance(fast_logon_enabled, str):
        fast_logon_enabled = fast_logon_enabled.lower() in ('true', '1', 'yes')
    return render_template('admin/login_modules.html',
                           logon_mods=logon_mods,
                           logoff_mods=logoff_mods,
                           module_types=MODULE_TYPES,
                           fast_logon_enabled=fast_logon_enabled)


@login_modules_admin_bp.route('/fast-logon-toggle', methods=['POST'])
@login_required
def fast_logon_toggle():
    _admin_required()
    current = current_app.config.get('FAST_LOGON_ENABLED', False)
    if isinstance(current, str):
        current = current.lower() in ('true', '1', 'yes')
    new_val = not current
    current_app.config['FAST_LOGON_ENABLED'] = new_val
    try:
        _write_env_key('FAST_LOGON_ENABLED', 'true' if new_val else 'false')
    except Exception as e:
        flash(f'Fast logon updated in memory but .env write failed: {e}', 'warning')
        return redirect(url_for('.index'))
    state = 'enabled' if new_val else 'disabled'
    flash(f'Fast logon {state}.', 'success')
    return redirect(url_for('.index'))


@login_modules_admin_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    _admin_required()
    if request.method == 'POST':
        params_raw = request.form.get('params_json', '{}').strip() or '{}'
        try:
            json.loads(params_raw)
        except json.JSONDecodeError:
            flash('params_json is not valid JSON.', 'danger')
            return redirect(url_for('.new'))
        mod = LoginModule(
            name=request.form['name'].strip(),
            description=request.form.get('description', '').strip(),
            event_type=request.form['event_type'],
            module_type=request.form['module_type'],
            params_json=params_raw,
            is_active=('is_active' in request.form),
            min_access_level=int(request.form.get('min_access_level') or 0),
            sort_order=int(request.form.get('sort_order') or 0),
            skip_on_fast_logon=('skip_on_fast_logon' in request.form),
        )
        db.session.add(mod)
        db.session.commit()
        flash(f'Module "{mod.name}" created.', 'success')
        return redirect(url_for('.index'))
    return render_template('admin/login_module_form.html',
                           mod=None,
                           module_types=MODULE_TYPES,
                           event_types=EVENT_TYPES,
                           params_helps={t: _params_help(t) for t, _ in MODULE_TYPES})


@login_modules_admin_bp.route('/<int:mod_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(mod_id):
    _admin_required()
    mod = LoginModule.query.get_or_404(mod_id)
    if request.method == 'POST':
        params_raw = request.form.get('params_json', '{}').strip() or '{}'
        try:
            json.loads(params_raw)
        except json.JSONDecodeError:
            flash('params_json is not valid JSON.', 'danger')
            return redirect(url_for('.edit', mod_id=mod_id))
        mod.name = request.form['name'].strip()
        mod.description = request.form.get('description', '').strip()
        mod.event_type = request.form['event_type']
        mod.module_type = request.form['module_type']
        mod.params_json = params_raw
        mod.is_active = ('is_active' in request.form)
        mod.min_access_level = int(request.form.get('min_access_level') or 0)
        mod.sort_order = int(request.form.get('sort_order') or 0)
        mod.skip_on_fast_logon = ('skip_on_fast_logon' in request.form)
        db.session.commit()
        flash(f'Module "{mod.name}" updated.', 'success')
        return redirect(url_for('.index'))
    return render_template('admin/login_module_form.html',
                           mod=mod,
                           module_types=MODULE_TYPES,
                           event_types=EVENT_TYPES,
                           params_helps={t: _params_help(t) for t, _ in MODULE_TYPES})


@login_modules_admin_bp.route('/<int:mod_id>/delete', methods=['POST'])
@login_required
def delete(mod_id):
    _admin_required()
    mod = LoginModule.query.get_or_404(mod_id)
    name = mod.name
    db.session.delete(mod)
    db.session.commit()
    flash(f'Module "{name}" deleted.', 'success')
    return redirect(url_for('.index'))


@login_modules_admin_bp.route('/<int:mod_id>/toggle', methods=['POST'])
@login_required
def toggle(mod_id):
    _admin_required()
    mod = LoginModule.query.get_or_404(mod_id)
    mod.is_active = not mod.is_active
    db.session.commit()
    state = 'enabled' if mod.is_active else 'disabled'
    flash(f'Module "{mod.name}" {state}.', 'success')
    return redirect(url_for('.index'))
