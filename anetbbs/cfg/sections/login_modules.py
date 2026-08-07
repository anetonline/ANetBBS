"""Login Modules section (anetbbs-cfg) -- logon/logoff actions (wall
prompt, ANSI screen, file bulletins, shell command, native/Python
doors). params_json is edited as raw JSON text, validated on save --
see MODULE_TYPES' help text for each type's expected shape (mirrors
login_modules_admin.py's _params_help()).
"""
import json

from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, LoginModule

MODULE_TYPE_CHOICES = [
    "wall", "lastcallers", "ansi", "file_bulletin", "shell",
    "door_native", "door_python",
]
EVENT_TYPE_CHOICES = ["logon", "logoff"]

PARAMS_HELP = {
    "wall": '{}',
    "lastcallers": '{}',
    "ansi": '{"slot": "welcome"}',
    "file_bulletin": '{}',
    "shell": '{"command": "/path/to/script.sh"}',
    "door_native": '{"path": "/path/to/door", "args": "--node $NODE"}',
    "door_python": '{"module": "anetbbs.doors.mything", "func": "run"}',
}

FIELDS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "description", "label": "Description", "kind": "text_nullable"},
    {"key": "event_type", "label": "Event", "kind": "choice", "choices": EVENT_TYPE_CHOICES},
    {"key": "module_type", "label": "Module Type", "kind": "choice", "choices": MODULE_TYPE_CHOICES},
    {"key": "params_json", "label": "Params (JSON)", "kind": "text"},
    {"key": "min_access_level", "label": "Min Access Level", "kind": "int"},
    {"key": "sort_order", "label": "Sort Order", "kind": "int"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
]

HELP = [
    "Params by type: wall/lastcallers/file_bulletin={} "
    "ansi={\"slot\":\"welcome\"} shell={\"command\":\"...\"} "
    "door_native={\"path\":\"...\",\"args\":\"...\"} door_python={\"module\":\"...\",\"func\":\"...\"}",
]

NEW_DEFAULTS = {
    "name": "", "description": None, "event_type": "logon", "module_type": "wall",
    "params_json": "{}", "min_access_level": 0, "sort_order": 0, "is_active": True,
}

COLUMNS = [
    ("Order", 6, lambda m: m.sort_order),
    ("Name", 22, lambda m: m.name),
    ("Event", 8, lambda m: m.event_type),
    ("Type", 14, lambda m: m.module_type),
    ("Active", 6, lambda m: "Yes" if m.is_active else "No"),
]


def list_modules():
    return LoginModule.query.order_by(LoginModule.event_type, LoginModule.sort_order).all()


def values_from_module(m):
    return {f["key"]: getattr(m, f["key"]) for f in FIELDS}


def create_module(data):
    m = LoginModule(**data)
    db.session.add(m)
    db.session.commit()
    return m


def update_module(m, data):
    for k, v in data.items():
        setattr(m, k, v)
    db.session.commit()


def delete_module(m):
    db.session.delete(m)
    db.session.commit()


def reorder_module(m, direction):
    ordered = [x for x in list_modules() if x.event_type == m.event_type]
    idx = ordered.index(m)
    new_idx = idx + direction
    if 0 <= new_idx < len(ordered):
        other = ordered[new_idx]
        m.sort_order, other.sort_order = other.sort_order, m.sort_order
        db.session.commit()


def _validate(data):
    try:
        json.loads(data.get("params_json") or "{}")
    except ValueError:
        return "Params must be valid JSON."
    return None


def _add(stdscr):
    data = ui.run_form(stdscr, "New Login Module", FIELDS, dict(NEW_DEFAULTS), help_lines=HELP)
    if data is None:
        return
    if not data.get("name"):
        ui.show_message(stdscr, "Name is required.", error=True)
        return
    err = _validate(data)
    if err:
        ui.show_message(stdscr, err, error=True)
        return
    try:
        create_module(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "Could not create module.", error=True)


def _edit(stdscr, m):
    data = ui.run_form(stdscr, f"Edit Module: {m.name}", FIELDS, values_from_module(m), help_lines=HELP)
    if data is None:
        return
    err = _validate(data)
    if err:
        ui.show_message(stdscr, err, error=True)
        return
    update_module(m, data)


def _delete(stdscr, m):
    if ui.confirm(stdscr, f"Delete login module '{m.name}'?"):
        delete_module(m)


def _reorder(stdscr, m, direction):
    reorder_module(m, direction)


def run(stdscr):
    ui.run_list(
        stdscr, "Login Modules", COLUMNS, list_modules,
        on_add=_add, on_edit=_edit, on_delete=_delete, on_reorder=_reorder,
    )
