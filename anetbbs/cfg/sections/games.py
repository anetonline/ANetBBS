"""Games section (anetbbs-cfg) -- door games, categories, active sessions.

Mirrors anetbbs/web/games_admin.py's GameForm field set. msgbase_area_id
(InterBBS score-sharing EchoArea) is a raw nullable FK id here rather
than a grouped picker like the web admin's dropdown -- check the
Echomail section for area ids. Same "known gap" pattern as other
sections' web-admin-only fields.
"""
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, Game, GameCategory, GameSession

GAME_TYPE_CHOICES = [
    "builtin_web", "builtin_python", "door_dos_browser", "door_dos", "door_dosemu",
    "door_mystic", "door_mystic_mps", "door_native", "door_synchronet",
    "door_rlogin", "door_telnet",
]

DROP_FILE_CHOICES = ["none", "door.sys", "dorinfo", "door32.sys"]

GAME_FIELDS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "slug", "label": "Slug (URL-safe)", "kind": "text"},
    {"key": "description", "label": "Description", "kind": "text_nullable"},
    {"key": "category", "label": "Category (slug)", "kind": "text_nullable"},
    {"key": "min_access_level", "label": "Min Access Level", "kind": "int"},
    {"key": "game_type", "label": "Game Type", "kind": "choice", "choices": GAME_TYPE_CHOICES},
    {"key": "icon", "label": "Bootstrap Icon Class", "kind": "text_nullable"},
    {"key": "max_nodes", "label": "Max Simultaneous Players", "kind": "int"},
    {"key": "sort_order", "label": "Sort Order", "kind": "int"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
    {"key": "is_multiplayer", "label": "Multiplayer", "kind": "bool"},
    {"key": "web_enabled", "label": "Web Enabled", "kind": "bool"},
    {"key": "terminal_enabled", "label": "Terminal Enabled", "kind": "bool"},
    {"key": "share_scores_interbbs", "label": "Share Scores InterBBS", "kind": "bool"},
    {"key": "executable_path", "label": "Executable Path", "kind": "text_nullable"},
    {"key": "working_directory", "label": "Working Directory", "kind": "text_nullable"},
    {"key": "command_line_args", "label": "Command Line Args", "kind": "text_nullable"},
    {"key": "rlogin_bbs_tag", "label": "rlogin BBS Tag", "kind": "text_nullable"},
    {"key": "drop_file_type", "label": "Drop File Type", "kind": "choice", "choices": DROP_FILE_CHOICES},
    {"key": "drop_file_path", "label": "Drop File Path", "kind": "text_nullable"},
    {"key": "use_dosbox", "label": "Use DOSBox/dosemu2", "kind": "bool"},
    {"key": "needs_fossil_driver", "label": "Needs FOSSIL Driver", "kind": "bool"},
    {"key": "mystic_script_path", "label": "Mystic Script Path (.mpy)", "kind": "text_nullable"},
    {"key": "synchronet_script_path", "label": "Synchronet Script Path (.js)", "kind": "text_nullable"},
    {"key": "synchronet_exec_dir", "label": "Synchronet Exec Dir", "kind": "text_nullable"},
    {"key": "msgbase_area_id", "label": "InterBBS Score-Sharing Area ID", "kind": "int_nullable"},
    {"key": "web_game_module", "label": "Web Game Module Name", "kind": "text_nullable"},
    {"key": "web_game_url", "label": "Web Game URL", "kind": "text_nullable"},
]

GAME_HELP = [
    "InterBBS Score-Sharing Area ID: raw EchoArea id (see Echomail section); blank = off.",
]

GAME_NEW_DEFAULTS = {
    "name": "", "slug": "", "description": None, "category": "other",
    "min_access_level": 0, "game_type": "door_dos", "icon": None,
    "max_nodes": 1, "sort_order": 0, "is_active": True, "is_multiplayer": False,
    "web_enabled": True, "terminal_enabled": True, "share_scores_interbbs": True,
    "executable_path": None, "working_directory": None, "command_line_args": None,
    "rlogin_bbs_tag": None, "drop_file_type": "none", "drop_file_path": None,
    "use_dosbox": False, "needs_fossil_driver": False, "mystic_script_path": None,
    "synchronet_script_path": None, "synchronet_exec_dir": None,
    "msgbase_area_id": None, "web_game_module": None, "web_game_url": None,
}

GAME_COLUMNS = [
    ("Order", 6, lambda g: g.sort_order),
    ("Name", 22, lambda g: g.name),
    ("Type", 16, lambda g: g.game_type),
    ("Category", 12, lambda g: g.category or ""),
    ("Active", 6, lambda g: "Yes" if g.is_active else "No"),
]

CATEGORY_FIELDS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "slug", "label": "Slug (URL-safe)", "kind": "text"},
    {"key": "sort_order", "label": "Sort Order", "kind": "int"},
    {"key": "as_submenu", "label": "Show As Submenu Section", "kind": "bool"},
]

CATEGORY_NEW_DEFAULTS = {"name": "", "slug": "", "sort_order": 0, "as_submenu": False}

CATEGORY_COLUMNS = [
    ("Order", 6, lambda c: c.sort_order),
    ("Name", 24, lambda c: c.name),
    ("Slug", 24, lambda c: c.slug),
    ("Submenu", 8, lambda c: "Yes" if c.as_submenu else "No"),
]

