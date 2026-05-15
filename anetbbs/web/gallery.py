"""Image gallery — browse photo/GIF collections natively in the browser.

Replaces DSR for users on the web. Galleries are configured in
`gallery-config.json` at the BBS install root (`current_app.root_path`'s
parent), editable via /admin/galleries/. On first run we seed the config
with the legacy DSR locations so existing data shows up automatically.
"""

import json
import threading
from pathlib import Path
from flask import (
    Blueprint, current_app, render_template, send_from_directory,
    abort, request,
)
from flask_login import login_required

gallery_bp = Blueprint('gallery', __name__, url_prefix='/gallery')

IMAGE_EXTS = {'.jpg', '.jpeg', '.gif', '.png', '.bmp', '.webp'}
_CONFIG_LOCK = threading.Lock()

# Seeded on first run if no config file exists.
DEFAULT_GALLERIES = [
    {
        'slug': 'gifs',
        'label': '90s GIFs Galore',
        'path': '/home/stingray/anetbbs/doors/sbbs/dsr/gifs',
        'description': '6,500+ classic 90s GIFs.',
        'is_active': True,
        'sort_order': 10,
    },
    {
        'slug': 'swim',
        'label': 'Sports Illustrated Swimsuit',
        'path': '/home/stingray/anetbbs/doors/sbbs/dsr/swim',
        'description': 'Sports Illustrated Swimsuit collection.',
        'is_active': True,
        'sort_order': 20,
    },
]


def _config_path():
    """Where the gallery JSON config lives (BBS install root)."""
    # current_app.root_path is .../anetbbs/anetbbs ; one level up is the
    # install dir.
    return Path(current_app.root_path).parent / 'gallery-config.json'


def _load_config():
    p = _config_path()
    with _CONFIG_LOCK:
        if not p.is_file():
            p.write_text(json.dumps(DEFAULT_GALLERIES, indent=2))
            return list(DEFAULT_GALLERIES)
        try:
            data = json.loads(p.read_text())
            return data if isinstance(data, list) else list(DEFAULT_GALLERIES)
        except Exception:
            return list(DEFAULT_GALLERIES)


def _save_config(galleries):
    p = _config_path()
    with _CONFIG_LOCK:
        p.write_text(json.dumps(galleries, indent=2))


def _get_active_galleries():
    galleries = _load_config()
    galleries = [g for g in galleries if g.get('is_active', True)]
    galleries.sort(key=lambda g: (g.get('sort_order', 999), g.get('label', '')))
    return galleries


def _get_gallery_by_slug(slug):
    for g in _load_config():
        if g.get('slug') == slug:
            return g
    return None


def _list_images(path):
    p = Path(path)
    if not p.is_dir():
        return []
    return sorted(
        f.name for f in p.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTS
    )


@gallery_bp.route('/')
@login_required
def index():
    galleries = _get_active_galleries()
    items = []
    for g in galleries:
        items.append({
            'slug': g.get('slug'),
            'label': g.get('label', g.get('slug')),
            'description': g.get('description', ''),
            'count': len(_list_images(g.get('path', ''))),
            'exists': Path(g.get('path', '')).is_dir(),
        })
    return render_template('gallery/index.html', galleries=items)


@gallery_bp.route('/<slug>/')
@login_required
def browse(slug):
    g = _get_gallery_by_slug(slug)
    if not g or not g.get('is_active', True):
        abort(404)
    images = _list_images(g.get('path', ''))
    page = max(1, int(request.args.get('page', 1)))
    per_page = 60
    total_pages = max(1, (len(images) + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    page_imgs = images[start:start + per_page]
    return render_template(
        'gallery/browse.html',
        slug=slug,
        label=g.get('label', slug),
        description=g.get('description', ''),
        images=page_imgs,
        page=page,
        total_pages=total_pages,
        total_images=len(images),
        per_page=per_page,
    )


@gallery_bp.route('/<slug>/img/<path:filename>')
@login_required
def image(slug, filename):
    g = _get_gallery_by_slug(slug)
    if not g or not g.get('is_active', True):
        abort(404)
    root = Path(g.get('path', ''))
    safe_path = (root / filename).resolve()
    try:
        if not str(safe_path).startswith(str(root.resolve())):
            abort(403)
    except FileNotFoundError:
        abort(404)
    if not safe_path.is_file():
        abort(404)
    return send_from_directory(str(root), filename)
