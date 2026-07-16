# anetbbs/web/control.py
"""
Sysop service control center — live status of every systemd unit AND
every TCP listener inside those units, plus restart + journal viewing.

The previous version of this file listed pre-merge units
(``anetbbs-telnet``, ``anetbbs-ssh``) that no longer exist, so the
panel showed "unknown" for half the service tree. This rewrite reflects
the current shape:

    anetbbs-web         gunicorn web + MSP + SYSTAT + federation
    anetbbs             telnet / SSH / rlogin / FTP
    anetbbs-mrc-bridge  MRC chat bridge
    anetbbs-finger      RFC 1288 finger daemon

Each unit declares the TCP ports it owns. We probe those independently
of systemctl so the sysop can tell apart "process up but listener
crashed" from "process down."

Service control uses systemctl. To make this work without prompting
for sudo, ``deploy/sudoers.anetbbs`` grants the service user permission
to run *only* anetbbs-* unit operations.

Under Docker, there's no systemd inside the container at all, so this
module dispatches on ``ANETBBS_RUNTIME`` (set in the Dockerfile, unset
on bare metal) instead:
  - ``systemd`` (default): the systemctl-based behavior above, unchanged.
  - ``docker-single``: the single-container quick-start path -- swaps
    systemctl calls for ``supervisorctl`` against the same 5-process
    supervisord (see docker/single/supervisord.conf).
  - ``docker-compose``: the multi-container path -- delegates to
    ``control_docker.py``, which talks to the Docker Engine API over
    the socket mounted into the web container.
"""
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   abort, jsonify, request, current_app, Response)
from flask_login import login_required, current_user

from ..models import User, UserSession

try:
    import eventlet.tpool  # type: ignore
    _TPOOL_AVAILABLE = True
except ImportError:
    eventlet = None  # type: ignore
    _TPOOL_AVAILABLE = False


def _run_subprocess(*args, **kwargs):
    """subprocess.run(), but off eventlet's greened subprocess/os.read
    machinery when running under gunicorn+eventlet (web_app.py calls
    eventlet.monkey_patch() unconditionally). Mirrors the fix already
    live in metrics.py's sampler (v1.0b2.131), which found this exact
    pattern -- a tight sequential loop of subprocess.run() calls, one
    per known unit -- crash-looping with "Second simultaneous read on
    fileno N detected" once a transient hiccup left a read's
    fd-listener registered in eventlet's shared epoll hub past the
    point its fd number got recycled by the next call. This module has
    the identical shape: `/status.json` calls `_systemctl_read` once
    per KNOWN_UNITS entry every time the Control Center panel polls
    (every 5s while open), so the same collision risk applies here."""
    if _TPOOL_AVAILABLE:
        return eventlet.tpool.execute(subprocess.run, *args, **kwargs)
    return subprocess.run(*args, **kwargs)


_RUNTIME = os.environ.get('ANETBBS_RUNTIME', 'systemd')

# KNOWN_UNITS' systemd-unit names -> the short name used by the other
# two runtimes (docker-compose's service name in docker-compose.yml,
# and supervisord's [program:] name in docker/single/supervisord.conf
# -- both happen to use the same short names).
UNIT_TO_SERVICE_NAME = {
    'anetbbs-web': 'web',
    'anetbbs': 'terminal',
    'anetbbs-mrc-bridge': 'mrc-bridge',
    'anetbbs-finger': 'finger',
    'anetbbs-binkp': 'binkp',
}


# Probe-result cache: opening multiple admin tabs (or other clients)
# refreshing /status.json shouldn't multiply how often we knock on
# every listener. Each probe greets the BBS protocols and a fast
# disconnect used to spam the journal with BrokenPipeError stacks —
# fixed at the session layer in v1.0a2.31, but capping probe
# frequency is good hygiene on its own.
_PROBE_CACHE_TTL_SEC = 4.0
_probe_cache_lock = threading.Lock()
_probe_cache: dict = {}   # (host, port, proto) -> (result, expires_at)


