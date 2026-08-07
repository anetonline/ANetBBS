"""Boards & Message Areas section (anetbbs-cfg).

Data-access helpers below (list_boards/create_board/update_board/
delete_board/reorder_board) are plain functions with no curses dependency
-- they're unit-tested directly in tests/test_cfg_sections_data.py. The
``_add``/``_edit``/``_delete``/``_reorder``/``run`` functions are the thin
curses-driving layer on top.
"""
from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, Board

FIELDS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "description", "label": "Description", "kind": "text_nullable"},
    {"key": "category", "label": "Category", "kind": "text_nullable"},
    {"key": "order", "label": "Sort Order", "kind": "int"},
    {"key": "min_access_level", "label": "Min Read Level", "kind": "int"},
    {"key": "min_write_level", "label": "Min Write Level", "kind": "int_nullable"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
]

HELP = [
    "Min Write Level blank = same as Min Read Level.",
    "ANSI banner editing: web admin only (Admin -> Boards).",
]

COLUMNS = [
    ("Order", 6, lambda b: b.order),
    ("Name", 24, lambda b: b.name),
    ("Category", 16, lambda b: b.category or ""),
    ("MinLvl", 7, lambda b: b.min_access_level),
    ("Active", 6, lambda b: "Yes" if b.is_active else "No"),
]

NEW_DEFAULTS = {
    "name": "", "description": None, "category": None,
    "order": 0, "min_access_level": 10, "min_write_level": None,
    "is_active": True,
}


def list_boards():
    return Board.query.order_by(Board.order, Board.name).all()


def values_from_board(b):
    return {f["key"]: getattr(b, f["key"]) for f in FIELDS}


def create_board(data):
    b = Board(**data)
    db.session.add(b)
    db.session.commit()
    return b


def update_board(b, data):
    for k, v in data.items():
        setattr(b, k, v)
    db.session.commit()


def delete_board(b):
    db.session.delete(b)
    db.session.commit()


def reorder_board(b, direction):
    """direction: -1 moves up (toward the top), +1 moves down."""
    ordered = list_boards()
    idx = ordered.index(b)
    new_idx = idx + direction
    if 0 <= new_idx < len(ordered):
        other = ordered[new_idx]
        b.order, other.order = other.order, b.order
        db.session.commit()


def _add(stdscr):
    data = ui.run_form(stdscr, "New Board", FIELDS, dict(NEW_DEFAULTS), help_lines=HELP)
    if data is None:
        return
    if not data.get("name"):
        ui.show_message(stdscr, "Name is required.", error=True)
        return
    try:
        create_board(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A board with that name already exists.", error=True)


def _edit(stdscr, b):
    data = ui.run_form(stdscr, f"Edit Board: {b.name}", FIELDS, values_from_board(b), help_lines=HELP)
    if data is None:
        return
    try:
        update_board(b, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A board with that name already exists.", error=True)


def _delete(stdscr, b):
    post_count = b.posts.count()
    warn = f"\nWARNING: this board has {post_count} post(s) which will also be deleted." if post_count else ""
    if ui.confirm(stdscr, f"Delete board '{b.name}'?{warn}"):
        delete_board(b)


def _reorder(stdscr, b, direction):
    reorder_board(b, direction)


def run(stdscr):
    ui.run_list(
        stdscr, "Boards & Message Areas", COLUMNS, list_boards,
        on_add=_add, on_edit=_edit, on_delete=_delete, on_reorder=_reorder,
    )
