"""ANetBBS in-place upgrade — sysop UI + federation API.

Two halves living in the same module:

* **Public-ish API** at ``/api/releases/latest`` — returns JSON describing
  the newest ``ANetBBS-vX.YaZ.NN.tar.gz`` in the configured downloads
  directory. Every install exposes this. The canonical "what's the latest
  alpha?" answer comes from whichever host is configured as ``REGISTRY_URL``,
  but the endpoint runs everywhere so a private
  network of peers can serve their own line.
* **Sysop UI** at ``/admin/upgrades/`` — admin-only. Shows current
  ``VERSION``, polls the configured upstream, compares, and offers a
  one-click "Download & Install" that hands the version string to the
  privileged wrapper ``deploy/run_upgrade.sh``. The wrapper verifies the
  sha256 from the same API response before extracting the tarball, so
  a hijacked upstream still can't ship a tampered binary unless the
  sha256 it publishes matches.

Security note: the install endpoint shells out to
``sudo /opt/anetbbs/deploy/run_upgrade.sh <version>`` (path resolved at
runtime to whatever ``INSTALL_DIR`` is on this box). The sudoers file
shipped in ``deploy/sudoers.anetbbs`` grants the service user
passwordless execution of *only* that script with a strict version
regex enforced inside the script itself.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

import requests
from flask import (Blueprint, abort, current_app, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from ..version import VERSION


logger = logging.getLogger(__name__)

# Two blueprints share this module — one public-ish JSON API mounted at
# the root, and one admin UI mounted under /admin/. Keeping them in the
# same file means the version-parsing + tarball-scan helpers are shared
# without an awkward cross-module import.
upgrades_api_bp = Blueprint('upgrades_api', __name__)
upgrades_admin_bp = Blueprint('upgrades_admin', __name__,
                              url_prefix='/admin/upgrades')


# === Version handling ========================================================

# Public alpha versions look like: v1.0a2.36
# Major.Minor + 'a' + AlphaPhase + '.' + Point.
_VERSION_RE = re.compile(r'^v(\d+)\.(\d+)a(\d+)\.(\d+)$')


def _parse_version(s: str):
    """Return a 4-tuple (major, minor, alpha, point) or None for unparseable."""
    if not s:
        return None
    m = _VERSION_RE.match(s.strip())
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def _version_newer(remote: str, local: str) -> bool:
    """True iff `remote` is strictly newer than `local`. Unparseable
    versions count as not-newer so we never offer a downgrade or junk."""
    r = _parse_version(remote)
    l = _parse_version(local)
    if not r or not l:
        return False
    return r > l


# === Tarball scan ============================================================

_TARBALL_RE = re.compile(
    r'^ANetBBS-(v\d+\.\d+a\d+\.\d+)\.tar\.gz$'
)


def _downloads_dir(cfg) -> Optional[str]:
    d = cfg.get('DOWNLOADS_DIR') or ''
    if not d:
        return None
    return os.path.realpath(d) if os.path.isdir(d) else None


def _sha256_of(path: str) -> Optional[str]:
    """Compute sha256 of a file. Try the cached sidecar first
    (Downloads page already writes one); otherwise compute live."""
    sidecar = path + '.sha256'
    if os.path.isfile(sidecar):
        try:
            with open(sidecar) as f:
                first = f.read().strip().split()
                if first and re.fullmatch(r'[0-9a-fA-F]{64}', first[0]):
                    return first[0].lower()
        except OSError:
            pass
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _scan_releases(cfg):
    """Return [{version, name, size, mtime, path}, ...] sorted newest-first
    by parsed version (NOT mtime — file mtimes are unreliable after rsync).
    """
    base = _downloads_dir(cfg)
    if not base:
        return []
    out = []
    try:
        for name in os.listdir(base):
            m = _TARBALL_RE.match(name)
            if not m:
                continue
            ver = m.group(1)
            parsed = _parse_version(ver)
            if not parsed:
                continue
            full = os.path.join(base, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out.append({
                'version': ver,
                'name': name,
                'size': st.st_size,
                'mtime': st.st_mtime,
                'path': full,
                'parsed': parsed,
            })
    except OSError as exc:
        logger.warning('upgrades: scan of %s failed: %s', base, exc)
        return []
    out.sort(key=lambda r: r['parsed'], reverse=True)
    return out


def _latest_payload(req) -> Optional[dict]:
    """Build the public JSON body for the newest tarball."""
    cfg = current_app.config
    rows = _scan_releases(cfg)
    if not rows:
        return None
    top = rows[0]
    sha = _sha256_of(top['path'])
    # Build absolute URL pointing at our existing /downloads/ route so
    # peers can pull the tarball without needing direct fs access.
    url = (req.host_url.rstrip('/')
           + url_for('downloads.serve', filename=top['name']))
    return {
        'version': top['version'],
        'name': top['name'],
        'url': url,
        'sha256': sha,
        'size': top['size'],
        'published_at': datetime.utcfromtimestamp(top['mtime'])
                                .strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


# === Public API endpoint =====================================================

@upgrades_api_bp.route('/api/releases/latest')
def releases_latest():
    """Return JSON describing the newest tarball this BBS is publishing.

    Body shape (200):
        {"version": "v1.0a2.42",
         "name": "ANetBBS-v1.0a2.42.tar.gz",
         "url": "https://your-registry-host/downloads/...",
         "sha256": "<hex>",
         "size": 7234574,
         "published_at": "2026-05-19T12:50:00Z"}

    404 with `{"detail": "no releases"}` when the directory is empty.
    """
    p = _latest_payload(request)
    if not p:
        return jsonify({'detail': 'no releases'}), 404
    return jsonify(p)


# === Sysop UI ================================================================

# Cache the upstream check briefly so a sysop spamming F5 doesn't hammer
# the federation hub. 30 seconds is fine — release cadence is days.
_UPSTREAM_TTL_SEC = 30
_upstream_lock = threading.Lock()
_upstream_cache = {'ts': 0.0, 'url': '', 'data': None, 'error': None}


def _upstream_url(cfg) -> str:
    base = (cfg.get('REGISTRY_URL') or '').rstrip('/')
    return base + '/api/releases/latest'


def _fetch_upstream(cfg, force: bool = False):
    """Return (data_or_none, error_or_none). Caches briefly."""
    url = _upstream_url(cfg)
    if not url.startswith(('http://', 'https://')):
        return None, 'upstream not configured — set REGISTRY_URL in Admin → Settings'
    now = time.time()
    with _upstream_lock:
        if (not force
                and _upstream_cache['url'] == url
                and now - _upstream_cache['ts'] < _UPSTREAM_TTL_SEC):
            return _upstream_cache['data'], _upstream_cache['error']
    try:
        resp = requests.get(url, timeout=10,
                            headers={'User-Agent': 'ANetBBS/upgrades'})
        if resp.status_code == 404:
            data, err = None, 'upstream has no releases yet'
        elif resp.status_code != 200:
            data, err = None, f'upstream returned {resp.status_code}'
        else:
            data, err = resp.json(), None
    except requests.RequestException as exc:
        data, err = None, f'upstream unreachable: {exc.__class__.__name__}'
    except ValueError as exc:
        data, err = None, f'upstream sent non-JSON: {exc}'
    with _upstream_lock:
        _upstream_cache.update({'ts': now, 'url': url,
                                'data': data, 'error': err})
    return data, err


def _require_admin():
    if not current_user.is_authenticated:
        abort(401)
    if not getattr(current_user, 'is_admin', False):
        abort(403)


def _local_releases(cfg):
    """List local release tarballs (newest first by parsed version) for
    the rollback drawer. We only show OLDER-than-current ones since
    rolling forward is what /install does. Format mtime here so the
    template doesn't need a custom Jinja filter."""
    rows = _scan_releases(cfg)
    cur = _parse_version(VERSION)
    if cur:
        rows = [r for r in rows if r['parsed'] < cur]
    for r in rows:
        r['mtime_str'] = datetime.utcfromtimestamp(r['mtime']) \
                                 .strftime('%Y-%m-%d %H:%M UTC')
    return rows[:3]


