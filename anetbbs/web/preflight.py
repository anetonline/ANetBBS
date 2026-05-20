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
from flask_login import current_user, login_required
from sqlalchemy import text

from ..models import db
from ..version import VERSION


logger = logging.getLogger(__name__)
preflight_bp = Blueprint('preflight', __name__, url_prefix='/admin/preflight')


def _admin_required():
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        from flask import abort
        abort(403)


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
    2. **Unit hardening blocks privilege escalation.** A unit with
       ``NoNewPrivileges=true`` or a restrictive
       ``CapabilityBoundingSet=`` causes any sudo call (including
       the wrapper) to fail with "unable to change to root gid".
       We detect this by reading the unit file directly — no sudo
       needed — instead of trying to escalate and getting a misleading
       error.

    The earlier version of this check ran ``sudo -n /bin/true`` and
    reported "sudoers grant missing" on every well-configured install
    because /bin/true is correctly NOT in the narrow allowlist. The
    proper probe targets the specific command the auto-update will run.
    """
    install_root = cfg.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    wrapper = os.path.join(install_root, 'deploy', 'run_upgrade.sh')

    # Read the unit file to detect hardening BEFORE trying sudo.
    # Cheap, no privileges needed, catches the friend's regression.
    unit_path = '/etc/systemd/system/anetbbs-web.service'
    try:
        with open(unit_path, 'r') as f:
            unit_text = f.read()
    except OSError:
        unit_text = ''
    bad_directives = []
    for line in unit_text.splitlines():
        s = line.strip()
        if s.startswith('NoNewPrivileges=') and \
                s.split('=', 1)[1].strip().lower() in ('yes', 'true', '1'):
            bad_directives.append(s)
        elif s.startswith('CapabilityBoundingSet='):
            # Any narrow set could exclude CAP_SETUID; flag if it doesn't
            # explicitly include the caps sudo needs.
            caps = s.split('=', 1)[1].lower()
            if caps and not all(c in caps for c in
                                ('cap_setuid', 'cap_setgid', 'cap_audit_write')):
                bad_directives.append(s)
    if bad_directives:
        return _check('sudo escalation from web service', 'fail',
                      'unit hardening blocks sudo: ' + '; '.join(bad_directives),
                      'Edit /etc/systemd/system/anetbbs-web.service to remove '
                      'NoNewPrivileges=true and/or add CAP_SETUID CAP_SETGID '
                      'CAP_AUDIT_WRITE to CapabilityBoundingSet. Then sudo '
                      'systemctl daemon-reload && systemctl restart anetbbs-web.')

    # Unit looks fine — now check that sudoers actually grants the wrapper.
    if not os.path.isfile(wrapper):
        return _check('sudo escalation from web service', 'warn',
                      f'upgrade wrapper not at {wrapper}',
                      'Re-run sudo bash update.sh to install the wrapper.')
    try:
        r = subprocess.run(['sudo', '-n', '-l', wrapper],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return _check('sudo escalation from web service', 'ok',
                          f'sudoers permits {wrapper}')
        stderr = (r.stderr or '').strip()[:200]
        return _check('sudo escalation from web service', 'fail',
                      f'sudo -n -l rejected wrapper: {stderr}',
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
    mrc_bind = None
    try:
        with open('/etc/systemd/system/anetbbs-mrc-bridge.service', 'r') as f:
            for line in f:
                m = _re.search(r':(\d+)', line)
                if m and 'ExecStart' in line:
                    mrc_bind = int(m.group(1))
                    break
    except OSError:
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
                      f'systemctl status anetbbs* and check logs for bind failures.')


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


def _check_email_config(cfg):
    smtp_host = cfg.get('SMTP_HOST') or ''
    sysop_email = cfg.get('SYSOP_EMAIL') or os.environ.get('SYSOP_EMAIL') or ''
    if not smtp_host:
        return _check('Outbound email configured', 'warn',
                      'SMTP_HOST not set in .env',
                      'Set SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS so '
                      'NUV verifications and password resets actually mail out.')
    if not sysop_email:
        return _check('Outbound email configured', 'warn',
                      'SYSOP_EMAIL not set',
                      'Sysop alerts (login failures, federation problems) need a destination.')
    return _check('Outbound email configured', 'ok',
                  f'SMTP={smtp_host}, sysop={sysop_email}')


def _check_federation_reachable(cfg):
    reg_url = cfg.get('REGISTRY_URL') or 'https://bbs.a-net.fyi'
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


def _check_secret_key(cfg):
    key = cfg.get('SECRET_KEY') or ''
    if not key or 'insecure' in key.lower() or key == 'change-me-in-production' or len(key) < 32:
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
    checks.append(_check_listener('finger', cfg.get('FINGER_PORT', 79), cfg))
    checks.append(_check_sudoers())
    checks.append(_check_email_config(cfg))
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
