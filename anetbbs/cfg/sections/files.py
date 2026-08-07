"""File Areas section (anetbbs-cfg).

`network_id` (which echomail network owns a given file echo) is shown
read-only in the list but not editable here -- reassigning it is rare and
interacts with TIC routing, left to the web admin for now.
"""
from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, FileArea

FIELDS = [
    {"key": "tag", "label": "Tag", "kind": "text"},
    {"key": "name", "label": "Name", "kind": "text_nullable"},
    {"key": "description", "label": "Description", "kind": "text_nullable"},
    {"key": "storage_path", "label": "Storage Path", "kind": "text_nullable"},
    {"key": "min_access_level", "label": "Min Access Level", "kind": "int"},
    {"key": "min_write_level", "label": "Min Upload Level", "kind": "int_nullable"},
    {"key": "upload_permission", "label": "Upload Permission", "kind": "choice",
     "choices": ["users", "sysop", "none"]},
    {"key": "is_active", "label": "Active", "kind": "bool"},
    {"key": "is_subscribed", "label": "Subscribed (receive from upstream)", "kind": "bool"},
    {"key": "is_sysop_only", "label": "Sysop Only", "kind": "bool"},
    {"key": "password", "label": "Area Password", "kind": "text_nullable"},
]

HELP = [
    "Min Upload Level blank = same as Min Access Level.",
    "Network assignment (TIC routing): web admin only.",
]

NEW_DEFAULTS = {
    "tag": "", "name": None, "description": None, "storage_path": None,
    "min_access_level": 10, "min_write_level": None, "upload_permission": "users",
    "is_active": True, "is_subscribed": True, "is_sysop_only": False, "password": None,
}

COLUMNS = [
    ("Tag", 18, lambda a: a.tag),
    ("Name", 20, lambda a: a.name or ""),
    ("Network", 14, lambda a: a.network.name if a.network else "(local)"),
    ("MinLvl", 7, lambda a: a.min_access_level),
    ("Active", 6, lambda a: "Yes" if a.is_active else "No"),
]


def list_file_areas():
    return FileArea.query.order_by(FileArea.tag).all()


def values_from_area(a):
    return {f["key"]: getattr(a, f["key"]) for f in FIELDS}


def create_file_area(data):
    a = FileArea(**data)
    db.session.add(a)
    db.session.commit()
    return a


def update_file_area(a, data):
    for k, v in data.items():
        setattr(a, k, v)
    db.session.commit()


def delete_file_area(a):
    db.session.delete(a)
    db.session.commit()


def _add(stdscr):
    data = ui.run_form(stdscr, "New File Area", FIELDS, dict(NEW_DEFAULTS), help_lines=HELP)
    if data is None:
        return
    if not data.get("tag"):
        ui.show_message(stdscr, "Tag is required.", error=True)
        return
    try:
        create_file_area(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A file area with that tag already exists.", error=True)


def _edit(stdscr, a):
    data = ui.run_form(stdscr, f"Edit File Area: {a.tag}", FIELDS, values_from_area(a), help_lines=HELP)
    if data is None:
        return
    try:
        update_file_area(a, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A file area with that tag already exists.", error=True)


def _delete(stdscr, a):
    text = (f"Delete file area '{a.tag}'?\n"
            "This removes the area definition only -- files already on disk\n"
            "under its storage path are NOT deleted.")
    if ui.confirm(stdscr, text):
        try:
            delete_file_area(a)
        except IntegrityError:
            db.session.rollback()
            ui.show_message(
                stdscr,
                "Could not delete: other records (uploads, TIC queue, subscriptions)\n"
                "still reference this area. Clear those first, or use the web admin.",
                error=True,
            )


def run(stdscr):
    ui.run_list(
        stdscr, "File Areas", COLUMNS, list_file_areas,
        on_add=_add, on_edit=_edit, on_delete=_delete,
    )
