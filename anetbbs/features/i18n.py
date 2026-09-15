# anetbbs/features/i18n.py
"""Shared translation-lookup helpers, backed by the MenuTranslation
table (User.language + MenuTranslation, added in v1.0b2.68).

Originally terminal-menu-only (menu_engine.py had its own inline
query), extended here so the web UI's `t()` Jinja global (see
web_app.py's context processor) shares the same lookup-with-English-
fallback logic instead of maintaining a second copy. menu_engine.py's
_apply_menu_translations() now calls get_translations() from here too.

Both callers agree on one convention: 'en' (or no language set at
all) is the common case and does zero queries -- MenuTranslation rows
only ever need to exist for non-English overrides.
"""
from ..models import MenuTranslation


def get_translations(lang, keys):
    """Batched lookup: returns {key: text} for every MenuTranslation
    row that exists for `lang` among `keys`. A key with no row simply
    isn't in the returned dict -- callers fall back to their own
    source text, same convention MenuTranslation's own docstring
    describes. Returns {} outright for 'en'/a falsy lang/no keys,
    without touching the database. Must be called inside an app
    context."""
    if not lang or lang == 'en' or not keys:
        return {}
    rows = (MenuTranslation.query
           .filter_by(lang=lang)
           .filter(MenuTranslation.key.in_(list(keys)))
           .all())
    return {r.key: r.text for r in rows}


def get_translation(lang, key, default):
    """Single-key lookup with fallback -- for callers (the web UI's
    t() Jinja global) whose call sites are scattered across templates
    rather than naturally batchable the way one menu render's title +
    item labels are. Prefer get_translations() directly for any caller
    that already has several keys to resolve together."""
    if not lang or lang == 'en':
        return default
    return get_translations(lang, [key]).get(key, default)


# First three community-requested language packs (Jerry's call, this
# round). Covers exactly the keys wired into templates so far --
# base.html's top-level nav bar and the Message Boards page heading
# (see docs/17-development.md's "Translating a web template string"
# section for current coverage / how to extend it). A sysop can
# override or add to any of these at Admin -> Translations at any
# time; seeding never touches a row that already exists.
DEFAULT_TRANSLATIONS = {
    'es': {
        'nav.home': 'Inicio',
        'nav.messaging': 'Mensajería',
        'nav.chat': 'Chat',
        'nav.games': 'Juegos',
        'nav.files': 'Archivos',
        'nav.tools': 'Herramientas',
        'boards.heading': 'Tablones de Mensajes',
    },
    'de': {
        'nav.home': 'Startseite',
        'nav.messaging': 'Nachrichten',
        'nav.chat': 'Chat',
        'nav.games': 'Spiele',
        'nav.files': 'Dateien',
        'nav.tools': 'Werkzeuge',
        'boards.heading': 'Nachrichtenbretter',
    },
    'pt': {
        'nav.home': 'Início',
        'nav.messaging': 'Mensagens',
        'nav.chat': 'Bate-papo',
        'nav.games': 'Jogos',
        'nav.files': 'Arquivos',
        'nav.tools': 'Ferramentas',
        'boards.heading': 'Quadros de Mensagens',
    },
}


def seed_default_translations():
    """Idempotent: insert any (lang, key) pair from DEFAULT_TRANSLATIONS
    that doesn't already have a MenuTranslation row. Never overwrites an
    existing row -- including one a sysop edited away from the default
    -- same "insert only if missing" convention as every other default-
    data seeder in this codebase (achievements.ensure_seeded(),
    web_app.py's default boards/MOTD seeding it's called alongside).
    Called from web_app.py's _create_default_data() on every app start;
    a fast no-op once every pair already exists."""
    from ..models import db
    existing = {(r.lang, r.key) for r in MenuTranslation.query.all()}
    added = False
    for lang, keys in DEFAULT_TRANSLATIONS.items():
        for key, text in keys.items():
            if (lang, key) not in existing:
                db.session.add(MenuTranslation(lang=lang, key=key, text=text))
                added = True
    if added:
        db.session.commit()
