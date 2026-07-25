"""Admin viewer for ``logs/door-errors.log``.

The Synchronet-compat door wrapper (``games/door_runner.py``)
appends a trace to that file whenever a user's door session crashes.
Format, repeated per error:

    [ISO-8601 UTC] door=<slug> user=<name>
    <multiline stack trace>

This page parses the file into discrete entries, lists them
newest-first, and lets the sysop clear the file once they've
acknowledged the breakage.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime

from flask import (Blueprint, current_app, flash,
                   redirect, render_template, url_for)
from flask_login import login_required


logger = logging.getLogger(__name__)
door_errors_bp = Blueprint('door_errors', __name__,
                           url_prefix='/admin/door-errors')


from .access_control import require_admin_or_403 as _admin_required


def _log_path() -> str:
    install_root = current_app.config.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(install_root, 'logs', 'door-errors.log')


# Match the runner's per-error preamble.
_ENTRY_RE = re.compile(
    r'^\[(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\]\s+'
    r'door=(?P<slug>\S+)\s+user=(?P<user>\S+)\s*$',
    re.MULTILINE,
)


def _parse(text: str):
    """Walk the log and yield one dict per error entry."""
    entries = []
    # Find every preamble; each entry runs from one preamble to the
    # next preamble (or end-of-file).
    matches = list(_ENTRY_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        trace = text[m.end():end].strip('\n')
        entries.append({
            'ts': m.group('ts'),
            'slug': m.group('slug'),
            'user': m.group('user'),
            'trace': trace,
        })
    entries.reverse()  # newest-first
    return entries


def _relative_age(iso_ts: str) -> str:
    try:
        dt = datetime.strptime(iso_ts.split('.')[0], '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return ''
    delta = datetime.utcnow() - dt
    if delta.total_seconds() < 60:
        return 'just now'
    if delta.total_seconds() < 3600:
        return f'{int(delta.total_seconds() // 60)}m ago'
    if delta.total_seconds() < 86400:
        return f'{int(delta.total_seconds() // 3600)}h ago'
    return f'{delta.days}d ago'


@door_errors_bp.route('/', methods=['GET'])
@login_required
def index():
    _admin_required()
    path = _log_path()
    raw = ''
    size = 0
    try:
        size = os.path.getsize(path)
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            raw = f.read()
    except FileNotFoundError:
        pass
    except OSError as exc:
        flash(f'Cannot read door-errors.log: {exc}', 'warning')
    entries = _parse(raw)
    for e in entries:
        e['relative_age'] = _relative_age(e['ts'])
    return render_template('admin/door_errors.html',
                           entries=entries,
                           log_path=path,
                           log_size=size)


@door_errors_bp.route('/clear', methods=['POST'])
@login_required
def clear():
    _admin_required()
    path = _log_path()
    try:
        # Truncate-in-place rather than delete — the file's fd is open
        # by every running door, and unlinking under them is iffy.
        with open(path, 'w'):
            pass
        flash('Door-errors log cleared.', 'success')
    except FileNotFoundError:
        flash('Log file was already gone.', 'info')
    except OSError as exc:
        flash(f'Could not clear log: {exc}', 'danger')
    return redirect(url_for('door_errors.index'))