SESSION_COLUMNS = [
    ("ID", 6, lambda s: s.id),
    ("Game", 20, lambda s: s.game.name if s.game else "?"),
    ("User", 16, lambda s: s.user.username if s.user else "?"),
    ("Node", 5, lambda s: s.node_number),
    ("Started", 20, lambda s: s.started_at.strftime("%Y-%m-%d %H:%M") if s.started_at else ""),
]


def list_games():
    return Game.query.order_by(Game.sort_order, Game.name).all()


def values_from_game(g):
    return {f["key"]: getattr(g, f["key"]) for f in GAME_FIELDS}


def create_game(data):
    g = Game(**data)
    db.session.add(g)
    db.session.commit()
    return g


def update_game(g, data):
    for k, v in data.items():
        setattr(g, k, v)
    db.session.commit()


def delete_game(g):
    db.session.delete(g)
    db.session.commit()


def list_categories():
    return GameCategory.query.order_by(GameCategory.sort_order, GameCategory.name).all()


def values_from_category(c):
    return {f["key"]: getattr(c, f["key"]) for f in CATEGORY_FIELDS}


def create_category(data):
    c = GameCategory(**data)
    db.session.add(c)
    db.session.commit()
    return c


def update_category(c, data):
    for k, v in data.items():
        setattr(c, k, v)
    db.session.commit()


def delete_category(c):
    db.session.delete(c)
    db.session.commit()


def list_active_sessions():
    return (GameSession.query.filter_by(status="active")
            .order_by(GameSession.started_at.desc()).all())


def disconnect_session(session_id):
    from anetbbs.games.door_runner import terminate_session
    terminate_session(session_id)
    gs = GameSession.query.get(session_id)
    if gs:
        gs.status = "completed"
        gs.ended_at = datetime.utcnow()
        db.session.commit()


def clear_stale_sessions():
    count = GameSession.query.filter_by(status="active").update(
        {"status": "stale", "ended_at": datetime.utcnow()})
    db.session.commit()
    return count


def _add_game(stdscr):
    data = ui.run_form(stdscr, "New Game", GAME_FIELDS, dict(GAME_NEW_DEFAULTS), help_lines=GAME_HELP)
    if data is None:
        return
    if not data.get("name") or not data.get("slug"):
        ui.show_message(stdscr, "Name and Slug are required.", error=True)
        return
    try:
        create_game(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A game with that slug already exists.", error=True)


def _edit_game(stdscr, g):
    data = ui.run_form(stdscr, f"Edit Game: {g.name}", GAME_FIELDS, values_from_game(g), help_lines=GAME_HELP)
    if data is None:
        return
    try:
        update_game(g, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A game with that slug already exists.", error=True)


def _delete_game(stdscr, g):
    if ui.confirm(stdscr, f"Delete game '{g.name}'?"):
        delete_game(g)


def _run_games(stdscr):
    ui.run_list(
        stdscr, "Games", GAME_COLUMNS, list_games,
        on_add=_add_game, on_edit=_edit_game, on_delete=_delete_game,
    )


def _add_category(stdscr):
    data = ui.run_form(stdscr, "New Category", CATEGORY_FIELDS, dict(CATEGORY_NEW_DEFAULTS))
    if data is None:
        return
    if not data.get("name") or not data.get("slug"):
        ui.show_message(stdscr, "Name and Slug are required.", error=True)
        return
    try:
        create_category(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A category with that slug already exists.", error=True)


def _edit_category(stdscr, c):
    data = ui.run_form(stdscr, f"Edit Category: {c.name}", CATEGORY_FIELDS, values_from_category(c))
    if data is None:
        return
    try:
        update_category(c, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A category with that slug already exists.", error=True)


def _delete_category(stdscr, c):
    if ui.confirm(stdscr, f"Delete category '{c.name}'?"):
        delete_category(c)


def _run_categories(stdscr):
    ui.run_list(
        stdscr, "Game Categories", CATEGORY_COLUMNS, list_categories,
        on_add=_add_category, on_edit=_edit_category, on_delete=_delete_category,
    )


def _disconnect(stdscr, s):
    if ui.confirm(stdscr, f"Disconnect session #{s.id} ({s.user.username if s.user else '?'})?"):
        disconnect_session(s.id)


def _clear_stale(stdscr, _row):
    if ui.confirm(stdscr, "Mark ALL active sessions as stale?"):
        count = clear_stale_sessions()
        ui.show_message(stdscr, f"Marked {count} session(s) as stale.")


def _run_sessions(stdscr):
    ui.run_list(
        stdscr, "Active Game Sessions", SESSION_COLUMNS, list_active_sessions,
        on_delete=_disconnect,
        extra_actions={"c": ("ClearStale", _clear_stale)},
        empty_hint="(no active sessions)",
    )


def run(stdscr):
    items = [("games", "Games"), ("categories", "Categories"), ("sessions", "Active Sessions")]
    while True:
        choice = ui.run_menu(stdscr, "Games", items)
        if choice is None:
            return
        if choice == "games":
            _run_games(stdscr)
        elif choice == "categories":
            _run_categories(stdscr)
        elif choice == "sessions":
            _run_sessions(stdscr)
