# anetbbs/web/personal_pages.py
"""
Personal web-pages server.

When `PERSONAL_PAGES_ENABLED=true`:
  - bbs.example.com/<dir>/        serves data/personal_pages/<dir>/index.html
                                  (or index.htm / default.html / default.htm /
                                   home.html — first one found)
  - bbs.example.com/<dir>/file    serves the file from that directory
  - bbs.example.com/~<user>/...   serves data/personal_pages/users/<user>/...
                                  so each registered user can publish.

Hooked via a Flask 404 handler so it never shadows real BBS routes —
only takes over when no other route matches.
"""
import os
import re
from pathlib import Path

from flask import (Blueprint, send_from_directory, abort, current_app,
                   render_template, redirect, url_for, request, flash)
from flask_login import login_required, current_user


pages_bp = Blueprint('personal_pages', __name__, url_prefix='/_pages')


# Files to try in order when the URL ends with a /
INDEX_FILES = ('index.html', 'index.htm', 'default.html', 'default.htm',
               'home.html', 'home.htm')


def _root():
    """Absolute path to data/personal_pages/. Created on demand."""
    base = Path(current_app.config.get('DATA_DIR', 'data')) / 'personal_pages'
    base.mkdir(parents=True, exist_ok=True)
    (base / 'users').mkdir(exist_ok=True)
    return base


def _safe_resolve(base, rel_path):
    """Combine base + rel_path safely (no traversal). Returns Path or None."""
    try:
        target = (base / rel_path).resolve()
        if not str(target).startswith(str(base.resolve()) + os.sep) \
                and target != base.resolve():
            return None
        return target
    except Exception:
        return None


# Audit H-5: personal pages are user-authored HTML served from the BBS's
# own origin. Every response gets a CSP sandbox WITHOUT allow-same-origin,
# so the browser treats the page as a unique opaque origin: its scripts
# can run, but cannot read BBS cookies, storage, or same-origin responses
# (e.g. an admin page's CSRF token) while a sysop is viewing it.
PERSONAL_PAGE_CSP = (
    "sandbox allow-scripts allow-forms allow-popups; "
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
    "media-src 'self' https:; font-src 'self' data:; connect-src 'none'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'")
# Types a browser would render as an active document when opened directly
# (and that the sandbox alone doesn't make inert for every browser) are
# always downloaded instead of displayed.
_FORCE_DOWNLOAD_EXTS = {'.svg', '.svgz', '.xml', '.xhtml', '.xht', '.xsl',
                        '.xslt', '.js', '.mjs'}
ALLOWED_UPLOAD_EXTS = {'.html', '.htm', '.css', '.js', '.txt', '.md', '.json',
                       '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp',
                       '.svg', '.mp3', '.ogg', '.wav', '.pdf', '.ans', '.asc',
                       '.nfo', '.diz'}


def _send_sandboxed(directory, name):
    resp = send_from_directory(
        directory, name,
        as_attachment=os.path.splitext(name)[1].lower() in _FORCE_DOWNLOAD_EXTS)
    resp.anetbbs_csp_override = PERSONAL_PAGE_CSP
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    resp.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    return resp


def _try_serve(target):
    """Given a Path, send it as a file or auto-index if it's a directory.
    Returns a Flask response or None if nothing matched."""
    if target is None or not target.exists():
        return None
    if target.is_file():
        return _send_sandboxed(target.parent, target.name)
    if target.is_dir():
        for idx in INDEX_FILES:
            candidate = target / idx
            if candidate.is_file():
                return _send_sandboxed(target, idx)
        return None
    return None


def _enabled():
    """Strict flag parse: the admin settings page can leave the raw
    string 'false' in live config, which is truthy (audit PP-1)."""
    v = current_app.config.get('PERSONAL_PAGES_ENABLED')
    if isinstance(v, str):
        return v.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(v)


