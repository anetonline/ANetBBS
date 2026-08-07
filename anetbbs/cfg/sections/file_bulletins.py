"""File Bulletins section (anetbbs-cfg).

Reuses anetbbs.features.file_bulletins.sync_bulletin_rows() -- the same
disk-scan-and-auto-register logic the web admin's index() route calls
before listing, so a file dropped into FILE_BULLETINS_DIR shows up here
too without duplicating that logic. Only metadata (title/order/active/
access level) is editable -- the file content itself lives on disk.
"""
from flask import current_app

from anetbbs.cfg import ui
from anetbbs.models import db, FileBulletin

FIELDS = [
    {"key": "title", "label": "Title", "kind": "text"},
    {"key": "sort_order", "label": "Sort Order", "kind": "int"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
    {"key": "min_access_level", "label": "Min Access Level", "kind": "int"},
]

COLUMNS = [
    ("Order", 6, lambda b: b.sort_order),
    ("Title", 26, lambda b: b.title),
    ("Filename", 26, lambda b: b.filename),
    ("Active", 6, lambda b: "Yes" if b.is_active else "No"),
]


def list_bulletins():
    from anetbbs.features import file_bulletins as fb
    fb.sync_bulletin_rows(current_app.config)
    return FileBulletin.query.order_by(FileBulletin.sort_order, FileBulletin.title).all()


def values_from_bulletin(b):
    return {f["key"]: getattr(b, f["key"]) for f in FIELDS}


def update_bulletin(b, data):
    for k, v in data.items():
        setattr(b, k, v)
    db.session.commit()


def delete_bulletin(b):
    """Removes the row only -- the file on disk is untouched (it'll be
    re-registered, inactive, next time the list is viewed)."""
    db.session.delete(b)
    db.session.commit()


def _edit(stdscr, b):
    data = ui.run_form(stdscr, f"Edit Bulletin: {b.filename}", FIELDS, values_from_bulletin(b))
    if data is None:
        return
    update_bulletin(b, data)


def _delete(stdscr, b):
    if ui.confirm(stdscr, f"Remove bulletin row for '{b.filename}'?\n"
                           "(the file itself is not deleted)"):
        delete_bulletin(b)


def run(stdscr):
    ui.run_list(
        stdscr, "File Bulletins", COLUMNS, list_bulletins,
        on_edit=_edit, on_delete=_delete,
        empty_hint="(no files found in FILE_BULLETINS_DIR)",
    )
