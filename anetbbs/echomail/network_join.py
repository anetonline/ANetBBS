# anetbbs/echomail/network_join.py
"""Zip-inspection helpers for the public "apply to join this network"
infopack (anetbbs/web/network_join.py + anetbbs/web/hub_admin.py's
join-form admin tab).

A hub sysop uploads one zip (their infopack -- rules text, area lists,
ANSI art, etc., e.g. a real one: annet.txt, readme.txt, systems.txt,
annet.ans, annet.na, annet_file.na). We need to pick out which .txt
member inside it is "the rules" to display inline on the public page,
not just offer the whole zip as a download.
"""
import logging
import os
import zipfile

logger = logging.getLogger(__name__)


def join_dir_for_identity(config, identity):
    """Where this hub identity's join-form uploads (infopack.zip) live.

    The default identity keeps the original flat NETWORK_JOIN_DIR
    (uploaded infopacks predate multi-hub-identity support -- an
    existing install's already-uploaded infopack.zip must keep working
    at its current path, unmoved). Every other identity gets its own
    subdirectory keyed by id, since two identities could otherwise
    upload identically-named files."""
    base = config.get('NETWORK_JOIN_DIR',
                      os.path.join(config['DATA_DIR'], 'network_join'))
    if identity is None or identity.is_default:
        return base
    return os.path.join(base, str(identity.id))


def list_txt_members(zip_path):
    """Return [(member_name, size), ...] for every .txt member in the
    zip, largest-first. Empty list if the path isn't a valid zip or
    has no .txt members at all."""
    if not zipfile.is_zipfile(zip_path):
        return []
    members = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.filename.lower().endswith('.txt'):
                    members.append((info.filename, info.file_size))
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning('network_join: could not list zip members: %s', exc)
        return []
    members.sort(key=lambda m: m[1], reverse=True)
    return members


def auto_pick_rules_member(zip_path):
    """Return the name of the largest .txt member in the zip -- the
    best default guess for "which file is the rules text" -- or None
    if the zip has no .txt members at all."""
    members = list_txt_members(zip_path)
    return members[0][0] if members else None


def extract_member_text(zip_path, member_name):
    """Read one member's bytes out of the zip and decode to text.
    Tries strict UTF-8 first, then CP437 (the classic BBS-art
    encoding), then falls back to latin-1 (which can decode any byte
    sequence, so it never raises). Returns None if the zip is invalid
    or the member doesn't exist."""
    if not zipfile.is_zipfile(zip_path):
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            raw = zf.read(member_name)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        logger.warning('network_join: could not extract %r from %s: %s',
                       member_name, zip_path, exc)
        return None
    for encoding in ('utf-8', 'cp437'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', errors='replace')
