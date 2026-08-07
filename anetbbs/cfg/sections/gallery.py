"""Image Gallery section (anetbbs-cfg).

Galleries aren't a DB model -- they're a JSON config file
(gallery-config.json) managed by anetbbs.web.gallery's private
_load_config()/_save_config() helpers, reused here directly rather than
re-implementing the same file format/locking. Rows are plain dicts, not
ORM objects; matched by 'slug' (unique) across edit/delete calls since
each list_galleries() call re-reads the file fresh.
"""
import re
from pathlib import Path

from anetbbs.cfg import ui

FIELDS = [
    {"key": "label", "label": "Label", "kind": "text"},
    {"key": "slug", "label": "Slug (URL-safe)", "kind": "text"},
    {"key": "path", "label": "Directory Path", "kind": "text"},
    {"key": "description", "label": "Description", "kind": "text_nullable"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
    {"key": "sort_order", "label": "Sort Order", "kind": "int"},
]

NEW_DEFAULTS = {"label": "", "slug": "", "path": "", "description": "",
                "is_active": True, "sort_order": 100}

COLUMNS = [
    ("Order", 6, lambda g: g.get("sort_order", 0)),
    ("Label", 20, lambda g: g.get("label", "")),
    ("Slug", 16, lambda g: g.get("slug", "")),
    ("Path", 28, lambda g: g.get("path", "")),
    ("Active", 6, lambda g: "Yes" if g.get("is_active", True) else "No"),
]


def _slugify(s):
    s = re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-')
    return s or 'gallery'


def list_galleries():
    from anetbbs.web.gallery import _load_config
    galleries = _load_config()
    return sorted(galleries, key=lambda g: (g.get("sort_order", 999), g.get("label", "")))


def values_from_gallery(g):
    return {f["key"]: g.get(f["key"]) for f in FIELDS}


def create_gallery(data):
    from anetbbs.web.gallery import _load_config, _save_config
    galleries = _load_config()
    slug = _slugify(data.get("slug") or data.get("label"))
    if any(g.get("slug") == slug for g in galleries):
        raise ValueError(f"Slug '{slug}' already exists.")
    entry = dict(data)
    entry["slug"] = slug
    galleries.append(entry)
    _save_config(galleries)
    if entry.get("path"):
        p = Path(entry["path"])
        if not p.exists():
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
    return entry


def update_gallery(slug, data):
    from anetbbs.web.gallery import _load_config, _save_config
    galleries = _load_config()
    for g in galleries:
        if g.get("slug") == slug:
            g.update(data)
            break
    _save_config(galleries)


def delete_gallery(slug):
    from anetbbs.web.gallery import _load_config, _save_config
    galleries = [g for g in _load_config() if g.get("slug") != slug]
    _save_config(galleries)


def _add(stdscr):
    data = ui.run_form(stdscr, "New Gallery", FIELDS, dict(NEW_DEFAULTS))
    if data is None:
        return
    if not data.get("label") or not data.get("path"):
        ui.show_message(stdscr, "Label and Path are required.", error=True)
        return
    try:
        create_gallery(data)
    except ValueError as e:
        ui.show_message(stdscr, str(e), error=True)


def _edit(stdscr, g):
    data = ui.run_form(stdscr, f"Edit Gallery: {g.get('label')}", FIELDS, values_from_gallery(g))
    if data is None:
        return
    update_gallery(g.get("slug"), data)


def _delete(stdscr, g):
    if ui.confirm(stdscr, f"Delete gallery '{g.get('label')}'?\n(images on disk are not deleted)"):
        delete_gallery(g.get("slug"))


def run(stdscr):
    ui.run_list(
        stdscr, "Image Galleries", COLUMNS, list_galleries,
        on_add=_add, on_edit=_edit, on_delete=_delete,
    )
