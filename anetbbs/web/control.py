# anetbbs/web/control.py
"""
Sysop control panel — live who's-online (web + terminal), service
management (status / start / stop / restart), and node spy.

Service control uses systemctl. To make this work without prompting
for sudo, the deploy script adds a sudoers entry granting the
service user permission to run *only* the anetbbs-* unit operations.
"""
import os
import subprocess
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   abort, jsonify, request)
from flask_login import login_required, current_user

from ..models import User, UserSession


control_bp = Blueprint('control', __name__, url_prefix='/admin/control')


# Units the panel knows how to manage. Renaming a service requires
# updating BOTH this list and the sudoers rule (if you use one).
KNOWN_UNITS = [
    ('anetbbs-web', 'Web (Flask + gunicorn)'),
    ('anetbbs-telnet', 'Telnet server (port 2233)'),
    ('anetbbs-ssh', 'SSH server (port 2234)'),
    ('anetbbs-rlogin', 'rlogin server'),
    ('anetbbs-mrc-bridge', 'MRC bridge'),
]


def _require_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


def _systemctl(*args):
    """Run systemctl with the given args, return (ok, stdout, stderr)."""
    cmd = ['sudo', '-n', 'systemctl'] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, '', 'timeout'
    except FileNotFoundError:
        return False, '', 'systemctl not found'
    except Exception as exc:
        return False, '', str(exc)


def _service_state(unit):
    """Return (active_str, sub_str, since_str). 'inactive' / 'failed' on missing."""
    ok, out, _ = _systemctl('show', unit, '--property=ActiveState,SubState,ActiveEnterTimestamp')
    if not ok:
        return ('unknown', '', '')
    state = {'ActiveState': '', 'SubState': '', 'ActiveEnterTimestamp': ''}
    for line in out.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            if k in state:
                state[k] = v
    return (state['ActiveState'] or 'unknown',
            state['SubState'] or '',
            state['ActiveEnterTimestamp'] or '')


@control_bp.route('/')
@login_required
def index():
    _require_admin()

    # Service rows — live state via systemctl.
    services = []
    for unit, label in KNOWN_UNITS:
        active, sub, since = _service_state(unit)
        services.append({
            'unit': unit, 'label': label,
            'active': active, 'sub': sub, 'since': since,
            'is_running': active == 'active',
        })

    # Web users — anyone with a UserSession row in the last 5 min.
    five_min_ago = datetime.utcnow() - timedelta(minutes=5)
    web_sessions = (UserSession.query
                    .filter(UserSession.last_seen >= five_min_ago)
                    .order_by(UserSession.last_seen.desc()).all())

    # Terminal sessions — pulled from the multinode registry of the BBS
    # daemon process. Since web + BBS are separate processes we can't
    # see the live registry directly; fall back to a presence-table
    # query (presence rows are written by every terminal session).
    try:
        from ..models import UserSession as _US
        # Filter UserSession entries whose page starts with a known
        # terminal marker — SessionPresence sets these.
        terminal_sessions = (_US.query
                             .filter(_US.last_seen >= five_min_ago)
                             .filter(_US.page.in_(
                                 ['main', 'menu', 'door', 'chat', 'msg',
                                  'echo', 'files', 'profile']))
                             .order_by(_US.last_seen.desc()).all())
    except Exception:
        terminal_sessions = []

    return render_template('admin/control.html',
                           services=services,
                           web_sessions=web_sessions,
                           terminal_sessions=terminal_sessions,
                           known_units=KNOWN_UNITS)


@control_bp.route('/service/<unit>/<action>', methods=['POST'])
@login_required
def service_action(unit, action):
    _require_admin()
    if unit not in {u for u, _ in KNOWN_UNITS}:
        flash(f'Unknown unit: {unit}', 'danger')
        return redirect(url_for('control.index'))
    if action not in ('start', 'stop', 'restart', 'reload'):
        flash(f'Unknown action: {action}', 'danger')
        return redirect(url_for('control.index'))
    ok, out, err = _systemctl(action, unit)
    if ok:
        flash(f'systemctl {action} {unit}: OK', 'success')
    else:
        flash(f'systemctl {action} {unit} failed: {err or out}', 'danger')
    return redirect(url_for('control.index'))


