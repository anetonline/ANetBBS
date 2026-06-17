"""Admin UI for the Graffiti Wall.

Routes:
    GET   /admin/wall/                  — list posts (paginated)
    POST  /admin/wall/<id>/delete       — soft-delete a post
    POST  /admin/wall/clear             — soft-delete ALL posts
"""
from __future__ import annotations

import os

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from ..models import db, WallPost
from ..features.wall import WALL_SCHEMES, WALL_SCHEME_LABELS

wall_admin_bp = Blueprint('wall_admin', __name__, url_prefix='/admin/wall')

PER_PAGE = 30


def _admin_required():
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        abort(403)


def _env_path():
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', '.env'))


@wall_admin_bp.route('/')
@login_required
def index():
    _admin_required()
    page = request.args.get('page', 1, type=int)
    show_deleted = request.args.get('deleted', '0') == '1'
    q = WallPost.query
    if not show_deleted:
        q = q.filter_by(is_deleted=False)
    pagination = q.order_by(WallPost.created_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False)
    current_scheme = current_app.config.get('WALL_COLOR_SCHEME', 'cyan')
    return render_template('admin/wall.html',
                           pagination=pagination,
                           show_deleted=show_deleted,
                           current_scheme=current_scheme,
                           schemes=WALL_SCHEME_LABELS)


@wall_admin_bp.route('/settings', methods=['POST'])
@login_required
def settings():
    _admin_required()
    scheme = request.form.get('color_scheme', 'cyan')
    if scheme not in WALL_SCHEMES:
        scheme = 'cyan'
    current_app.config['WALL_COLOR_SCHEME'] = scheme
    try:
        env_path = _env_path()
        lines = []
        found = False
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    if line.startswith('WALL_COLOR_SCHEME='):
                        lines.append(f'WALL_COLOR_SCHEME={scheme}\n')
                        found = True
                    else:
                        lines.append(line)
        if not found:
            lines.append(f'WALL_COLOR_SCHEME={scheme}\n')
        with open(env_path, 'w') as f:
            f.writelines(lines)
    except Exception as e:
        flash(f'Color updated in memory but could not save to .env: {e}', 'warning')
        return redirect(url_for('.index'))
    flash(f'Wall color scheme set to "{WALL_SCHEME_LABELS.get(scheme, scheme)}".', 'success')
    return redirect(url_for('.index'))


@wall_admin_bp.route('/<int:post_id>/delete', methods=['POST'])
@login_required
def delete(post_id):
    _admin_required()
    post = WallPost.query.get_or_404(post_id)
    post.is_deleted = True
    db.session.commit()
    flash(f'Wall post #{post_id} deleted.', 'success')
    return redirect(url_for('.index'))


@wall_admin_bp.route('/restore/<int:post_id>', methods=['POST'])
@login_required
def restore(post_id):
    _admin_required()
    post = WallPost.query.get_or_404(post_id)
    post.is_deleted = False
    db.session.commit()
    flash(f'Wall post #{post_id} restored.', 'success')
    return redirect(url_for('.index', deleted=1))


@wall_admin_bp.route('/clear', methods=['POST'])
@login_required
def clear_all():
    _admin_required()
    count = WallPost.query.filter_by(is_deleted=False).update({'is_deleted': True})
    db.session.commit()
    flash(f'Cleared {count} wall posts.', 'warning')
    return redirect(url_for('.index'))
