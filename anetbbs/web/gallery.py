"""Image gallery — browse photo/GIF collections natively in the browser.

Replaces DSR for users on the web. Galleries are configured in
`gallery-config.json` at the BBS install root (`current_app.root_path`'s
parent), editable via /admin/galleries/. On first run we seed the config
with the legacy DSR locations so existing data shows up automatically.
"""

import json
import mimetypes
import threading
import zipfile
from pathlib import Path
from flask import (
    Blueprint, current_app, render_template, send_from_directory,
    abort, request, Response,
)
from flask_login import login_required

gallery_bp = Blueprint('gallery', __name__, url_prefix='/gallery')

IMAGE_EXTS = {'.jpg', '.jpeg', '.gif', '.png', '.bmp', '.webp'}
# Digital Showroom-style zip galleries (the format some TIC-fed file
# areas -- e.g. a daily NASA photo feed -- already arrive in): one image
# per zip, sysop points a gallery at the same directory a file area
# already unpacks TIC into, and each zip is browsed/thumbnailed by
# extracting its one image member in memory, never to disk. A zip with
# more than one image inside just shows the first (by name) -- matches
# the one-photo-per-archive convention this feed format is built around.
ARCHIVE_EXTS = {'.zip'}
_CONFIG_LOCK = threading.Lock()

# Seeded on first run if no config file exists. Empty by default -- the
# sysop adds their own collections via /admin/galleries/; there's no
# generic sample image content bundled with the project to point at.
DEFAULT_GALLERIES = []


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
    """Gallery entries under `path` -- despite the name, this includes
    both raw image files AND .zip archives (each holding one photo,
    Digital Showroom-style); the browse grid and the /img/ route below
    both treat a zip entry exactly like an image file, just with the
    actual bytes coming from inside the archive instead of straight off
    disk. Kept as one list/one function (rather than a separate zip
    list) since both feed the exact same template loop and route today.
    """
    p = Path(path)
    if not p.is_dir():
        return []
    return sorted(
        f.name for f in p.iterdir()
        if f.is_file() and f.suffix.lower() in (IMAGE_EXTS | ARCHIVE_EXTS)
    )


def _first_image_in_zip(zip_path):
    """Return (member_name, mimetype) for the first image-looking member
    in a zip, or None if it has none / can't be opened. 'First' = sorted
    by name, skipping directory entries and macOS junk -- matches the
    one-photo-per-archive convention these TIC-fed feeds are built
    around; a zip with more than one image just shows the first."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            candidates = sorted(
                n for n in zf.namelist()
                if not n.endswith('/')
                and '__MACOSX' not in n
                and not Path(n).name.startswith('.')
                and Path(n).suffix.lower() in IMAGE_EXTS
            )
            if not candidates:
                return None
            name = candidates[0]
            mimetype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
            return name, mimetype
    except (zipfile.BadZipFile, OSError):
        return None


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
    if safe_path.suffix.lower() in ARCHIVE_EXTS:
        found = _first_image_in_zip(safe_path)
        if not found:
            abort(404)
        member_name, mimetype = found
        try:
            with zipfile.ZipFile(safe_path) as zf:
                data = zf.read(member_name)
        except (zipfile.BadZipFile, OSError, KeyError):
            abort(404)
        return Response(data, mimetype=mimetype)
    return send_from_directory(str(root), filename)
