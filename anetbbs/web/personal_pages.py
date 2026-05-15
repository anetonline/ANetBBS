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


def _try_serve(target):
    """Given a Path, send it as a file or auto-index if it's a directory.
    Returns a Flask response or None if nothing matched."""
    if target is None or not target.exists():
        return None
    if target.is_file():
        return send_from_directory(target.parent, target.name)
    if target.is_dir():
        for idx in INDEX_FILES:
            candidate = target / idx
            if candidate.is_file():
                return send_from_directory(target, idx)
        return None
    return None


def serve_root_page(path):
    """Serve a path of the form '<dir>[/<rest>]' from personal_pages/.
    Returns a response or None if not handled."""
    if not current_app.config.get('PERSONAL_PAGES_ENABLED'):
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
    }
    parts = path.split('/', 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ''
    if head.lower() in reserved:
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
    target_base = base / head
    if not target_base.is_dir():
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
    enabled = current_app.config.get('PERSONAL_PAGES_ENABLED', False)
    return render_template('personal_pages/index.html',
                           files=files, enabled=enabled,
                           username=current_user.username,
                           index_files=INDEX_FILES)


@pages_bp.route('/upload', methods=['POST'])
@login_required
def upload():
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
    f.save(str(base / safe_name))
    flash(f'Uploaded {safe_name}.', 'success')
    return redirect(url_for('personal_pages.my_pages'))


@pages_bp.route('/delete/<path:filename>', methods=['POST'])
@login_required
def delete(filename):
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
