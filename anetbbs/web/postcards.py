# anetbbs/web/postcards.py
"""
Shareable ANSI/PETSCII "postcards" -- any logged-in user composes art
with the same grid editor engine as the admin ANSI art library
(web/ansi_editor.py, templates/ansi_editor/_editor_widget.html), then
gets a public, no-login link (`/postcards/<slug>`) and a PNG they can
paste anywhere -- social media, chat, wherever a live terminal render
isn't usable.

Creating/editing a postcard requires login (any account, not just admin)
and only the creator (or an admin) may edit/delete one. Viewing a
published postcard's page, its PNG, and its raw .ans download are all
public with no login, since the entire point is a link non-BBS-users can
open directly.
"""
import json
from datetime import datetime

from flask import (Blueprint, Response, abort, flash, jsonify,
                   redirect, render_template, request, url_for)
from flask_login import current_user, login_required

from ..features.ansi_png import render_grid_png
from ..models import Postcard, db
from .ansi_editor import _slugify, render_ansi_text

postcards_bp = Blueprint('postcards', __name__, url_prefix='/postcards')


def _require_owner_or_403(postcard):
    if not current_user.is_authenticated:
        abort(403)
    if postcard.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)


@postcards_bp.route('/')
@login_required
def index():
    mine = (Postcard.query.filter_by(created_by_id=current_user.id)
            .order_by(Postcard.updated_at.desc()).all())
    return render_template('postcards/index.html', postcards=mine)


@postcards_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip() or 'Untitled Postcard'
        width = max(20, min(100, request.form.get('width', type=int) or 60))
        height = max(5, min(40, request.form.get('height', type=int) or 20))
        slug = _slugify(name) + '-' + datetime.utcnow().strftime('%H%M%S')
        grid = {'width': width, 'height': height,
                'cells': [{'c': ' ', 'fg': 15, 'bg': 1}
                          for _ in range(width * height)]}
        card = Postcard(
            name=name, slug=slug, width=width, height=height,
            grid_json=json.dumps(grid),
            ansi_text=render_ansi_text(grid),
            created_by_id=current_user.id,
        )
        db.session.add(card)
        db.session.commit()
        return redirect(url_for('postcards.edit', slug=card.slug))
    return render_template('postcards/new.html')


@postcards_bp.route('/<slug>/edit')
@login_required
def edit(slug):
    card = Postcard.query.filter_by(slug=slug).first_or_404()
    _require_owner_or_403(card)
    grid = json.loads(card.grid_json or '{}')
    if not grid:
        grid = {'width': card.width or 60, 'height': card.height or 20,
                'cells': [{'c': ' ', 'fg': 15, 'bg': 1}
                          for _ in range((card.width or 60) * (card.height or 20))]}
    return render_template('postcards/edit.html', card=card,
                           grid_json=json.dumps(grid))


@postcards_bp.route('/<slug>/save', methods=['POST'])
@login_required
def save(slug):
    card = Postcard.query.filter_by(slug=slug).first_or_404()
    _require_owner_or_403(card)
    payload = request.get_json(silent=True) or {}
    grid = payload.get('grid')
    if not grid or 'cells' not in grid:
        return jsonify({'ok': False, 'error': 'missing grid'}), 400
    card.name = payload.get('name') or card.name
    card.width = int(grid.get('width') or card.width)
    card.height = int(grid.get('height') or card.height)
    card.grid_json = json.dumps(grid)
    card.ansi_text = render_ansi_text(grid)
    card.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'updated_at': card.updated_at.isoformat()})


@postcards_bp.route('/<slug>/delete', methods=['POST'])
@login_required
def delete(slug):
    card = Postcard.query.filter_by(slug=slug).first_or_404()
    _require_owner_or_403(card)
    db.session.delete(card)
    db.session.commit()
    flash(f'Deleted "{card.name}".', 'success')
    return redirect(url_for('postcards.index'))


@postcards_bp.route('/<slug>')
def view(slug):
    """Public, no-login: the shareable page.

    Renders straight from grid_json (a plain per-cell loop, same
    approach as ansi_editor's admin preview page) rather than via
    features.ansi_html.to_html() -- that helper hardcodes an 80-column
    virtual terminal, which would silently truncate or misrender any
    postcard wider than 80 columns (postcards.create allows up to 100)."""
    card = Postcard.query.filter_by(slug=slug).first_or_404()
    grid = json.loads(card.grid_json or '{}')
    return render_template('postcards/view.html', card=card, grid=grid,
                           share_url=url_for('postcards.view', slug=slug,
                                             _external=True))


@postcards_bp.route('/<slug>.png')
def png(slug):
    """Public, no-login: PNG export for off-platform sharing."""
    card = Postcard.query.filter_by(slug=slug).first_or_404()
    grid = json.loads(card.grid_json or '{}')
    png_bytes = render_grid_png(grid, scale=2)
    resp = Response(png_bytes, mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=300'
    return resp


@postcards_bp.route('/<slug>/raw.ans')
def raw_ansi(slug):
    """Public, no-login: download the rendered .ans (Pablo/Moebius/SyncTERM-compatible)."""
    card = Postcard.query.filter_by(slug=slug).first_or_404()
    body = (card.ansi_text or '').encode('cp437', errors='replace')
    return Response(body, mimetype='application/octet-stream',
                    headers={'Content-Disposition':
                             f'attachment; filename="{card.slug}.ans"'})