@upgrades_admin_bp.route('/', methods=['GET'])
@login_required
def index():
    _require_admin()
    cfg = current_app.config
    upstream, err = _fetch_upstream(cfg, force=False)
    newer = bool(upstream
                 and _version_newer(upstream.get('version', ''), VERSION))
    return render_template('admin/upgrades.html',
                           current=VERSION,
                           upstream=upstream,
                           upstream_url=_upstream_url(cfg),
                           upstream_error=err,
                           newer=newer,
                           rollback_candidates=_local_releases(cfg))


@upgrades_admin_bp.route('/check', methods=['POST'])
@login_required
def check():
    _require_admin()
    cfg = current_app.config
    data, err = _fetch_upstream(cfg, force=True)
    body = {'current': VERSION,
            'upstream': data,
            'error': err,
            'newer': bool(data and _version_newer(data.get('version', ''),
                                                  VERSION))}
    return jsonify(body)


# === Install ================================================================

_UPGRADE_LOG_PATH = '/tmp/anetbbs_upgrade.log'
_INSTALL_LOCK = threading.Lock()
_install_state = {'running': False, 'started_at': 0.0, 'version': ''}


def _wrapper_path(cfg) -> str:
    """Resolve deploy/run_upgrade.sh relative to the install root.
    INSTALL_DIR config wins; otherwise derive from this file's location."""
    install_dir = cfg.get('INSTALL_DIR') or ''
    if not install_dir:
        # anetbbs/web/upgrades.py → install root is two-parents up
        install_dir = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(install_dir, 'deploy', 'run_upgrade.sh')


