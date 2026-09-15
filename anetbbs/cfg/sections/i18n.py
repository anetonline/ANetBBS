"""Translations section (anetbbs-cfg) -- CRUD over MenuTranslation
rows, the (language, key) -> text overrides read by both the terminal
menu engine (menu_engine.py) and the web UI's t() Jinja global (see
anetbbs/features/i18n.py and docs/17-development.md's "Translating a
web template string" section). Mirrors anetbbs/web/admin.py's
translations() route -- same table, same rules, just a curses front
end instead of a browser.
"""
from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, MenuTranslation

FIELDS = [
    {"key": "lang", "label": "Language Code (e.g. es)", "kind": "text"},
    {"key": "key", "label": "Key (e.g. nav.home)", "kind": "text"},
    {"key": "text", "label": "Translated Text", "kind": "text"},
]

NEW_DEFAULTS = {"lang": "", "key": "", "text": ""}

COLUMNS = [
    ("Lang", 8, lambda r: r.lang),
    ("Key", 30, lambda r: r.key),
    ("Text", 40, lambda r: r.text),
]


def list_translations():
    return MenuTranslation.query.order_by(MenuTranslation.lang, MenuTranslation.key).all()


def values_from(r):
    return {"lang": r.lang, "key": r.key, "text": r.text}


def create_translation(data):
    row = MenuTranslation(**data)
    db.session.add(row)
    db.session.commit()
    return row


def update_translation(r, data):
    for k, v in data.items():
        setattr(r, k, v)
    db.session.commit()


def delete_translation(r):
    db.session.delete(r)
    db.session.commit()


def _add(stdscr):
    data = ui.run_form(stdscr, "New Translation", FIELDS, dict(NEW_DEFAULTS))
    if data is None:
        return
    data["lang"] = data["lang"].strip().lower()
    data["key"] = data["key"].strip()
    if not (data["lang"] and data["key"] and data["text"]):
        ui.show_message(stdscr, "Language, key, and text are all required.", error=True)
        return
    if MenuTranslation.query.filter_by(lang=data["lang"], key=data["key"]).first():
        ui.show_message(stdscr, f"A translation for {data['lang']}/{data['key']} already exists.", error=True)
        return
    create_translation(data)


def _edit(stdscr, r):
    data = ui.run_form(stdscr, f"Edit: {r.lang}/{r.key}", FIELDS, values_from(r))
    if data is None:
        return
    data["lang"] = data["lang"].strip().lower()
    data["key"] = data["key"].strip()
    try:
        update_translation(r, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, f"A translation for {data['lang']}/{data['key']} already exists.", error=True)


def _delete(stdscr, r):
    if ui.confirm(stdscr, f"Delete translation {r.lang}/{r.key!r}?"):
        delete_translation(r)


def run(stdscr):
    ui.run_list(
        stdscr, "Translations", COLUMNS, list_translations,
        on_add=_add, on_edit=_edit, on_delete=_delete,
        empty_hint="(no translation overrides yet -- English is the built-in fallback)",
    )
