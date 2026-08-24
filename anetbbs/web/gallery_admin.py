"""Admin UI for managing image galleries.

Adds / removes / edits gallery entries (label, slug, path, description,
active flag, sort order). Per-gallery: list image files, delete file,
upload new file. Storage is the JSON config file used by the public
gallery blueprint — no DB schema changes.
"""

import os
import re
from pathlib import Path
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    abort,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

from .gallery import (
    _load_config, _save_config, _list_images, _get_gallery_by_slug,
    IMAGE_EXTS, ARCHIVE_EXTS,
)
from .access_control import require_admin as _admin_required

gallery_admin_bp = Blueprint('gallery_admin', __name__,
                             url_prefix='/admin/galleries')


def _slugify(s):
    s = re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-')
    return s or 'gallery'


@gallery_admin_bp.route('/')
@login_required
@_admin_required
def index():
    galleries = _load_config()
    enriched = []
    for g in galleries:
        path = Path(g.get('path', ''))
        enriched.append({
            **g,
            'count': len(_list_images(g.get('path', ''))) if path.is_dir() else 0,
            'exists': path.is_dir(),
        })
    enriched.sort(key=lambda g: (g.get('sort_order', 999), g.get('label', '')))
    return render_template('gallery_admin/index.html', galleries=enriched)


@gallery_admin_bp.route('/add', methods=['GET', 'POST'])
@login_required
@_admin_required
def add():
    if request.method == 'POST':
        label = (request.form.get('label') or '').strip()
        slug = _slugify(request.form.get('slug') or label)
        path = (request.form.get('path') or '').strip()
        description = (request.form.get('description') or '').strip()
        is_active = bool(request.form.get('is_active'))
        try:
            sort_order = int(request.form.get('sort_order') or 100)
        except ValueError:
            sort_order = 100
        if not label or not slug or not path:
            flash('Label, slug, and path are required.', 'error')
            return redirect(url_for('gallery_admin.add'))
        galleries = _load_config()
        if any(g.get('slug') == slug for g in galleries):
            flash(f'Slug "{slug}" already exists.', 'error')
            return redirect(url_for('gallery_admin.add'))
        # Try to create the directory if it doesn't exist.
        p = Path(path)
        if not p.exists():
            try:
                p.mkdir(parents=True, exist_ok=True)
                flash(f'Created directory {path}.', 'success')
            except OSError as exc:
                flash(f'Could not create {path}: {exc}', 'error')
                return redirect(url_for('gallery_admin.add'))
        galleries.append({
            'slug': slug, 'label': label, 'path': path,
            'description': description, 'is_active': is_active,
            'sort_order': sort_order,
        })
        _save_config(galleries)
        flash(f'Gallery "{label}" added.', 'success')
        return redirect(url_for('gallery_admin.index'))
    return render_template('gallery_admin/edit.html', g=None)


@gallery_admin_bp.route('/<slug>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit(slug):
    galleries = _load_config()
    g = next((x for x in galleries if x.get('slug') == slug), None)
    if not g:
        abort(404)
    if request.method == 'POST':
        g['label'] = (request.form.get('label') or g['label']).strip()
        g['path'] = (request.form.get('path') or g['path']).strip()
        g['description'] = (request.form.get('description') or '').strip()
        g['is_active'] = bool(request.form.get('is_active'))
        try:
            g['sort_order'] = int(request.form.get('sort_order') or g.get('sort_order', 100))
        except ValueError:
            pass
        _save_config(galleries)
        flash('Gallery updated.', 'success')
        return redirect(url_for('gallery_admin.index'))
    return render_template('gallery_admin/edit.html', g=g)


@gallery_admin_bp.route('/<slug>/delete', methods=['POST'])
@login_required
@_admin_required
def delete(slug):
    galleries = _load_config()
    galleries = [g for g in galleries if g.get('slug') != slug]
    _save_config(galleries)
    flash(f'Gallery "{slug}" removed (files on disk untouched).', 'success')
    return redirect(url_for('gallery_admin.index'))


@gallery_admin_bp.route('/<slug>/files')
@login_required
@_admin_required
def files(slug):
    g = _get_gallery_by_slug(slug)
    if not g:
        abort(404)
    images = _list_images(g.get('path', ''))
    page = max(1, int(request.args.get('page', 1)))
    per_page = 60
    total_pages = max(1, (len(images) + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    return render_template(
        'gallery_admin/files.html',
        gallery=g, images=images[start:start + per_page],
        page=page, total_pages=total_pages, total_images=len(images),
    )


@gallery_admin_bp.route('/<slug>/files/<path:filename>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_file(slug, filename):
    g = _get_gallery_by_slug(slug)
    if not g:
        abort(404)
    root = Path(g.get('path', ''))
    target = (root / filename).resolve()
    if not str(target).startswith(str(root.resolve()) + os.sep):
        abort(403)
    if target.is_file():
        try:
            target.unlink()
            flash(f'Deleted {filename}.', 'success')
        except OSError as exc:
            flash(f'Delete failed: {exc}', 'error')
    else:
        flash('File not found.', 'error')
    return redirect(url_for('gallery_admin.files', slug=slug,
                            page=request.args.get('page', 1)))


@gallery_admin_bp.route('/<slug>/upload', methods=['POST'])
@login_required
@_admin_required
def upload(slug):
    g = _get_gallery_by_slug(slug)
    if not g:
        abort(404)
    root = Path(g.get('path', ''))
    if not root.is_dir():
        flash(f'Gallery directory missing: {root}', 'error')
        return redirect(url_for('gallery_admin.files', slug=slug))
    files = request.files.getlist('files')
    if not files:
        flash('No file uploaded.', 'error')
        return redirect(url_for('gallery_admin.files', slug=slug))
    saved = 0
    skipped = 0
    for f in files:
        if not f or not f.filename:
            continue
        name = secure_filename(f.filename)
        if not name:
            skipped += 1; continue
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXTS and ext not in ARCHIVE_EXTS:
            skipped += 1; continue
        dest = root / name
        # If the file already exists, append a numeric suffix.
        if dest.exists():
            stem = dest.stem; n = 1
            while dest.exists():
                dest = root / f'{stem}-{n}{ext}'; n += 1
        f.save(str(dest))
        saved += 1
    if saved:
        flash(f'Uploaded {saved} file(s).' + (f' Skipped {skipped}.' if skipped else ''),
              'success')
    else:
        flash('No valid image files in upload.' + (f' ({skipped} skipped)' if skipped else ''),
              'error')
    return redirect(url_for('gallery_admin.files', slug=slug))
