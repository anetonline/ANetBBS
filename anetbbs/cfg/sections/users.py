"""Users & Security section (anetbbs-cfg).

Two sub-screens off one menu: Users (search-by-username, edit access
level/flags, reset password) and IP Bans (add/edit/delete). The
IpWhitelist table is rarely touched and stays web-admin-only for v1,
same "known gap" pattern as the other sections.
"""
import secrets
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, User, IpBan, WordFilter, AutoBanConfig, RegistrationAttempt

USER_FIELDS = [
    {"key": "display_name", "label": "Display Name", "kind": "text_nullable"},
    {"key": "access_level", "label": "Access Level", "kind": "int"},
    {"key": "is_admin", "label": "Admin", "kind": "bool"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
    {"key": "is_locked", "label": "Locked (sysop lock-out)", "kind": "bool"},
]

USER_HELP = [
    "Access Level convention: 10=newuser 20=verified 50=power 100=sysop.",
    "Password reset: use [R] from the list, not this form.",
]

USER_COLUMNS = [
    ("Username", 18, lambda u: u.username),
    ("Level", 6, lambda u: u.access_level),
    ("Admin", 6, lambda u: "Yes" if u.is_admin else "No"),
    ("Active", 6, lambda u: "Yes" if u.is_active else "No"),
    ("Locked", 6, lambda u: "Yes" if u.is_locked else "No"),
]

BAN_FIELDS = [
    {"key": "cidr", "label": "IP / CIDR", "kind": "text"},
    {"key": "reason", "label": "Reason", "kind": "text_nullable"},
    {"key": "expires_at", "label": "Expires (YYYY-MM-DD, blank=never)", "kind": "text_nullable"},
]

BAN_NEW_DEFAULTS = {"cidr": "", "reason": None, "expires_at": None}

BAN_COLUMNS = [
    ("IP / CIDR", 20, lambda b: b.cidr),
    ("Reason", 28, lambda b: b.reason or ""),
    ("Expires", 12, lambda b: b.expires_at.strftime("%Y-%m-%d") if b.expires_at else "never"),
]


def search_users(term, limit=50):
    q = User.query
    if term:
        q = q.filter(User.username.ilike(f"%{term}%"))
    return q.order_by(User.username).limit(limit).all()


def values_from_user(u):
    return {f["key"]: getattr(u, f["key"]) for f in USER_FIELDS}


def update_user(u, data):
    for k, v in data.items():
        setattr(u, k, v)
    db.session.commit()


def reset_password(user):
    temp = secrets.token_urlsafe(9)
    user.set_password(temp)
    db.session.commit()
    return temp


def list_bans():
    return IpBan.query.order_by(IpBan.created_at.desc()).all()


def _parse_expiry(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Invalid date '{s}' -- use YYYY-MM-DD.")


def values_from_ban(b):
    return {
        "cidr": b.cidr,
        "reason": b.reason,
        "expires_at": b.expires_at.strftime("%Y-%m-%d") if b.expires_at else None,
    }


def create_ban(data):
    b = IpBan(**data)
    db.session.add(b)
    db.session.commit()
    return b


def update_ban(b, data):
    for k, v in data.items():
        setattr(b, k, v)
    db.session.commit()


def delete_ban(b):
    db.session.delete(b)
    db.session.commit()


def _edit_user(stdscr, u):
    data = ui.run_form(stdscr, f"Edit User: {u.username}", USER_FIELDS, values_from_user(u), help_lines=USER_HELP)
    if data is None:
        return
    update_user(u, data)


def _reset_password(stdscr, u):
    if not ui.confirm(stdscr, f"Reset password for '{u.username}'?\nA random temporary password will be generated."):
        return
    temp = reset_password(u)
    ui.show_message(
        stdscr,
        f"New temporary password for {u.username}:\n\n    {temp}\n\n"
        "Give this to the user now -- it will not be shown again.",
    )


def _run_users(stdscr):
    term = [""]

    def fetch():
        return search_users(term[0])

    def _search(stdscr):
        new = ui.prompt_text(stdscr, "Search username: ", term[0])
        if new is not None:
            term[0] = new

    ui.run_list(
        stdscr, "Users" + (f" (search: {term[0]})" if term[0] else ""), USER_COLUMNS, fetch,
        on_edit=_edit_user,
        extra_actions={"/": ("Search", _search), "r": ("ResetPW", _reset_password)},
    )


def _add_ban(stdscr):
    data = ui.run_form(stdscr, "New IP Ban", BAN_FIELDS, dict(BAN_NEW_DEFAULTS))
    if data is None:
        return
    if not data.get("cidr"):
        ui.show_message(stdscr, "IP/CIDR is required.", error=True)
        return
    try:
        data["expires_at"] = _parse_expiry(data.pop("expires_at"))
    except ValueError as e:
        ui.show_message(stdscr, str(e), error=True)
        return
    try:
        create_ban(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "That IP/CIDR is already banned.", error=True)


def _edit_ban(stdscr, b):
    data = ui.run_form(stdscr, f"Edit Ban: {b.cidr}", BAN_FIELDS, values_from_ban(b))
    if data is None:
        return
    try:
        data["expires_at"] = _parse_expiry(data.pop("expires_at"))
    except ValueError as e:
        ui.show_message(stdscr, str(e), error=True)
        return
    try:
        update_ban(b, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "That IP/CIDR is already banned.", error=True)


def _delete_ban(stdscr, b):
    if ui.confirm(stdscr, f"Remove ban on '{b.cidr}'?"):
        delete_ban(b)


def _run_bans(stdscr):
    ui.run_list(
        stdscr, "IP Bans", BAN_COLUMNS, list_bans,
        on_add=_add_ban, on_edit=_edit_ban, on_delete=_delete_ban,
    )


WORD_FILTER_FIELDS = [
    {"key": "pattern", "label": "Pattern", "kind": "text"},
    {"key": "replacement", "label": "Replacement", "kind": "text"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
]

WORD_FILTER_NEW_DEFAULTS = {"pattern": "", "replacement": "****", "is_active": True}

WORD_FILTER_COLUMNS = [
    ("Pattern", 24, lambda f: f.pattern),
    ("Replacement", 16, lambda f: f.replacement or ""),
    ("Active", 6, lambda f: "Yes" if f.is_active else "No"),
]

AUTO_BAN_FIELDS = [
    {"key": "enabled", "label": "Enabled", "kind": "bool"},
    {"key": "attempt_limit", "label": "Attempt Limit", "kind": "int"},
    {"key": "window_seconds", "label": "Window (seconds)", "kind": "int"},
    {"key": "ban_duration_hours", "label": "Ban Duration (hours, 0=permanent)", "kind": "int"},
]

REG_ATTEMPT_COLUMNS = [
    ("Date", 17, lambda r: r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""),
    ("IP", 18, lambda r: r.ip_address),
    ("Username Tried", 18, lambda r: r.username_attempted or ""),
    ("OK", 4, lambda r: "Yes" if r.success else "No"),
    ("Reason", 20, lambda r: r.error_reason or ""),
]


def list_word_filters():
    return WordFilter.query.order_by(WordFilter.pattern).all()


def values_from_word_filter(f):
    return {fl["key"]: getattr(f, fl["key"]) for fl in WORD_FILTER_FIELDS}


def create_word_filter(data):
    f = WordFilter(**data)
    db.session.add(f)
    db.session.commit()
    return f


def update_word_filter(f, data):
    for k, v in data.items():
        setattr(f, k, v)
    db.session.commit()


def delete_word_filter(f):
    db.session.delete(f)
    db.session.commit()


def get_auto_ban_config():
    return AutoBanConfig.get()


def update_auto_ban_config(data):
    cfg = AutoBanConfig.get()
    for k, v in data.items():
        setattr(cfg, k, v)
    db.session.commit()
    return cfg


def list_registration_attempts(limit=200):
    return RegistrationAttempt.query.order_by(RegistrationAttempt.created_at.desc()).limit(limit).all()


def _add_word_filter(stdscr):
    from sqlalchemy.exc import IntegrityError
    data = ui.run_form(stdscr, "New Word Filter", WORD_FILTER_FIELDS, dict(WORD_FILTER_NEW_DEFAULTS))
    if data is None:
        return
    if not data.get("pattern"):
        ui.show_message(stdscr, "Pattern is required.", error=True)
        return
    try:
        create_word_filter(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "That pattern is already filtered.", error=True)


def _edit_word_filter(stdscr, f):
    data = ui.run_form(stdscr, f"Edit Filter: {f.pattern}", WORD_FILTER_FIELDS, values_from_word_filter(f))
    if data is None:
        return
    update_word_filter(f, data)


def _delete_word_filter(stdscr, f):
    if ui.confirm(stdscr, f"Delete word filter '{f.pattern}'?"):
        delete_word_filter(f)


def _run_word_filters(stdscr):
    ui.run_list(
        stdscr, "Word Filters", WORD_FILTER_COLUMNS, list_word_filters,
        on_add=_add_word_filter, on_edit=_edit_word_filter, on_delete=_delete_word_filter,
    )


def _run_auto_ban_config(stdscr):
    cfg = get_auto_ban_config()
    values = {f["key"]: getattr(cfg, f["key"]) for f in AUTO_BAN_FIELDS}
    data = ui.run_form(stdscr, "Login Auto-Ban Settings", AUTO_BAN_FIELDS, values)
    if data is None:
        return
    update_auto_ban_config(data)


def _run_registration_attempts(stdscr):
    ui.run_list(stdscr, "Registration Attempts", REG_ATTEMPT_COLUMNS, list_registration_attempts,
                empty_hint="(no registration attempts logged)")


def run(stdscr):
    items = [
        ("users", "Users"),
        ("bans", "IP Bans"),
        ("filters", "Word Filters"),
        ("autoban", "Login Auto-Ban Settings"),
        ("regattempts", "Registration Attempts"),
    ]
    while True:
        choice = ui.run_menu(stdscr, "Users & Security", items)
        if choice is None:
            return
        if choice == "users":
            _run_users(stdscr)
        elif choice == "bans":
            _run_bans(stdscr)
        elif choice == "filters":
            _run_word_filters(stdscr)
        elif choice == "autoban":
            _run_auto_ban_config(stdscr)
        elif choice == "regattempts":
            _run_registration_attempts(stdscr)
