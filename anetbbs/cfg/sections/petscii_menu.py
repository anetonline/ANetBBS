"""PETSCII Menu editor section (anetbbs-cfg) -- Commodore 64/128 terminal
menus. Same two-level shape as the BBS Menu section, but a much smaller
action_type set (only what petscii_ui.py implements a plain-text
handler for) and no ansi_screen field to worry about.
"""
from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem

MENU_FIELDS = [
    {"key": "name", "label": "Name (internal key)", "kind": "text"},
    {"key": "title", "label": "Title", "kind": "text"},
    {"key": "prompt", "label": "Prompt", "kind": "text_nullable"},
    {"key": "is_default", "label": "Default Menu", "kind": "bool"},
    {"key": "min_access", "label": "Min Access Level", "kind": "int"},
]

MENU_NEW_DEFAULTS = {
    "name": "", "title": "", "prompt": "Choice: ", "is_default": False, "min_access": 0,
}

MENU_COLUMNS = [
    ("Name", 16, lambda m: m.name),
    ("Title", 24, lambda m: m.title),
    ("Default", 8, lambda m: "Yes" if m.is_default else "No"),
    ("MinAccess", 10, lambda m: m.min_access),
]

ACTION_TYPE_CHOICES = ["goto", "boards", "echo", "pm", "files", "who", "profile", "games", "logoff"]

ITEM_FIELDS = [
    {"key": "hotkey", "label": "Hotkey", "kind": "text"},
    {"key": "label", "label": "Label", "kind": "text"},
    {"key": "action_type", "label": "Action Type", "kind": "choice", "choices": ACTION_TYPE_CHOICES},
    {"key": "action_args", "label": "Action Args", "kind": "text_nullable"},
    {"key": "min_access", "label": "Min Access Level", "kind": "int"},
    {"key": "sort_order", "label": "Sort Order", "kind": "int"},
    {"key": "is_visible", "label": "Visible", "kind": "bool"},
]

ITEM_NEW_DEFAULTS = {
    "hotkey": "", "label": "", "action_type": "goto", "action_args": None,
    "min_access": 0, "sort_order": 0, "is_visible": True,
}

ITEM_COLUMNS = [
    ("Order", 6, lambda i: i.sort_order),
    ("Key", 5, lambda i: i.hotkey),
    ("Label", 22, lambda i: i.label),
    ("Action", 12, lambda i: i.action_type),
    ("Args", 16, lambda i: i.action_args or ""),
    ("Vis", 4, lambda i: "Yes" if i.is_visible else "No"),
]


def list_menus():
    return PetsciiMenu.query.order_by(PetsciiMenu.name).all()


def values_from_menu(m):
    return {f["key"]: getattr(m, f["key"]) for f in MENU_FIELDS}


def create_menu(data):
    m = PetsciiMenu(**data)
    db.session.add(m)
    db.session.commit()
    return m


def update_menu(m, data):
    for k, v in data.items():
        setattr(m, k, v)
    db.session.commit()


def delete_menu(m):
    db.session.delete(m)
    db.session.commit()


def list_items(menu):
    return PetsciiMenuItem.query.filter_by(menu_id=menu.id).order_by(PetsciiMenuItem.sort_order).all()


def values_from_item(i):
    return {f["key"]: getattr(i, f["key"]) for f in ITEM_FIELDS}


def create_item(menu, data):
    i = PetsciiMenuItem(menu_id=menu.id, **data)
    db.session.add(i)
    db.session.commit()
    return i


def update_item(i, data):
    for k, v in data.items():
        setattr(i, k, v)
    db.session.commit()


def delete_item(i):
    db.session.delete(i)
    db.session.commit()


def reorder_item(menu, i, direction):
    ordered = list_items(menu)
    idx = ordered.index(i)
    new_idx = idx + direction
    if 0 <= new_idx < len(ordered):
        other = ordered[new_idx]
        i.sort_order, other.sort_order = other.sort_order, i.sort_order
        db.session.commit()


def _add_menu(stdscr):
    data = ui.run_form(stdscr, "New PETSCII Menu", MENU_FIELDS, dict(MENU_NEW_DEFAULTS))
    if data is None:
        return
    if not data.get("name") or not data.get("title"):
        ui.show_message(stdscr, "Name and Title are required.", error=True)
        return
    try:
        create_menu(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A menu with that name already exists.", error=True)


def _edit_menu_settings(stdscr, m):
    data = ui.run_form(stdscr, f"Menu Settings: {m.name}", MENU_FIELDS, values_from_menu(m))
    if data is None:
        return
    try:
        update_menu(m, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A menu with that name already exists.", error=True)


def _delete_menu(stdscr, m):
    item_count = PetsciiMenuItem.query.filter_by(menu_id=m.id).count()
    warn = f"\nWARNING: this deletes {item_count} menu item(s) too." if item_count else ""
    if ui.confirm(stdscr, f"Delete menu '{m.name}'?{warn}"):
        delete_menu(m)


def _add_item(menu):
    def _cb(stdscr):
        data = ui.run_form(stdscr, f"New Item in {menu.name}", ITEM_FIELDS, dict(ITEM_NEW_DEFAULTS))
        if data is None:
            return
        if not data.get("hotkey") or not data.get("label"):
            ui.show_message(stdscr, "Hotkey and Label are required.", error=True)
            return
        create_item(menu, data)
    return _cb


def _edit_item(stdscr, i):
    data = ui.run_form(stdscr, f"Edit Item: {i.label}", ITEM_FIELDS, values_from_item(i))
    if data is None:
        return
    update_item(i, data)


def _delete_item(stdscr, i):
    if ui.confirm(stdscr, f"Delete menu item '{i.label}' ({i.hotkey})?"):
        delete_item(i)


def _manage_items(stdscr, m):
    def _reorder_cb(stdscr, i, direction):
        reorder_item(m, i, direction)

    ui.run_list(
        stdscr, f"PETSCII Menu Items: {m.name}", ITEM_COLUMNS, lambda: list_items(m),
        on_add=_add_item(m), on_edit=_edit_item, on_delete=_delete_item,
        on_reorder=_reorder_cb,
    )


def run(stdscr):
    ui.run_list(
        stdscr, "PETSCII Menus", MENU_COLUMNS, list_menus,
        on_add=_add_menu, on_edit=_manage_items, on_delete=_delete_menu,
        extra_actions={"s": ("Settings", _edit_menu_settings)},
    )
