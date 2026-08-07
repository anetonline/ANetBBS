"""Backups section (anetbbs-cfg) -- browse/delete update.sh's pre-update
snapshots (data/backups/anetbbs-backup-YYYYMMDDHHMMSS/). Reuses
anetbbs.web.backups_admin's own _scan()/_safe_backup_dir()/_human_size()
helpers directly rather than re-implementing the manifest parsing and
path-safety checks.

Restore (.env / DB) is deliberately NOT included here -- it goes through
a privileged helper script (deploy/run_restore.sh, sudoers-gated) and is
a real "can corrupt a live install" operation; left web-admin-only
(Admin -> Backups) where that confirmation flow already lives.
"""
import shutil

from anetbbs.cfg import ui

COLUMNS = [
    ("Created", 17, lambda b: b["created_at"].strftime("%Y-%m-%d %H:%M") if b["created_at"] else "?"),
    ("From", 10, lambda b: b["from_version"]),
    ("To", 10, lambda b: b["to_version"]),
    ("Size", 10, lambda b: _human_size(b["size_bytes"])),
    ("Files", 20, lambda b: ",".join(sorted(f.replace(".bak", "") for f in b["files"]))[:20]),
]


def _human_size(n):
    from anetbbs.web.backups_admin import _human_size as _hs
    return _hs(n)


def list_backups():
    from anetbbs.web.backups_admin import _scan
    return _scan()


def delete_backup(name):
    """Returns (ok: bool, message: str)."""
    from anetbbs.web.backups_admin import _safe_backup_dir
    p = _safe_backup_dir(name)
    if not p:
        return False, "Invalid backup name."
    try:
        shutil.rmtree(p)
        return True, f"Deleted {name}."
    except PermissionError:
        return False, ("Delete failed: permission denied. This backup was created by an "
                        "older update.sh (root-owned) -- delete it via the web admin "
                        "(Admin -> Backups), which has the privileged-helper fallback.")


def _delete(stdscr, b):
    if not ui.confirm(stdscr, f"Delete backup '{b['name']}' ({_human_size(b['size_bytes'])})?"):
        return
    ok, msg = delete_backup(b["name"])
    if not ok:
        ui.show_message(stdscr, msg, error=True)


def run(stdscr):
    ui.run_list(
        stdscr, "Backups (restore: use web admin)", COLUMNS, list_backups,
        on_delete=_delete,
        empty_hint="(no backups found)",
    )
