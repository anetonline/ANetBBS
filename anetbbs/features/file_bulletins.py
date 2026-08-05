"""File Bulletins — .txt/.asc/.ans files a sysop drops into
FILE_BULLETINS_DIR (config.py), auto-detected and admin-enabled at
Admin -> Bulletins -> Files. Distinct from the existing DB-authored
Bulletins (Message model, web textarea) -- this is for pre-made files:
classic BBS-style text/ANSI bulletins, or .ans score/news files a door
game writes out itself.

The file on disk is the source of truth for content; FileBulletin rows
are just display metadata (title/order/enabled/min_access_level) plus
the auto-registration bookkeeping described on the model itself.

Content is read as raw bytes and decoded latin-1 (byte-for-byte,
matching anetbbs/core/session.py's _load_display_screens -- the same
convention every other file-based screen in this project uses), then
rendered through the existing CP437/ANSI-aware ANView pipeline
(anedit.launch_aneview) so real ANSI art displays correctly.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r'^[A-Za-z0-9._+~\-]+$')
_ALLOWED_EXTENSIONS = ('.txt', '.asc', '.ans')


def safe_filename(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and '..' not in name


def resolve_dir(cfg) -> str | None:
    """Absolute, real path of the bulletins directory, or None if
    unconfigured. Creates it on first call so a fresh install doesn't
    need the sysop to mkdir it themselves before dropping in a file."""
    d = cfg.get('FILE_BULLETINS_DIR') or ''
    if not d:
        return None
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as exc:
        logger.warning('file_bulletins: cannot create %s: %s', d, exc)
        return None
    return os.path.realpath(d)


def scan_filenames(base: str) -> list[str]:
    """Real, safely-named .txt/.asc/.ans files directly inside *base*
    (depth 1 only -- a dropped-in subdirectory is invisible, same as
    downloads.py). Returns bare filenames, sorted."""
    if not base or not os.path.isdir(base):
        return []
    out = []
    try:
        for name in os.listdir(base):
            if not safe_filename(name):
                continue
            if not name.lower().endswith(_ALLOWED_EXTENSIONS):
                continue
            full = os.path.join(base, name)
            if not os.path.isfile(full):
                continue
            out.append(name)
    except OSError as exc:
        logger.warning('file_bulletins: scan of %s failed: %s', base, exc)
        return []
    out.sort(key=str.lower)
    return out


def _default_title(filename: str) -> str:
    stem, _dot, _ext = filename.rpartition('.')
    stem = stem or filename
    return stem.replace('_', ' ').replace('-', ' ').strip().title() or filename


def sync_bulletin_rows(cfg) -> list[str]:
    """Ensure a FileBulletin row exists for every real file currently in
    FILE_BULLETINS_DIR. New files get is_active=False (same "detected
    but off until the sysop confirms it" convention as everywhere else
    in this project) so nothing new appears to users unannounced. Never
    deletes rows for files that disappeared -- see FileBulletin's own
    docstring for why. Must be called inside an app context (caller's
    responsibility, matching every other DB-touching helper here).

    Returns the list of real filenames currently on disk.
    """
    from ..models import db, FileBulletin

    base = resolve_dir(cfg)
    filenames = scan_filenames(base) if base else []
    known = {r.filename for r in FileBulletin.query.all()}
    added = False
    for name in filenames:
        if name in known:
            continue
        db.session.add(FileBulletin(
            filename=name, title=_default_title(name), is_active=False))
        added = True
    if added:
        db.session.commit()
    return filenames


def get_visible_bulletins(cfg, user_level: int = 0, is_admin: bool = False):
    """Active FileBulletin rows whose file still exists on disk, gated
    by min_access_level, sorted for display. Returns a list of
    (row, full_path) tuples -- callers need both the row (title/id) and
    a ready-to-read path."""
    from ..models import FileBulletin

    base = resolve_dir(cfg)
    on_disk = set(scan_filenames(base)) if base else set()
    rows = (FileBulletin.query
            .filter_by(is_active=True)
            .order_by(FileBulletin.sort_order, FileBulletin.title)
            .all())
    out = []
    for r in rows:
        if r.filename not in on_disk:
            continue
        if not is_admin and user_level < (r.min_access_level or 0):
            continue
        out.append((r, os.path.join(base, r.filename)))
    return out


async def show_file_bulletins(session) -> None:
    """Entry point for LoginModule dispatch (module_type='file_bulletin')
    -- matches show_wall()/show_last_callers()'s own calling convention
    (a bare `await show_x(session)`), and doubles as the same function
    the main-menu 'A' choice ultimately reaches via
    BBSMenuUI.list_file_bulletins(). One implementation, two entry
    points, matching the ANSI-screen module_type's own precedent of
    reusing an already-menu-reachable display path from a logon/logoff
    module."""
    from .bbs_ui import BBSMenuUI
    await BBSMenuUI(session).list_file_bulletins()


def read_bulletin_body(full_path: str) -> str:
    """Raw bytes -> latin-1 str, matching _load_display_screens's own
    convention -- launch_aneview's CP437 decode step expects exactly
    this (each character's ordinal IS the original byte value)."""
    with open(full_path, 'rb') as fh:
        return fh.read().decode('latin-1')