def _probe_cached(key, fetch):
    now = time.time()
    with _probe_cache_lock:
        cached = _probe_cache.get(key)
        if cached and now < cached[1]:
            return cached[0]
    result = fetch()
    with _probe_cache_lock:
        _probe_cache[key] = (result, time.time() + _PROBE_CACHE_TTL_SEC)
    return result


control_bp = Blueprint('control', __name__, url_prefix='/admin/control')


# Service tree — each entry is one systemd unit plus the TCP listeners
# it owns. `port_key` references the Flask config key the actual port
# comes from; `port_default` is the fallback if the key isn't set.
KNOWN_UNITS = [
    {
        'unit': 'anetbbs-web',
        'label': 'Web + Federation',
        'desc': ('Flask / gunicorn web UI plus MSP (RFC 1312) instant '
                 'messaging, SYSTAT user-list, the federation hub, '
                 'and the inter-BBS directory refresher.'),
        'icon': 'bi-globe2',
        'ports': [
            {'name': 'HTTP',   'key': 'WEB_PORT',    'default': 5000, 'proto': 'tcp'},
            {'name': 'MSP',    'key': 'MSP_PORT',    'default': 18,   'proto': 'tcp'},
            {'name': 'SYSTAT', 'key': 'SYSTAT_PORT', 'default': 11,   'proto': 'udp'},
        ],
    },
    {
        'unit': 'anetbbs',
        'label': 'Terminal Protocols',
        'desc': ('Single asyncio process serving telnet, SSH, rlogin, '
                 'and FTP. Each protocol is gated by its own '
                 '*_ENABLED flag in the .env file.'),
        'icon': 'bi-terminal',
        'ports': [
            {'name': 'Telnet', 'key': 'TELNET_PORT', 'default': 2233, 'proto': 'tcp'},
            {'name': 'SSH',    'key': 'SSH_PORT',    'default': 2234, 'proto': 'tcp'},
            {'name': 'rlogin', 'key': 'RLOGIN_PORT', 'default': 513,  'proto': 'tcp'},
            {'name': 'FTP',    'key': 'FTP_PORT',    'default': 21,   'proto': 'tcp'},
        ],
    },
    {
        'unit': 'anetbbs-mrc-bridge',
        'label': 'MRC Chat Bridge',
        'desc': ('Persistent client connection to the MRC network at '
                 'mrc.bottomlessabyss.net. No inbound ports — health '
                 'is purely the systemd ActiveState.'),
        'icon': 'bi-chat-dots',
        'ports': [],
    },
    {
        'unit': 'anetbbs-finger',
        'label': 'Finger (RFC 1288)',
        'desc': 'Classic finger daemon — peers can query user presence.',
        'icon': 'bi-info-circle',
        'ports': [
            {'name': 'Finger', 'key': None, 'default': 79, 'proto': 'tcp'},
        ],
    },
    {
        'unit': 'anetbbs-binkp',
        'label': 'BinkP Inbound',
        'desc': ('BinkP/1.1 inbound listener — accepts incoming sessions '
                 'from remote echomail nodes that want to deliver mail to '
                 'or pull mail from this BBS.'),
        'icon': 'bi-envelope-arrow-down',
        'ports': [
            {'name': 'BinkP', 'key': 'BINKP_LISTEN_PORT', 'default': 24554, 'proto': 'tcp'},
        ],
    },
]


VALID_ACTIONS = ('start', 'stop', 'restart', 'reload')

from .access_control import require_admin_or_403 as _require_admin