@upgrades_admin_bp.route('/install', methods=['POST'])
@login_required
def install():
    _require_admin()
    cfg = current_app.config
    target = (request.form.get('version') or '').strip()
    if not _parse_version(target):
        return jsonify({'ok': False, 'error': 'bad version'}), 400

    upstream, err = _fetch_upstream(cfg, force=True)
    if err or not upstream:
        return jsonify({'ok': False, 'error': err or 'no upstream'}), 502
    if upstream.get('version') != target:
        return jsonify({'ok': False,
                        'error': 'upstream version mismatch — refresh page'}), 409
    if not _version_newer(target, VERSION):
        return jsonify({'ok': False,
                        'error': 'already at or ahead of upstream'}), 409

    wrapper = _wrapper_path(cfg)
    if not os.path.isfile(wrapper):
        return jsonify({'ok': False,
                        'error': f'upgrade wrapper missing: {wrapper}'}), 500

    sha256 = upstream.get('sha256') or ''
    url = upstream.get('url') or ''
    if not re.fullmatch(r'[0-9a-fA-F]{64}', sha256):
        return jsonify({'ok': False, 'error': 'upstream sha256 invalid'}), 502
    if not url.startswith(('http://', 'https://')):
        return jsonify({'ok': False, 'error': 'upstream url invalid'}), 502

    with _INSTALL_LOCK:
        if _install_state['running']:
            return jsonify({'ok': False,
                            'error': 'install already running'}), 409
        _install_state.update({'running': True,
                               'started_at': time.time(),
                               'version': target})

    # Truncate prior log, then spawn the wrapper detached so the HTTP
    # request doesn't get killed when update.sh restarts gunicorn.
    try:
        open(_UPGRADE_LOG_PATH, 'w').close()
    except OSError:
        pass

    _spawn_upgrade(wrapper, target, url, sha256, label='upgrade')
    return jsonify({'ok': True,
                    'log_url': url_for('upgrades_admin.log_tail')})


def _spawn_upgrade(wrapper: str, target: str, url: str, sha256: str,
                   *, label: str) -> None:
    """Spawn the privileged upgrade wrapper in its own systemd scope.

    Why a scope: the wrapper inherits the cgroup of whatever spawned it
    (gunicorn for the web service). When ``update.sh`` later runs
    ``systemctl stop anetbbs-web`` in Step 3, systemd terminates every
    process in the unit's cgroup — including our running wrapper. The
    upgrade halts at the service-stop step, leaving the box on the old
    version with anetbbs-web down. ``systemd-run --scope`` creates a
    transient scope under system.slice (when invoked via sudo as root)
    so the wrapper escapes the unit's cgroup and survives the stop.

    Sudoers ships an entry granting exactly this command shape.
    """
    cmd = ['sudo', '-n',
           '/usr/bin/systemd-run', '--scope', '--collect', '--quiet',
           wrapper, target, url, sha256]

    def _runner():
        try:
            with open(_UPGRADE_LOG_PATH, 'a') as log:
                log.write(f'[{datetime.utcnow().isoformat()}Z] starting '
                          f'{label}: ' + ' '.join(cmd) + '\n')
                log.flush()
                proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                        close_fds=True, start_new_session=True)
                try:
                    proc.wait()
                except RuntimeError as _e:
                    # eventlet's hub raises "Second simultaneous read on fileno N"
                    # when its internal fd-monitoring conflicts with proc.wait().
                    # The upgrade is already running detached in its own systemd
                    # scope (systemd-run --scope), so this is non-fatal — drop it.
                    if 'simultaneous read' not in str(_e):
                        raise
                log.write(f'\n[{datetime.utcnow().isoformat()}Z] '
                          f'exit {proc.returncode}\n')
                if proc.returncode != 0:
                    # Check for the container/VPS sudo audit restriction.
                    # On LXC/OpenVZ hosts the kernel audit subsystem is
                    # unavailable; sudo fails before running our script.
                    try:
                        with open(_UPGRADE_LOG_PATH) as lf:
                            log_text = lf.read()
                    except OSError:
                        log_text = ''
                    if ('unable to change to root gid' in log_text
                            or 'sudoers_audit' in log_text):
                        log.write(
                            '\n[!] Web upgrade is not supported on this host.\n'
                            '    sudo cannot access the kernel audit subsystem\n'
                            '    (common on LXC/OpenVZ containers and some VPS).\n'
                            '    Run the upgrade manually from a terminal:\n\n'
                            f'      sudo anetbbs-upgrade {target}\n\n'
                            '    Or extract the tarball and run update.sh as root.\n'
                        )
        except Exception as exc:
            try:
                with open(_UPGRADE_LOG_PATH, 'a') as log:
                    log.write(f'\n[runner] exception: {exc!r}\n')
            except OSError:
                pass
        finally:
            with _INSTALL_LOCK:
                _install_state['running'] = False

    threading.Thread(target=_runner, daemon=True,
                     name=f'anetbbs-{label}').start()


