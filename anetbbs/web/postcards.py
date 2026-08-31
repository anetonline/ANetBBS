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
from ..features.rate_limit import rate_limit, _user_or_ip
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
# Real gap found in a security/performance audit: unlike every other
# user-content-creation route in this app (board_post, netmail_compose,
# file_area_upload, pm's own inline PM limiter), this route had no
# rate limit at all -- any logged-in user could POST here in a tight
# loop and create an unbounded number of Postcard rows. Same threshold
# already used for the equivalent board_post/netmail_compose routes.
@rate_limit('postcard_create', limit=20, window=300, key_fn=_user_or_ip)
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
    # Real gap found in a security/performance audit: create() clamps
    # width/height to [20,100]/[5,40], but save() (an authenticated JS
    # fetch(), same route class as new.html's own editor) took the
    # client JSON's width/height completely unclamped. Confirmed live
    # (a real >60s hang reproduced against unfixed code, not just
    # reasoned through): render_ansi_text() a few lines below loops
    # width*height times SYNCHRONOUSLY on every save() call itself, no
    # need to wait for anyone to view the postcard -- an authenticated
    # user submitting one oversized save can hang the request/worker
    # directly. features/ansi_png.py's render_grid_png() -- reached by
    # the PUBLIC, no-login /postcards/<slug>.png route -- has the exact
    # same width*height loop plus an Image.new() sized width*_CELL_W by
    # height*_CELL_H, and reads width/height back OUT of the stored
    # grid_json (not the card.width/card.height columns), so an
    # unclamped save is ALSO re-triggerable by any anonymous visitor
    # afterward. Same failure class as the v1.0.54 OOM incident.
    # Clamping only card.width/card.height (a first attempt at this
    # fix) would NOT have closed either path -- neither reads those
    # columns. The clamp has to land inside `grid` itself, before it's
    # serialized or passed to render_ansi_text(), matching create()'s
    # own [20,100]/[5,40] bounds.
    try:
        grid['width'] = max(20, min(100, int(grid.get('width') or card.width)))
    except (TypeError, ValueError):
        grid['width'] = card.width
    try:
        grid['height'] = max(5, min(40, int(grid.get('height') or card.height)))
    except (TypeError, ValueError):
        grid['height'] = card.height
    card.width = grid['width']
    card.height = grid['height']
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
