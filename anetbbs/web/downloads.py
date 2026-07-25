"""Public downloads page — auto-listing for the sysop's release directory.

The sysop drops a file (release tarball, .iso, util, .sha256 sidecar)
into ``DOWNLOADS_DIR`` (default ``{INSTALL_DIR}/data/releases``) and it
shows up on ``/downloads/`` immediately. No DB rows, no manual link
plumbing — directory scan on every request, cached briefly so the
page is snappy.

Safety considerations:

* **Filename whitelist** by extension (configurable via
  ``DOWNLOADS_EXTENSIONS``). Defaults cover archives, ISOs, checksum
  sidecars, and the obvious BBS-era text drops.
* **Realpath check** before serving — both the resolved file path and
  the resolved directory path must share a common ancestor, so a
  symlink or ``../`` query string can't reach outside the configured
  directory.
* **No directory listing** — files only, depth 1. If the sysop drops
  a subdirectory it's invisible to the listing AND to the serve route.

The page is public by default (no login). Sysops who want to gate
downloads behind login can set ``DOWNLOADS_REQUIRE_LOGIN=true`` in
.env. Admin-only is one more env flip away if asked.
"""
import hashlib
import logging
import mimetypes
import os
import re
import threading
import time
from datetime import datetime

from flask import (Blueprint, abort, current_app, render_template,
                   send_from_directory)
from flask_login import current_user


logger = logging.getLogger(__name__)
downloads_bp = Blueprint('downloads', __name__, url_prefix='/downloads')


# Cheap thread-safe cache. Listing changes rarely — re-scan at most
# once every 5 s rather than on every request.
_LISTING_TTL_SEC = 5
_cache_lock = threading.Lock()
_cache: dict = {'ts': 0, 'dir': '', 'entries': []}

_NAME_RE = re.compile(r'^[A-Za-z0-9._+~\-]+$')


def _allowed_extensions(cfg):
    """Comma-separated extension list, normalised to lowercase + leading '.'."""
    raw = (cfg.get('DOWNLOADS_EXTENSIONS') or '').lower()
    exts = []
    for piece in raw.split(','):
        piece = piece.strip().lstrip('.')
        if piece:
            exts.append('.' + piece)
    return tuple(exts)


def _matches_extension(name: str, exts) -> bool:
    """Honor multi-segment extensions like ``.tar.gz`` — endswith() does
    the right thing here because we lowercased + dot-prefixed everything."""
    n = name.lower()
    return any(n.endswith(ext) for ext in exts)