def serve_root_page(path):
    """Serve a path of the form '<dir>[/<rest>]' from personal_pages/.
    Returns a response or None if not handled."""
    if not _enabled():
        return None
    if not path:
        return None
    base = _root()

    # Reserved names we never let personal-pages claim — safety guard
    # in case a sysop creates a folder that conflicts with a real route.
    reserved = {
        'admin', 'auth', 'profile', 'boards', 'pm', 'files', 'echomail',
        'mrcweb', 'mrcws', 'mrc', 'irc', 'shoutbox', 'static', 'api',
        'who', 'polls', 'finger', 'telegram', 'peers', 'ansi', 'term',
        'stats', 'feed', 'gemini', 'notifications', 'calendar', 'groups',
        'leaderboard', 'oneliners', 'qwk', 'blocks', 'm', 'tour', 'help',
        'about', 'search', 'register', 'login', 'logout', 'healthz',
        'robots.txt', 'humans.txt', 'sitemap.xml', '_pages', 'saved',
        'site', 'bulletins', 'file-areas', 'netmail', 'nodelist',
        'contacts', 'games',
        # Live route prefixes that can 404 internally (audit PP-1): a
        # sysop directory with one of these names must never be served.
        'wiki', 'registry', 'docs', 'downloads', 'mail', 'anetbbs.lst',
        'upgrades', 'pulse', 'mud', 'feeds', 'gallery', 'ebooks', 'pages',
        'notify', 'mrcweb', 'sixel', 'imsg', 'watch', 'telnet', 'web-terminal',
    }
    parts = path.split('/', 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ''
    # Every real route prefix is reserved too, derived from the live URL
    # map so a new blueprint can never be shadowed (audit PP-1).
    route_heads = {r.rule.strip('/').split('/')[0].lower()
                   for r in current_app.url_map.iter_rules()}
    if head.lower() in reserved or head.lower() in route_heads:
        return None

    if head.startswith('~'):
        # /~user/... -> data/personal_pages/users/<user>/...
        username = head[1:]
        if not re.fullmatch(r'[A-Za-z0-9._-]+', username):
            return None
        target_base = base / 'users' / username
        target = _safe_resolve(target_base, rest) if rest else target_base
        return _try_serve(target)

    # /folder/...
    # Real gap found in a security/performance audit: unlike the
    # `~user` branch just above (which regex-validates `username`
    # before ever touching the filesystem), `head` here was used
    # completely unvalidated. A path of '../<rest>' makes
    # target_base = base / '..' resolve to data/personal_pages/'s
    # PARENT (i.e. data/ itself), which .is_dir() happily confirms --
    # and _safe_resolve()'s own containment check is then evaluated
    # against that already-escaped target_base, so it passes trivially,
    # serving anything under data/ (including data/admin_password.txt,
    # the initial-admin plaintext password written on first boot) to
    # any request that reaches this 404 fallback. Resolving target_base
    # itself through the SAME containment check _safe_resolve() applies
    # to `rest` -- confirming it's still really inside `base` -- closes
    # this the same way the `~user` branch is already closed, instead
    # of trusting a bare .is_dir() truthiness check.
    target_base = _safe_resolve(base, head)
    if target_base is None or not target_base.is_dir():
        return None
    target = _safe_resolve(target_base, rest) if rest else target_base
    return _try_serve(target)


# ---------------------------------------------------------------------------
# Per-user file manager — drop files into your /~username/ space
# ---------------------------------------------------------------------------

@pages_bp.route('/')
@login_required
def my_pages():
    """List + manage the current user's personal-page files."""
    base = _root() / 'users' / current_user.username
    base.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(base.iterdir()):
        if p.is_file():
            files.append({
                'name': p.name,
                'size': p.stat().st_size,
                'is_index': p.name.lower() in INDEX_FILES,
            })
    enabled = _enabled()
    return render_template('personal_pages/index.html',
                           files=files, enabled=enabled,
                           username=current_user.username,
                           index_files=INDEX_FILES)


def _require_enabled():
    """Upload/delete exist only while the feature is on (audit H-5)."""
    if not _enabled():
        abort(404)


def _usage(base):
    files = [p for p in base.rglob('*') if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


@pages_bp.route('/upload', methods=['POST'])
@login_required
def upload():
    _require_enabled()
    base = _root() / 'users' / current_user.username
    base.mkdir(parents=True, exist_ok=True)
    f = request.files.get('file')
    if not f or not f.filename:
        flash('No file selected.', 'danger')
        return redirect(url_for('personal_pages.my_pages'))
    safe_name = os.path.basename(f.filename)
    if not safe_name or safe_name.startswith('.'):
        flash('Invalid filename.', 'danger')
        return redirect(url_for('personal_pages.my_pages'))
    if os.path.splitext(safe_name)[1].lower() not in ALLOWED_UPLOAD_EXTS:
        flash('That file type is not allowed on personal pages.', 'danger')
        return redirect(url_for('personal_pages.my_pages'))
    max_files = int(current_app.config.get('PERSONAL_PAGES_MAX_FILES', 100))
    max_bytes = int(current_app.config.get('PERSONAL_PAGES_MAX_BYTES', 10 * 1024 * 1024))
    count, used = _usage(base)
    existing = base / safe_name
    if existing.is_file():
        count -= 1
        used -= existing.stat().st_size
    data = f.read(max_bytes + 1)
    if count + 1 > max_files or used + len(data) > max_bytes:
        flash('Personal page storage limit reached.', 'danger')
        return redirect(url_for('personal_pages.my_pages'))
    with open(base / safe_name, 'wb') as out:
        out.write(data)
    flash(f'Uploaded {safe_name}.', 'success')
    return redirect(url_for('personal_pages.my_pages'))


@pages_bp.route('/delete/<path:filename>', methods=['POST'])
@login_required
def delete(filename):
    _require_enabled()
    base = _root() / 'users' / current_user.username
    target = _safe_resolve(base, filename)
    if target is None or not target.is_file():
        abort(404)
    try:
        target.unlink()
        flash(f'Deleted {filename}.', 'success')
    except Exception as exc:
        flash(f'Delete failed: {exc}', 'danger')
    return redirect(url_for('personal_pages.my_pages'))
