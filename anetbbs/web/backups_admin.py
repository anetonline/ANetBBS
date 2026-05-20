"""Admin browser for the pre-update backups ``update.sh`` keeps.

Every upgrade snapshots `.env`, the production + dev SQLite DBs,
systemd unit files, and an `anetbbs-nginx` config (if present) into
``/tmp/anetbbs-backup-YYYYMMDDHHMMSS/`` and drops a ``MANIFEST`` file
recording the version transition. Those dirs accumulate forever
because we don't trust ourselves to GC them automatically.

This page lists them, lets the sysop delete old ones, and offers a
"restore" path that's intentionally narrow:

* **Restore .env** — copies the snapshot's `.env.bak` over the live
  `.env`. Common case: a bad merge added/removed keys.
* **Restore DB** — copies the snapshot's `anetbbs.db.bak` over the
  live `anetbbs.db`. Catastrophic-rollback case: a botched migration.

Both restores require a privileged helper invocation (since the
service user can't typically chown/chmod arbitrary files outside the
install). Helper: ``deploy/run_upgrade.sh`` is too narrow — we add a
small ``deploy/run_restore.sh`` for these operations.

If the helper isn't installed (older release without sudoers grant),
the page degrades to "Delete" only and explains the situation.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import List, Optional

from flask import (Blueprint, abort, current_app, flash,
                   redirect, render_template, request, url_for)
from flask_login import current_user, login_required


logger = logging.getLogger(__name__)
backups_bp = Blueprint('backups_admin', __name__,
                       url_prefix='/admin/backups')

# Backups land under /tmp by update.sh; same naming convention every time.
_BACKUP_GLOB = re.compile(r'^anetbbs-backup-(\d{14})$')
_BACKUP_ROOT = '/tmp'


def _admin_required():
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        abort(403)


def _parse_manifest(path: str) -> dict:
    """Read the small key=value MANIFEST file update.sh writes.
    Returns {} on any failure — the listing degrades to dir name only."""
    out = {}
    try:
        with open(path, 'r') as f:
            for line in f:
                k, _, v = line.partition('=')
                k = k.strip()
                if k and not k.startswith('#'):
                    out[k] = v.strip()
    except OSError:
        pass
    return out


def _dir_size(p: str) -> int:
    total = 0
    try:
        for entry in os.scandir(p):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += _dir_size(entry.path)
            except OSError:
                pass
    except OSError:
        pass
    return total


def _scan() -> List[dict]:
    rows = []
    try:
        for name in os.listdir(_BACKUP_ROOT):
            m = _BACKUP_GLOB.match(name)
            if not m:
                continue
            full = os.path.join(_BACKUP_ROOT, name)
            if not os.path.isdir(full):
                continue
            try:
                # YYYYMMDDHHMMSS → datetime
                created = datetime.strptime(m.group(1), '%Y%m%d%H%M%S')
            except ValueError:
                created = None
            manifest = _parse_manifest(os.path.join(full, 'MANIFEST'))
            present_files = {
                f for f in (
                    '.env.bak', 'anetbbs.db.bak', 'anetbbs_dev.db.bak',
                    'anetbbs-web.service.bak', 'anetbbs.service.bak',
                    'anetbbs-mrc-bridge.service.bak', 'anetbbs-finger.service.bak',
                    'anetbbs-nginx.bak',
                )
                if os.path.isfile(os.path.join(full, f))
            }
            rows.append({
                'name': name,
                'path': full,
                'created_at': created,
                'from_version': manifest.get('from_version', '?'),
                'to_version': manifest.get('to_version', '?'),
                'size_bytes': _dir_size(full),
                'files': present_files,
            })
    except OSError as exc:
        logger.warning('cannot scan %s: %s', _BACKUP_ROOT, exc)
    rows.sort(key=lambda r: r['created_at'] or datetime.min, reverse=True)
    return rows


def _human_size(n: int) -> str:
    units = ['B', 'KB', 'MB', 'GB']
    f = float(n)
    for u in units:
        if f < 1024:
            return f'{f:.1f} {u}' if u != 'B' else f'{n} {u}'
        f /= 1024
    return f'{f:.1f} TB'


def _safe_backup_dir(name: str) -> Optional[str]:
    """Reject anything that doesn't match our glob — same defensive
    check we use elsewhere to prevent ../ escapes."""
    if not _BACKUP_GLOB.match(name):
        return None
    p = os.path.realpath(os.path.join(_BACKUP_ROOT, name))
    if not p.startswith(_BACKUP_ROOT + os.sep):
        return None
    return p if os.path.isdir(p) else None


def _restore_helper() -> Optional[str]:
    install_root = current_app.config.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = os.path.join(install_root, 'deploy', 'run_restore.sh')
    return p if os.path.isfile(p) else None


@backups_bp.route('/', methods=['GET'])
@login_required
def index():
    _admin_required()
    rows = _scan()
    for r in rows:
        r['size_human'] = _human_size(r['size_bytes'])
        r['created_str'] = r['created_at'].strftime('%Y-%m-%d %H:%M UTC') if r['created_at'] else '?'
    helper = _restore_helper()
    return render_template('admin/backups.html',
                           rows=rows,
                           restore_available=bool(helper),
                           helper_path=helper)


@backups_bp.route('/<name>/delete', methods=['POST'])
@login_required
def delete(name):
    _admin_required()
    p = _safe_backup_dir(name)
    if not p:
        flash('Invalid backup name.', 'danger')
        return redirect(url_for('backups_admin.index'))
    # Try the direct path first — works for backups created by .58+
    # (update.sh chowns new backups to the service user). Falls back
    # to the sudoers-granted helper for older root-owned backups.
    try:
        shutil.rmtree(p)
        flash(f'Deleted {name}.', 'success')
        return redirect(url_for('backups_admin.index'))
    except PermissionError:
        ok, msg = _run_restore('delete', p)
        flash(f'Deleted {name} via helper. {msg}'
              if ok else f'Delete failed: {msg}',
              'success' if ok else 'danger')
    except OSError as exc:
        flash(f'Delete failed: {exc}', 'danger')
    return redirect(url_for('backups_admin.index'))


def _run_restore(kind: str, backup_dir: str) -> tuple[bool, str]:
    helper = _restore_helper()
    if not helper:
        return False, 'restore helper missing — re-run update.sh on this version'
    try:
        proc = subprocess.run(
            ['sudo', '-n', helper, kind, backup_dir],
            capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout + proc.stderr).strip()[:1000]
        return (proc.returncode == 0), out or f'exit {proc.returncode}'
    except subprocess.TimeoutExpired:
        return False, 'restore helper timed out'
    except FileNotFoundError as exc:
        return False, f'sudo or helper not callable: {exc}'


@backups_bp.route('/<name>/restore-env', methods=['POST'])
@login_required
def restore_env(name):
    _admin_required()
    p = _safe_backup_dir(name)
    if not p:
        flash('Invalid backup name.', 'danger')
        return redirect(url_for('backups_admin.index'))
    ok, msg = _run_restore('env', p)
    flash(('.env restored from ' + name + ' — restart services to pick up changes. ' + msg)
          if ok else 'Restore failed: ' + msg,
          'success' if ok else 'danger')
    return redirect(url_for('backups_admin.index'))


@backups_bp.route('/<name>/restore-db', methods=['POST'])
@login_required
def restore_db(name):
    _admin_required()
    p = _safe_backup_dir(name)
    if not p:
        flash('Invalid backup name.', 'danger')
        return redirect(url_for('backups_admin.index'))
    ok, msg = _run_restore('db', p)
    flash(('DB restored from ' + name + '. ' + msg)
          if ok else 'Restore failed: ' + msg,
          'success' if ok else 'danger')
    return redirect(url_for('backups_admin.index'))