def _safe_filename(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and '..' not in name


def _resolve_dir(cfg):
    """Return the absolute, real path of the downloads directory, or None
    if it's mis-configured. Creates it on first call so the page never
    404s on a fresh install."""
    d = cfg.get('DOWNLOADS_DIR') or ''
    if not d:
        return None
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as exc:
        logger.warning('downloads: cannot create %s: %s', d, exc)
        return None
    return os.path.realpath(d)


def _scan(cfg):
    """One directory scan. Returns a list of dicts sorted by mtime desc."""
    base = _resolve_dir(cfg)
    if not base or not os.path.isdir(base):
        return base, []
    exts = _allowed_extensions(cfg)
    out = []
    try:
        for name in os.listdir(base):
            if not _safe_filename(name):
                continue
            if not _matches_extension(name, exts):
                continue
            full = os.path.join(base, name)
            if not os.path.isfile(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            from ..core.tz import fmt_eastern
            out.append({
                'name': name,
                'size': st.st_size,
                'mtime': st.st_mtime,
                'mtime_iso': fmt_eastern(
                    datetime.utcfromtimestamp(st.st_mtime), '%Y-%m-%d %H:%M %Z'),
                'mime': mimetypes.guess_type(name)[0] or 'application/octet-stream',
            })
    except OSError as exc:
        logger.warning('downloads: scan of %s failed: %s', base, exc)
        return base, []
    out.sort(key=lambda d: d['mtime'], reverse=True)
    return base, out


def _cached_scan(cfg):
    """Return (base_dir, entries) with a brief TTL cache so back-to-back
    page loads don't re-stat the directory every time."""
    base_hint = cfg.get('DOWNLOADS_DIR') or ''
    now = time.time()
    with _cache_lock:
        if (_cache['dir'] == base_hint
                and now - _cache['ts'] < _LISTING_TTL_SEC
                and _cache['entries'] is not None):
            return _cache.get('resolved_dir', base_hint), _cache['entries']

    base, entries = _scan(cfg)
    with _cache_lock:
        _cache['ts'] = now
        _cache['dir'] = base_hint
        _cache['resolved_dir'] = base
        _cache['entries'] = entries
    return base, entries


def _fmt_size(n: int) -> str:
    if n is None:
        return '?'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {unit}' if unit != 'B' else f'{n} {unit}'
        n /= 1024.0
    return f'{n:.1f} PB'


def _maybe_require_login(view):
    """Conditionally wrap the route — gate is controlled by config."""
    def wrapper(*args, **kwargs):
        cfg = current_app.config
        if cfg.get('DOWNLOADS_REQUIRE_LOGIN'):
            if not current_user.is_authenticated:
                from flask_login import login_required as _li
                return _li(view)(*args, **kwargs)
        return view(*args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


@downloads_bp.route('/')
@_maybe_require_login
def index():
    cfg = current_app.config
    if not cfg.get('DOWNLOADS_ENABLED', True):
        abort(404)
    base, entries = _cached_scan(cfg)
    # Pre-render display info to keep the template tidy.
    for e in entries:
        e['size_h'] = _fmt_size(e['size'])
    bbs_name = cfg.get('BBS_NAME', 'ANetBBS')
    return render_template('downloads/index.html',
                           entries=entries,
                           base_dir=base or cfg.get('DOWNLOADS_DIR'),
                           bbs_name=bbs_name)


@downloads_bp.route('/<path:filename>')
@_maybe_require_login
def serve(filename):
    """Serve one file. Filename is checked against the same whitelist
    the listing uses, and the resolved path is verified to be inside
    DOWNLOADS_DIR — symlinks or ``..`` traversal can't escape."""
    cfg = current_app.config
    if not cfg.get('DOWNLOADS_ENABLED', True):
        abort(404)
    if not _safe_filename(filename):
        abort(404)
    if not _matches_extension(filename, _allowed_extensions(cfg)):
        abort(404)
    base = _resolve_dir(cfg)
    if not base:
        abort(404)
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base + os.sep) and target != base:
        abort(404)
    if not os.path.isfile(target):
        abort(404)
    return send_from_directory(base, filename, as_attachment=True)


@downloads_bp.route('/<filename>.sha256')
@_maybe_require_login
def sha256_for(filename):
    """Generate a SHA-256 on demand for a release file. Cached on disk
    next to the file so subsequent hits are free."""
    cfg = current_app.config
    if not cfg.get('DOWNLOADS_ENABLED', True):
        abort(404)
    if not _safe_filename(filename) or not _matches_extension(
            filename, _allowed_extensions(cfg)):
        abort(404)
    base = _resolve_dir(cfg)
    if not base:
        abort(404)
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base + os.sep):
        abort(404)
    if not os.path.isfile(target):
        abort(404)
    sidecar = target + '.sha256'
    if os.path.isfile(sidecar):
        try:
            with open(sidecar, 'r') as fh:
                return fh.read(), 200, {'Content-Type': 'text/plain'}
        except OSError:
            pass
    # Compute + cache
    h = hashlib.sha256()
    try:
        with open(target, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b''):
                h.update(chunk)
    except OSError:
        abort(500)
    digest = h.hexdigest()
    text = f'{digest}  {filename}\n'
    try:
        with open(sidecar, 'w') as fh:
            fh.write(text)
    except OSError:
        pass
    return text, 200, {'Content-Type': 'text/plain'}