@control_bp.route('/service/<unit>/journal')
@login_required
def service_journal(unit):
    _require_admin()
    if unit not in {u for u, _ in KNOWN_UNITS}:
        abort(404)
    # Run journalctl WITHOUT sudo — system journal is readable by any
    # user in the `systemd-journal` group (or `adm` on Debian/Ubuntu).
    # We avoid sudo because the unit's CapabilityBoundingSet (set so
    # we can bind privileged MSP/SYSTAT ports as a non-root user)
    # strips CAP_SETUID/SETGID/AUDIT_WRITE, which sudo needs to elevate.
    try:
        r = subprocess.run(
            ['journalctl', '-u', unit, '--no-pager', '-n', '200'],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            log = (r.stderr or r.stdout
                   or '(journalctl returned non-zero with no output)')
            if 'No journal files were opened' in log or 'Permission' in log:
                log += (
                    '\n\nHint: the service user needs read access to the '
                    'system journal. On Debian/Ubuntu run:\n'
                    f'    sudo usermod -aG systemd-journal,adm {os.getenv("USER", "stingray")}\n'
                    'then restart anetbbs-web.')
        else:
            log = r.stdout or '(empty)'
    except Exception as exc:
        log = f'Could not read journal: {exc}'
    return render_template('admin/journal.html', unit=unit, log=log)


@control_bp.route('/nodespy.json')
@login_required
def nodespy_json():
    """JSON snapshot of all live NodeActivity rows for the auto-refreshing
    sysop NodeSpy panel."""
    _require_admin()
    from ..models import NodeActivity
    rows = (NodeActivity.query.order_by(NodeActivity.slot).all())
    out = []
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    for r in rows:
        # Skip stale rows — terminal session likely died without cleanup.
        if r.last_seen and r.last_seen < cutoff:
            continue
        # Timestamps are stored as naive UTC. Append 'Z' so JavaScript's
        # Date.parse() treats them as UTC instead of local-time, otherwise
        # the idle calculation skews by the local UTC offset.
        out.append({
            'slot': r.slot,
            'username': r.username,
            'protocol': r.protocol,
            'peer': r.peer or '',
            'page': r.page or '',
            'action': r.action or '',
            'started': (r.started_at.isoformat() + 'Z') if r.started_at else '',
            'last_seen': (r.last_seen.isoformat() + 'Z') if r.last_seen else '',
        })
    return jsonify(out)


@control_bp.route('/nodespy/<int:slot>')
@login_required
def nodespy_view(slot):
    """Detailed view of one node — last screen snapshot if available.

    Filters out rows whose last_seen is older than 5 minutes; those are
    sessions that died without cleanup and shouldn't render as live."""
    _require_admin()
    from ..models import NodeActivity
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    row = (NodeActivity.query
           .filter(NodeActivity.slot == slot)
           .filter(NodeActivity.last_seen >= cutoff)
           .first())
    if row is None:
        abort(404)
    return render_template('admin/nodespy.html', node=row)


@control_bp.route('/nodespy/<int:slot>/kick', methods=['POST'])
@login_required
def nodespy_kick(slot):
    """Forcibly disconnect the terminal session at this slot.

    Different from a ban — this just drops the current connection.
    The user can reconnect immediately. To prevent reconnect, sysop
    must also add an IP ban under /admin/ip-bans/.

    Optional `reason` form field is shown to the user before disconnect.

    Cross-process: this endpoint runs in `anetbbs-web` (gunicorn) but
    terminal sessions live in `anetbbs-telnet`. Their `_NODES` dicts are
    independent, so we set a flag on the user's `NodeActivity` row and
    let the telnet process's BBSSession poller see it and self-disconnect.
    """
    _require_admin()
    from ..models import db, NodeActivity, UserActivity
    reason = (request.form.get('reason') or '').strip() \
             or 'Disconnected by sysop'

    cutoff = datetime.utcnow() - timedelta(minutes=5)
    row = (NodeActivity.query
           .filter(NodeActivity.slot == slot)
           .filter(NodeActivity.last_seen >= cutoff)
           .first())
    if row is None:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'ok': False,
                            'error': f'no live session at slot {slot}'}), 404
        flash(f'No live session at slot {slot}.', 'warning')
        return redirect(url_for('control.index'))

    target_user = row.username
    row.kick_requested = True
    row.kick_reason = reason[:200]

    db.session.add(UserActivity(
        user_id=current_user.id,
        activity_type='kick_node',
        details=f'slot {slot} ({target_user}): {reason}',
        ip_address=request.remote_addr,
        service='web'))
    try:
        db.session.commit()
        ok = True
        msg = (f'Kick requested for {target_user} on slot {slot}. '
               f'They will be disconnected within ~5 seconds.')
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        ok = False
        msg = f'Kick flag commit failed: {exc}'

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': ok, 'message': msg})
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('control.index'))


@control_bp.route('/who.json')
@login_required
def who_json():
    """Live who's-online JSON — used by the auto-refreshing panel."""
    _require_admin()
    five_min_ago = datetime.utcnow() - timedelta(minutes=5)
    rows = (UserSession.query
            .filter(UserSession.last_seen >= five_min_ago)
            .order_by(UserSession.last_seen.desc()).all())
    out = []
    for r in rows:
        u = User.query.get(r.user_id) if r.user_id else None
        out.append({
            'username': u.username if u else '?',
            'page': r.page or '',
            'ip': r.ip_address or '',
            'agent': (r.user_agent or '')[:40],
            # naive UTC -> append 'Z' so JS Date.parse treats it as UTC
            'last_seen': (r.last_seen.isoformat() + 'Z') if r.last_seen else '',
        })
    return jsonify(out)
