"""Pre-launch system check.

One page, admin-only, runs a battery of "is this BBS actually ready
for the public?" probes and shows a green/red checklist. Designed for
two moments:

1. **First install** — `bash install.sh` ran, the box booted, the
   sysop logs in once. /admin/preflight/ tells them what still needs
   manual attention (DNS records, firewall holes, missing email
   config, etc.) before flipping public.
2. **Pre-release verification** — before announcing the BBS publicly
   (e.g., posting to a mailing list, sharing with friends), the sysop
   runs the page once more to confirm nothing has regressed.

Each check returns a small dict ``{name, status, detail, fix}``.
``status`` is one of ``ok``, ``warn``, ``fail``. ``fix`` is a single
short sentence telling the sysop what to do — if a check fails,
nobody should have to grep through docs to figure out the remediation.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from typing import List

from flask import Blueprint, current_app, render_template
from flask_login import login_required
from sqlalchemy import text

from ..models import db
from ..version import VERSION


logger = logging.getLogger(__name__)
preflight_bp = Blueprint('preflight', __name__, url_prefix='/admin/preflight')


from .access_control import require_admin_or_403 as _admin_required


# ── Individual checks ──────────────────────────────────────────────────────
def _check(name, status, detail='', fix=''):
    return {'name': name, 'status': status, 'detail': detail, 'fix': fix}


def _check_disk(cfg):
    install_root = cfg.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        u = shutil.disk_usage(install_root)
    except OSError as exc:
        return _check('Disk space', 'fail', str(exc),
                      'Check the install volume is mounted.')
    free_gb = u.free / (1024 ** 3)
    pct = (1 - u.free / u.total) * 100
    detail = f'{free_gb:.1f} GB free ({pct:.1f}% used) on {install_root}'
    if free_gb < 0.5:
        return _check('Disk space', 'fail', detail,
                      'Free up space — under 500 MB is critical for SQLite + logs.')
    if free_gb < 2:
        return _check('Disk space', 'warn', detail,
                      'Under 2 GB free is tight for echomail + uploads. Consider attaching more storage.')
    return _check('Disk space', 'ok', detail)


def _check_db(cfg):
    try:
        db.session.execute(text('SELECT 1'))
    except Exception as exc:  # noqa: BLE001
        return _check('Database reachable', 'fail', str(exc),
                      'Check SQLALCHEMY_DATABASE_URI in .env and that the file is readable.')
    return _check('Database reachable', 'ok', 'SELECT 1 returned')


def _check_data_owned_by_service_user(cfg):
    install_root = cfg.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data = os.path.join(install_root, 'data')
    if not os.path.isdir(data):
        return _check('data/ directory exists', 'fail', f'{data} missing',
                      'Re-run install.sh to create the runtime data tree.')
    st = os.stat(data)
    try:
        import pwd
        owner = pwd.getpwuid(st.st_uid).pw_name
    except (KeyError, ImportError):
        owner = str(st.st_uid)
    # Best-effort: the service user is "anetbbs" by default but can vary.
    # We treat "data/ writable by the service that runs the web" as the
    # actual property we care about — anything we can write to is fine.
    test_path = os.path.join(data, '.preflight-write-test')
    try:
        with open(test_path, 'w') as f:
            f.write('ok')
        os.remove(test_path)
        return _check('data/ writable by service user', 'ok',
                      f'owned by {owner}, write test passed')
    except OSError as exc:
        return _check('data/ writable by service user', 'fail',
                      f'owned by {owner}: {exc}',
                      f'sudo chown -R $(systemctl show anetbbs-web -p User --value):$(systemctl show anetbbs-web -p User --value) {data}')


def _check_sudo_escalation(cfg):
    """Verify the running web worker can actually launch the upgrade.

    Two failure modes the auto-update endpoint can hit:

    1. **No sudoers grant for the wrapper.** ``sudo -n -l <wrapper>``
       returns non-zero. ``-l`` just queries permission, it doesn't
       execute, so this is safe.
    2. **Unit hardening blocks privilege escalation.** NoNewPrivileges
       being effectively "yes" for the unit, or a restrictive
       ``CapabilityBoundingSet=``, causes any sudo call (including the
       wrapper) to fail with "The 'no new privileges' flag is set" or
       "unable to change to root gid".

       A real live outage (2026-10-27) found that checking for a
       literal ``NoNewPrivileges=yes`` line in the unit file — what
       this function used to do — isn't enough: several seccomp-backed
       hardening directives (``SystemCallFilter=``,
       ``SystemCallArchitectures=``, ``RestrictNamespaces=``,
       ``RestrictRealtime=``, ``RestrictSUIDSGID=``,
       ``MemoryDenyWriteExecute=``, ``LockPersonality=``) each require
       the kernel's own ``no_new_privs`` bit to install their seccomp
       filter on a unit with no ``CAP_SYS_ADMIN`` (which this unit
       deliberately has none of), so systemd forces
       ``NoNewPrivileges=yes`` for real regardless of what the unit
       file says — confirmed by a real fresh restart on live
       production still showing ``NoNewPrivs: 1`` in
       ``/proc/<pid>/status`` with an explicit ``NoNewPrivileges=no``
       already in place. This is a hard kernel constraint, not a
       systemd policy default — there is no config override, which is
       why ``anetbbs-web.service`` must never carry any of those
       directives in the first place (see install.sh/update.sh's own
       unit-generation code for the full writeup). A text-only check
       can't know any of this; querying systemd's own computed
       ``NoNewPrivileges`` property (``systemctl show -p
       NoNewPrivileges --value``) can, since systemd itself applies
       that implication when computing it. No privileges needed for
       this query either.

    The earlier version of this check ran ``sudo -n /bin/true`` and
    reported "sudoers grant missing" on every well-configured install
    because /bin/true is correctly NOT in the narrow allowlist. The
    proper probe targets the specific command the auto-update will run.
    """
    install_root = cfg.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    wrapper = os.path.join(install_root, 'deploy', 'run_upgrade.sh')

    bad_directives = []

    # Ask systemd for the EFFECTIVE, computed NoNewPrivileges value --
    # not a text-file guess. Catches both a literal NoNewPrivileges=yes
    # AND the implied-by-other-directives case above.
    try:
        r = subprocess.run(
            ['systemctl', 'show', 'anetbbs-web.service', '-p',
             'NoNewPrivileges', '--value'],
            capture_output=True, text=True, timeout=5)
        effective_nnp = (r.stdout or '').strip().lower()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        effective_nnp = ''
    if effective_nnp == 'yes':
        bad_directives.append(
            'NoNewPrivileges=yes (effective — may be implied by other '
            'hardening directives even if no unit file literally sets '
            'it; see systemd.exec(5))')

    # CapabilityBoundingSet= isn't affected by the implied-NoNewPrivileges
    # trap above -- still fine to detect via a direct text read, no
    # privileges needed.
    unit_path = '/etc/systemd/system/anetbbs-web.service'
    try:
        with open(unit_path, 'r') as f:
            unit_text = f.read()
    except OSError:
        unit_text = ''
    for line in unit_text.splitlines():
        s = line.strip()
        if s.startswith('CapabilityBoundingSet='):
            # Any narrow set could exclude CAP_SETUID; flag if it doesn't
            # explicitly include the caps sudo needs.
            caps = s.split('=', 1)[1].lower()
            if caps and not all(c in caps for c in
                                ('cap_setuid', 'cap_setgid', 'cap_audit_write')):
                bad_directives.append(s)
    if bad_directives:
        return _check('sudo escalation from web service', 'fail',
                      'unit hardening blocks sudo: ' + '; '.join(bad_directives),
                      'Re-run sudo bash update.sh over SSH -- it removes the '
                      'seccomp-backed directives that force this on this '
                      'unit and restarts the service. Manually adding '
                      'NoNewPrivileges=no via systemctl edit alone will NOT '
                      'fix this if a seccomp-backed directive '
                      '(SystemCallFilter=, RestrictNamespaces=, etc.) is '
                      'still present elsewhere on the unit -- that forces '
                      'the flag back on regardless of an explicit override. '
                      'If a CapabilityBoundingSet= is also listed above, '
                      'add CAP_SETUID CAP_SETGID CAP_AUDIT_WRITE to it too, '
                      'then sudo systemctl daemon-reload && systemctl '
                      'restart anetbbs-web.')

    # Unit looks fine — now check that sudoers actually grants the
    # EXACT command the auto-update button will run. A prior version
    # of this probe just ran ``sudo -n -l <wrapper>`` with no
    # arguments, which only matches sudoers' second ("direct
    # invocation") grant alternative — never the systemd-run form
    # ``_spawn_upgrade()`` actually uses. That let this check report
    # "ok" while the real auto-update path stayed broken (the exact
    # failure shape this whole preflight page exists to catch). Build
    # the real probe command via the SAME helper ``_spawn_upgrade()``
    # calls, so the two can never silently diverge again.
    if not os.path.isfile(wrapper):
        return _check('sudo escalation from web service', 'warn',
                      f'upgrade wrapper not at {wrapper}',
                      'Re-run sudo bash update.sh to install the wrapper.')
    from .upgrades import build_upgrade_cmd  # local import: upgrades.py imports from this module
    probe_cmd = build_upgrade_cmd(wrapper, 'v0.0.0-preflight-probe',
                                  'https://example.invalid/preflight-probe.tar.gz',
                                  '0' * 64)
    try:
        r = subprocess.run(['sudo', '-n', '-l'] + probe_cmd,
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return _check('sudo escalation from web service', 'ok',
                          f'sudoers permits {wrapper} via systemd-run')
        stderr = (r.stderr or '').strip()[:200]
        return _check('sudo escalation from web service', 'fail',
                      f'sudo -n -l rejected the real upgrade command: {stderr}',
                      'Re-run sudo bash update.sh — it rewrites /etc/sudoers.d/anetbbs '
                      'with the grant for the current service user + install path.')
    except FileNotFoundError:
        return _check('sudo escalation from web service', 'fail',
                      'sudo binary not found',
                      'apt install sudo, then re-run update.sh.')
    except subprocess.TimeoutExpired:
        return _check('sudo escalation from web service', 'warn',
                      'sudo -n -l timed out after 5s',
                      'Unusual — check whether sudo itself is hanging.')


def _check_port_consistency(cfg):
    """Look for the classic "I just upgraded and my BBS moved ports" trap.

    Two failure shapes to surface:

    1. WEB_PORT in .env disagrees with what anetbbs-web actually binds.
       Happens when update.sh's auto-install block writes a fresh
       unit with hard-coded :5000 while the sysop's URL bookmarks
       expect :8080 (or whatever they were on previously).
    2. The MRC bridge is bound to the same port a sysop was using
       for the web UI before. Visitors hitting the old URL land on
       MRC instead of the BBS.
    """
    import re as _re
    expected = int(cfg.get('WEB_PORT', 5000) or 5000)
    install_root = cfg.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # What does the unit file actually use?
    unit_path = '/etc/systemd/system/anetbbs-web.service'
    actual = None
    try:
        with open(unit_path, 'r') as f:
            for line in f:
                m = _re.search(r'-b\s+\S*:(\d+)', line)
                if m:
                    actual = int(m.group(1))
                    break
    except OSError:
        pass
    if actual and actual != expected:
        return _check('WEB_PORT consistency', 'fail',
                      f'.env WEB_PORT={expected} but unit binds :{actual}',
                      f'Either fix .env (WEB_PORT={actual}) or rewrite the unit '
                      f'to -b 0.0.0.0:{expected} and restart anetbbs-web.')
    # Cross-check: is MRC on whatever port WEB_PORT used to be?
    # The bridge's own ExecStart is just `python -m mrc.bridge.main` --
    # no port ever appears on that line, it reads web_listen_port from
    # its own config.json. Read that directly instead of trying (and
    # previously failing silently) to regex a port out of the unit file.
    import json as _json
    mrc_bind = None
    mrc_config_path = os.path.join(install_root, 'mrc', 'bridge', 'config.json')
    try:
        with open(mrc_config_path, 'r') as f:
            mrc_bind = int(_json.load(f).get('web_listen_port') or 0) or None
    except (OSError, ValueError, _json.JSONDecodeError):
        pass
    if mrc_bind and mrc_bind == expected:
        return _check('WEB_PORT consistency', 'warn',
                      f'WEB_PORT and MRC bridge both want :{expected}',
                      'Move MRC_BRIDGE_PORT in .env (e.g., 8081) or move WEB_PORT.')
    return _check('WEB_PORT consistency', 'ok',
                  f'.env says {expected}, unit binds {actual or "?"}'
                  + (f', mrc on {mrc_bind}' if mrc_bind else ''))


def _check_listener(name, port, cfg, *, required=False):
    p = int(port or 0)
    if not p:
        return _check(f'{name} port configured', 'warn', 'disabled',
                      f'Set {name.upper()}_PORT in .env if you want this protocol.')
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(('127.0.0.1', p))
        return _check(f'{name} listener (:{p})', 'ok',
                      f'TCP connect to 127.0.0.1:{p} succeeded')
    except (socket.timeout, ConnectionRefusedError, OSError) as exc:
        st = 'fail' if required else 'warn'
        return _check(f'{name} listener (:{p})', st,
                      f'no listener on 127.0.0.1:{p} ({exc.__class__.__name__})',
                      'systemctl status anetbbs* and check logs for bind failures.')


def _check_sudoers():
    path = '/etc/sudoers.d/anetbbs'
    if not os.path.exists(path):
        return _check('sudoers grant present', 'warn',
                      f'{path} not found',
                      'Re-run update.sh — SCC restart buttons + auto-update wrapper need it.')
    try:
        st = os.stat(path)
        if st.st_mode & 0o077:
            return _check('sudoers grant present', 'warn',
                          f'permissions {oct(st.st_mode & 0o777)} are too open',
                          'sudo chmod 0440 /etc/sudoers.d/anetbbs')
    except OSError as exc:
        return _check('sudoers grant present', 'warn', str(exc), '')
    return _check('sudoers grant present', 'ok', path)



def _check_federation_reachable(cfg):
    reg_url = cfg.get('REGISTRY_URL') or ''
    if not reg_url:
        return _check('Federation hub reachable', 'warn', 'REGISTRY_URL not configured',
                      'Set REGISTRY_URL in Admin → Settings to enable federation.')
    try:
        import requests
        r = requests.get(reg_url.rstrip('/') + '/anetbbs.lst',
                         timeout=8,
                         headers={'User-Agent': 'ANetBBS/preflight'})
        if r.status_code == 200:
            return _check('Federation hub reachable', 'ok',
                          f'{reg_url} returned 200 ({len(r.text)} bytes)')
        return _check('Federation hub reachable', 'warn',
                      f'{reg_url} returned {r.status_code}',
                      'Hub may be down — your peer list won\'t refresh until it returns.')
    except Exception as exc:  # noqa: BLE001
        return _check('Federation hub reachable', 'warn',
                      f'{reg_url}: {exc.__class__.__name__}',
                      'Outbound HTTPS to the hub is blocked. Federation directory will be stale.')


# Real Medium finding from a security/performance audit (2026-09-02):
# this used to only check the LITERAL string 'change-me-in-production',
# which has never been this codebase's actual insecure default -- that's
# 'dev-secret-key-change-in-production' (config.py's Config.SECRET_KEY
# fallback, also checked by web_app.py's own boot-time guard and
# installer/upgrade.py's auto-heal step). An install still running the
# real known-insecure default got a false-positive "ok" here, on the
# one page whose whole job is to catch this before a sysop goes live.
# Matches the exact set web_app.py's own `_bad_defaults` checks.
_KNOWN_BAD_SECRET_KEYS = {
    'dev-secret-key-change-in-production',
    'changeme',
    'your-secret-key-here',
    '',
}


def _check_secret_key(cfg):
    key = cfg.get('SECRET_KEY') or ''
    if key in _KNOWN_BAD_SECRET_KEYS or 'insecure' in key.lower() or len(key) < 32:
        return _check('Strong SECRET_KEY', 'fail', 'using a known-weak default',
                      'Set SECRET_KEY in .env to a 32+ character random string before exposing publicly.')
    return _check('Strong SECRET_KEY', 'ok', f'set ({len(key)} chars)')


def _check_version(cfg):
    return _check('Running version', 'ok', VERSION,
                  'Check /admin/upgrades/ to see if newer is available.')


def _check_anetbbs_install_sentinel():
    p = '/etc/anetbbs.install'
    if os.path.exists(p):
        return _check('/etc/anetbbs.install written', 'ok', p)
    return _check('/etc/anetbbs.install written', 'warn',
                  'missing — privileged upgrade wrapper may not find INSTALL_DIR',
                  'Re-run update.sh, which writes this sentinel.')


def _run_all(cfg):
    checks: List[dict] = []
    checks.append(_check_version(cfg))
    checks.append(_check_disk(cfg))
    checks.append(_check_db(cfg))
    checks.append(_check_data_owned_by_service_user(cfg))
    checks.append(_check_sudo_escalation(cfg))
    checks.append(_check_port_consistency(cfg))
    checks.append(_check_listener('web', cfg.get('WEB_PORT', 5000), cfg, required=True))
    checks.append(_check_listener('telnet', cfg.get('TELNET_PORT', 23), cfg))
    checks.append(_check_listener('ssh', cfg.get('SSH_PORT', 2222), cfg))
    checks.append(_check_listener('rlogin', cfg.get('RLOGIN_PORT', 513), cfg))
    checks.append(_check_listener('ftp', cfg.get('FTP_PORT', 2121), cfg))
    checks.append(_check_listener('finger', cfg.get('FINGER_LISTEN_PORT', 79), cfg))
    checks.append(_check_sudoers())
    checks.append(_check_federation_reachable(cfg))
    checks.append(_check_secret_key(cfg))
    checks.append(_check_anetbbs_install_sentinel())
    return checks


@preflight_bp.route('/', methods=['GET'])
@login_required
def index():
    _admin_required()
    cfg = current_app.config
    started = time.monotonic()
    checks = _run_all(cfg)
    elapsed = (time.monotonic() - started) * 1000
    counts = {
        'ok': sum(1 for c in checks if c['status'] == 'ok'),
        'warn': sum(1 for c in checks if c['status'] == 'warn'),
        'fail': sum(1 for c in checks if c['status'] == 'fail'),
    }
    overall = 'ok' if counts['fail'] == 0 else 'fail'
    if overall == 'ok' and counts['warn']:
        overall = 'warn'
    return render_template('admin/preflight.html',
                           checks=checks, counts=counts,
                           overall=overall, elapsed_ms=int(elapsed))