@upgrades_admin_bp.route('/rollback', methods=['POST'])
@login_required
def rollback():
    """Reinstall an older tarball that lives in our local data/releases/.

    Unlike /install, this skips the upstream check entirely — the
    "trust" comes from the file already being on disk (the sysop put
    it there). Same privileged wrapper runs the actual upgrade, same
    sha256 verification path (we compute the sha here and pass it).
    """
    _require_admin()
    cfg = current_app.config
    target = (request.form.get('version') or '').strip()
    if not _parse_version(target):
        return jsonify({'ok': False, 'error': 'bad version'}), 400

    # Locate a matching tarball among the local releases.
    rows = _scan_releases(cfg)
    match = next((r for r in rows if r['version'] == target), None)
    if not match:
        return jsonify({'ok': False,
                        'error': f'no local tarball for {target}'}), 404

    # Rollback is intentionally a downgrade — guard against accidentally
    # picking a NEWER one, which the caller would use /install for.
    if _version_newer(target, VERSION):
        return jsonify({'ok': False,
                        'error': 'rollback target must be older than current'}), 409
    if target == VERSION:
        return jsonify({'ok': False,
                        'error': 'already on that version'}), 409

    sha = _sha256_of(match['path']) or ''
    if not re.fullmatch(r'[0-9a-fA-F]{64}', sha):
        return jsonify({'ok': False, 'error': 'cannot compute sha256'}), 500
    # Local URL routed through our own /downloads/ — the wrapper uses
    # curl, which is happy with 127.0.0.1, and we don't have to grant
    # the wrapper raw file-read access outside its sudoers.
    url = (request.host_url.rstrip('/')
           + url_for('downloads.serve', filename=match['name']))

    wrapper = _wrapper_path(cfg)
    if not os.path.isfile(wrapper):
        return jsonify({'ok': False,
                        'error': f'upgrade wrapper missing: {wrapper}'}), 500

    with _INSTALL_LOCK:
        if _install_state['running']:
            return jsonify({'ok': False,
                            'error': 'install already running'}), 409
        _install_state.update({'running': True,
                               'started_at': time.time(),
                               'version': target})
    try:
        open(_UPGRADE_LOG_PATH, 'w').close()
    except OSError:
        pass
    _spawn_upgrade(wrapper, target, url, sha, label='rollback')
    return jsonify({'ok': True,
                    'log_url': url_for('upgrades_admin.log_tail')})


# ANSI-escape stripper. update.sh's `info`/`ok`/`warn`/`fail` helpers
# emit colour codes that travel through into /tmp/anetbbs_upgrade.log
# as literal `\033[…m` bytes. The browser renders those as glyphs,
# turning every "[INFO]" prefix into "[0;32m[INFO][0m".
# Strip them server-side so the JS only has to colour structural
# content, not work around terminal escapes.
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


@upgrades_admin_bp.route('/log', methods=['GET'])
@login_required
def log_tail():
    _require_admin()
    try:
        with open(_UPGRADE_LOG_PATH, 'rb') as f:
            data = f.read()
    except FileNotFoundError:
        data = b'(no log yet)\n'
    except OSError as exc:
        data = f'(log read error: {exc})\n'.encode()
    text = _ANSI_RE.sub('', data.decode('utf-8', errors='replace'))
    body = {'running': _install_state['running'],
            'version': _install_state['version'],
            'log': text}
    return jsonify(body)
