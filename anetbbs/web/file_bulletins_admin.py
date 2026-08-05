"""Admin UI for File Bulletins (.txt/.asc/.ans dropped into
FILE_BULLETINS_DIR) -- distinct from the DB-authored Bulletins admin
(bulletins.py). Real files are auto-registered here (inactive until
enabled) every time this list is viewed, so a sysop just has to refresh
the page after dropping in (or a door game writing out) a new file.

Routes:
    GET       /admin/file-bulletins/              — list (syncs from disk first)
    GET/POST  /admin/file-bulletins/<id>/edit      — edit title/order/active/access
    POST      /admin/file-bulletins/<id>/toggle    — enable/disable
    POST      /admin/file-bulletins/<id>/delete    — remove the row (not the file)
"""
from __future__ import annotations

from flask import (Blueprint, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import login_required

from ..models import db, FileBulletin
from .access_control import require_admin as _admin_required
from ..features import file_bulletins as _fb

file_bulletins_admin_bp = Blueprint(
    'file_bulletins_admin', __name__, url_prefix='/admin/file-bulletins')


@file_bulletins_admin_bp.route('/')
@login_required
@_admin_required
def index():
    _fb.sync_bulletin_rows(current_app.config)
    base = _fb.resolve_dir(current_app.config)
    on_disk = set(_fb.scan_filenames(base)) if base else set()
    rows = FileBulletin.query.order_by(
        FileBulletin.sort_order, FileBulletin.title).all()
    return render_template('admin/file_bulletins.html',
                           rows=rows, on_disk=on_disk,
                           bulletins_dir=base or current_app.config.get('FILE_BULLETINS_DIR'))


@file_bulletins_admin_bp.route('/<int:row_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit(row_id):
    row = FileBulletin.query.get_or_404(row_id)
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        if not title:
            flash('Title is required.', 'danger')
            return redirect(url_for('.edit', row_id=row_id))
        row.title = title
        row.sort_order = int(request.form.get('sort_order') or 0)
        row.is_active = ('is_active' in request.form)
        row.min_access_level = int(request.form.get('min_access_level') or 0)
        db.session.commit()
        flash(f'"{row.title}" saved.', 'success')
        return redirect(url_for('.index'))
    base = _fb.resolve_dir(current_app.config)
    on_disk = row.filename in (set(_fb.scan_filenames(base)) if base else set())
    return render_template('admin/file_bulletin_form.html', row=row, on_disk=on_disk)


@file_bulletins_admin_bp.route('/<int:row_id>/toggle', methods=['POST'])
@login_required
@_admin_required
def toggle(row_id):
    row = FileBulletin.query.get_or_404(row_id)
    row.is_active = not row.is_active
    db.session.commit()
    state = 'enabled' if row.is_active else 'disabled'
    flash(f'"{row.title}" {state}.', 'success')
    return redirect(url_for('.index'))


@file_bulletins_admin_bp.route('/<int:row_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete(row_id):
    row = FileBulletin.query.get_or_404(row_id)
    title = row.title
    db.session.delete(row)
    db.session.commit()
    flash(f'"{title}" removed from the list (the file on disk is untouched).', 'success')
    return redirect(url_for('.index'))