def _systemctl_read(*args):
    """Run a read-only systemctl command WITHOUT sudo. ``systemctl show``,
    ``status``, etc. work for unprivileged users out of the box on
    systemd — we only need sudo for the state-changing verbs.

    Past versions of this module shelled out through ``sudo -n
    systemctl`` for every call. That broke on Ubuntu because sudo
    resolves ``systemctl`` via its own ``secure_path`` (typically
    ``/usr/bin/systemctl``) and compares it literally against the
    sudoers rule's path. Rules written against ``/bin/systemctl``
    silently didn't match → every read returned "permission denied"
    → the panel showed every service as ``unknown``. Sidestepping
    sudo for reads makes the panel robust regardless of which path
    sudo picks."""
    cmd = ['systemctl'] + list(args)
    try:
        r = _run_subprocess(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, '', 'timeout'
    except FileNotFoundError:
        return False, '', 'systemctl not found'
    except Exception as exc:  # pylint: disable=broad-except
        return False, '', str(exc)


def _systemctl_change(*args):
    """Run a state-changing systemctl command (start/stop/restart) via
    sudo so the gunicorn worker can do it without root. Requires the
    NOPASSWD entries in /etc/sudoers.d/anetbbs (auto-installed by
    install.sh/update.sh)."""
    cmd = ['sudo', '-n', 'systemctl'] + list(args)
    try:
        r = _run_subprocess(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, '', 'timeout'
    except FileNotFoundError:
        return False, '', 'systemctl not found'
    except Exception as exc:  # pylint: disable=broad-except
        return False, '', str(exc)


def _unit_state_systemd(unit):
    """Return a dict with ActiveState / SubState / ActiveEnterTimestamp /
    MainPID. Falls back to a partial dict on failure so the caller can
    always render *something*."""
    ok, out, _ = _systemctl_read(
        'show', unit,
        '--property=ActiveState,SubState,ActiveEnterTimestamp,MainPID,LoadState')
    state = {'ActiveState': 'unknown', 'SubState': '',
             'ActiveEnterTimestamp': '', 'MainPID': '0',
             'LoadState': 'unknown'}
    if not ok:
        return state
    for line in out.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            if k in state:
                state[k] = v
    return state


def _supervisorctl(*args):
    """Run a supervisorctl command against the single-container image's
    supervisord (docker/single/supervisord.conf). Structurally mirrors
    _systemctl_read()'s subprocess pattern -- supervisorctl needs no
    sudo since supervisord itself already runs as root in that image."""
    cmd = ['supervisorctl', '-c', '/app/docker/single/supervisord.conf'] + list(args)
    try:
        r = _run_subprocess(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, '', 'timeout'
    except FileNotFoundError:
        return False, '', 'supervisorctl not found'
    except Exception as exc:  # pylint: disable=broad-except
        return False, '', str(exc)


def _unit_state_supervisor(unit):
    """Same return shape as _unit_state_systemd(), sourced from
    ``supervisorctl status <program>``. Output looks like:
        web    RUNNING   pid 123, uptime 0:05:23
        binkp  STOPPED   Not started
        finger FATAL     Exited too quickly
    """
    state = {'ActiveState': 'unknown', 'SubState': '',
             'ActiveEnterTimestamp': '', 'MainPID': '0',
             'LoadState': 'loaded'}
    program = UNIT_TO_SERVICE_NAME.get(unit)
    if not program:
        return state
    ok, out, _ = _supervisorctl('status', program)
    line = (out or '').strip()
    if not line:
        return state
    parts = line.split(None, 2)
    if len(parts) < 2:
        return state
    status_word = parts[1].upper()
    state['SubState'] = status_word.lower()
    state['ActiveState'] = 'active' if status_word == 'RUNNING' else 'inactive'
    rest = parts[2] if len(parts) > 2 else ''
    if 'pid ' in rest:
        try:
            pid_part = rest.split('pid ', 1)[1]
            state['MainPID'] = pid_part.split(',')[0].strip()
        except (IndexError, ValueError):
            pass
    return state


def _unit_state(unit):
    if _RUNTIME == 'docker-compose':
        from .control_docker import unit_state as _docker_unit_state
        return _docker_unit_state(unit)
    if _RUNTIME == 'docker-single':
        return _unit_state_supervisor(unit)
    return _unit_state_systemd(unit)


def _probe_tcp_uncached(port, host='127.0.0.1', timeout=0.4):
    """Pure listener health — open a TCP connection and immediately
    close it. The BBS protocols (telnet/SSH/rlogin) greet on accept,
    so a fast probe-and-close looks like a disconnected client. We
    classify that as "listening" — it just confirmed the socket is
    accepting connections. The session layer treats the resulting
    BrokenPipe as a clean unwind (v1.0a2.31+), so this no longer
    spams the journal."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _probe_tcp(port, host='127.0.0.1', timeout=0.4):
    return _probe_cached(
        (host, int(port), 'tcp'),
        lambda: _probe_tcp_uncached(port, host=host, timeout=timeout))


def _probe_udp_uncached(port, host='127.0.0.1', timeout=0.4):
    """UDP can't be probed by connect alone — the kernel happily accepts
    a connect() to a closed UDP socket. We fall back to checking whether
    something is bound by looking at /proc/net/udp; if that's unreadable
    we return None (unknown) rather than a misleading "down"."""
    try:
        # netstat-equivalent: scan /proc/net/udp for a listener on `port`
        # bound to 0.0.0.0 or 127.0.0.1.
        target = f'{int(port):04X}'
        for fname in ('/proc/net/udp', '/proc/net/udp6'):
            try:
                with open(fname) as f:
                    for line in f.readlines()[1:]:
                        cols = line.split()
                        if len(cols) < 2:
                            continue
                        local = cols[1]
                        if local.endswith(':' + target):
                            return True
            except OSError:
                continue
        return False
    except Exception:  # pylint: disable=broad-except
        return None


def _probe_udp(port, host='127.0.0.1', timeout=0.4):
    return _probe_cached(
        (host, int(port), 'udp'),
        lambda: _probe_udp_uncached(port, host=host, timeout=timeout))


def _resolve_port(cfg, port_spec):
    """Resolve a port dict to its effective port number."""
    if port_spec.get('key'):
        return int(cfg.get(port_spec['key'], port_spec['default']))
    return int(port_spec['default'])


def _enrich_unit(cfg, spec):
    """Bundle systemctl state + per-port probe results for one unit."""
    state = _unit_state(spec['unit'])
    active = state['ActiveState']
    is_running = active == 'active'

    ports = []
    for p in spec['ports']:
        port = _resolve_port(cfg, p)
        if p['proto'] == 'tcp':
            up = _probe_tcp(port)
        elif p['proto'] == 'udp':
            up = _probe_udp(port)
        else:
            up = None
        ports.append({
            'name': p['name'], 'port': port, 'proto': p['proto'].upper(),
            'up': up,
        })

    # Listener health summary used by the template's pill colour.
    listener_summary = 'na'
    if ports:
        tcp_results = [pp['up'] for pp in ports if pp['proto'] == 'TCP']
        if tcp_results and all(tcp_results):
            listener_summary = 'all_up'
        elif tcp_results and not any(tcp_results):
            listener_summary = 'all_down'
        elif tcp_results:
            listener_summary = 'partial'

    return {
        'unit': spec['unit'],
        'label': spec['label'],
        'desc': spec['desc'],
        'icon': spec.get('icon', 'bi-gear'),
        'active': active,
        'sub': state['SubState'],
        'since': state['ActiveEnterTimestamp'],
        'pid': state['MainPID'],
        'load': state['LoadState'],
        'is_running': is_running,
        'ports': ports,
        'listener_summary': listener_summary,
    }


@control_bp.route('/')
@login_required
def index():
    _require_admin()
    cfg = current_app.config
    services = [_enrich_unit(cfg, spec) for spec in KNOWN_UNITS]

    # Aggregate banner counts
    running = sum(1 for s in services if s['is_running'])
    total = len(services)
    listener_problems = sum(
        1 for s in services
        if s['listener_summary'] in ('all_down', 'partial'))

    # Web users — anyone with a UserSession row in the last 5 min.
    five_min_ago = datetime.utcnow() - timedelta(minutes=5)
    web_sessions = (UserSession.query
                    .filter(UserSession.last_seen >= five_min_ago)
                    .order_by(UserSession.last_seen.desc()).all())

    return render_template('admin/control.html',
                           services=services,
                           web_sessions=web_sessions,
                           summary={'running': running,
                                    'total': total,
                                    'listener_problems': listener_problems})


@control_bp.route('/status.json')
@login_required
def status_json():
    """JSON snapshot of every unit + listener — drives live refresh."""
    _require_admin()
    cfg = current_app.config
    services = [_enrich_unit(cfg, spec) for spec in KNOWN_UNITS]
    return jsonify({
        'updated': datetime.utcnow().isoformat() + 'Z',
        'services': services,
    })


@control_bp.route('/metrics.json')
@login_required
def metrics_json():
    """Per-PID CPU%/RSS/thread ring-buffer snapshot for the live graphs.

    The sampler thread is started by `web_app.create_app` and runs
    inside the gunicorn worker. Returns ``available=False`` if psutil
    isn't installed so the front-end can render an explanatory pill
    instead of an empty chart."""
    _require_admin()
    from .metrics import snapshot
    return jsonify(snapshot())


@control_bp.route('/connections.json')
@login_required
def connections_json():
    """Per-protocol connection counts for the terminal-side card and
    the aggregate dashboard chip. Pulled from NodeActivity (terminal
    sessions) + UserSession (web sessions) within the last 5 min."""
    _require_admin()
    from ..models import NodeActivity
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    rows = (NodeActivity.query
            .filter(NodeActivity.last_seen >= cutoff)
            .all())
    counts = {'telnet': 0, 'ssh': 0, 'rlogin': 0, 'web': 0}
    for r in rows:
        proto = (r.protocol or '').lower()
        if proto in counts:
            counts[proto] += 1
    counts['web'] = (UserSession.query
                     .filter(UserSession.last_seen >= cutoff)
                     .count())
    return jsonify({
        'updated': datetime.utcnow().isoformat() + 'Z',
        'counts': counts,
    })


def _change_state(unit, action):
    """Dispatch a start/stop/restart/reload to whichever backend this
    runtime uses. Returns (ok, out, err) in all three cases."""
    if _RUNTIME == 'docker-compose':
        from .control_docker import change_state as _docker_change_state
        return _docker_change_state(unit, action)
    if _RUNTIME == 'docker-single':
        program = UNIT_TO_SERVICE_NAME.get(unit)
        if not program:
            return False, '', f'no supervisor program mapped for unit {unit}'
        # supervisorctl has no separate "reload" verb -- treat it as restart.
        verb = 'restart' if action == 'reload' else action
        return _supervisorctl(verb, program)
    return _systemctl_change(action, unit)


@control_bp.route('/service/<unit>/<action>', methods=['POST'])
@login_required
def service_action(unit, action):
    _require_admin()
    if unit not in {s['unit'] for s in KNOWN_UNITS}:
        flash(f'Unknown unit: {unit}', 'danger')
        return redirect(url_for('control.index'))
    if action not in VALID_ACTIONS:
        flash(f'Unknown action: {action}', 'danger')
        return redirect(url_for('control.index'))
    ok, out, err = _change_state(unit, action)
    if ok:
        flash(f'{action} {unit}: OK', 'success')
    else:
        # Surface the underlying error so the sysop can fix it without
        # digging through logs. Common systemd ones: "a password is
        # required" (NOPASSWD rule missing), "command not allowed"
        # (sudoers path doesn't match the resolved systemctl binary).
        flash(f'{action} {unit} failed: {err or out or "no output"}',
              'danger')
    return redirect(url_for('control.index'))


def _read_journal_systemd(unit, lines=200):
    """Return ``journalctl`` output for *unit*. We never elevate via
    sudo here — that fails under the unit's restricted capability set.
    The service user just needs read access to the system journal
    (``systemd-journal`` / ``adm`` group on Debian/Ubuntu)."""
    try:
        r = _run_subprocess(
            ['journalctl', '-u', unit, '--no-pager',
             '-n', str(int(lines))],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            log = (r.stderr or r.stdout
                   or '(journalctl returned non-zero with no output)')
            if 'No journal files' in log or 'Permission' in log:
                log += (
                    '\n\nHint: the service user needs read access to the '
                    'system journal. On Debian/Ubuntu run:\n'
                    f'    sudo usermod -aG systemd-journal,adm {os.getenv("USER", "<your-username>")}\n'
                    'then restart anetbbs-web.')
            return log
        return r.stdout or '(empty)'
    except Exception as exc:  # pylint: disable=broad-except
        return f'Could not read journal: {exc}'


def _read_journal_supervisor(unit, lines=200):
    """supervisorctl tail -- the [program:] stanzas in
    docker/single/supervisord.conf all log to stdout, captured by
    `docker logs` for the whole container; supervisorctl's own tail
    only sees what it's buffered since its own start, which is good
    enough for the panel's purposes."""
    program = UNIT_TO_SERVICE_NAME.get(unit)
    if not program:
        return f'No supervisor program mapped for unit {unit}'
    ok, out, err = _supervisorctl('tail', '-{}'.format(int(lines)), program)
    if not ok:
        return err or out or '(supervisorctl tail returned no output)'
    return out or '(empty)'


def _read_journal(unit, lines=200):
    if _RUNTIME == 'docker-compose':
        from .control_docker import read_logs as _docker_read_logs
        return _docker_read_logs(unit, lines=lines)
    if _RUNTIME == 'docker-single':
        return _read_journal_supervisor(unit, lines=lines)
    return _read_journal_systemd(unit, lines=lines)


@control_bp.route('/service/<unit>/journal')
@login_required
def service_journal(unit):
    _require_admin()
    if unit not in {s['unit'] for s in KNOWN_UNITS}:
        abort(404)
    try:
        lines = max(20, min(5000, int(request.args.get('n', 200))))
    except (TypeError, ValueError):
        lines = 200
    log = _read_journal(unit, lines=lines)
    return render_template('admin/journal.html', unit=unit, log=log,
                           lines=lines)


@control_bp.route('/service/<unit>/journal.txt')
@login_required
def service_journal_download(unit):
    """Plain-text download of the journal — sysop can keep a copy or
    paste into a bug report."""
    _require_admin()
    if unit not in {s['unit'] for s in KNOWN_UNITS}:
        abort(404)
    try:
        lines = max(20, min(20000, int(request.args.get('n', 2000))))
    except (TypeError, ValueError):
        lines = 2000
    log = _read_journal(unit, lines=lines)
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    filename = f'{unit}-{ts}.log'
    return Response(
        log,
        mimetype='text/plain; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename={filename}'})


# ---------------------------------------------------------------------------
# NodeSpy + who-online — unchanged from the prior revision
# ---------------------------------------------------------------------------

@control_bp.route('/nodespy.json')
@login_required
def nodespy_json():
    """JSON snapshot of all live NodeActivity rows."""
    _require_admin()
    from ..models import NodeActivity
    rows = (NodeActivity.query.order_by(NodeActivity.slot).all())
    out = []
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    for r in rows:
        if r.last_seen and r.last_seen < cutoff:
            continue
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
    """Forcibly disconnect the terminal session at this slot."""
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
            'last_seen': (r.last_seen.isoformat() + 'Z') if r.last_seen else '',
        })
    return jsonify(out)
