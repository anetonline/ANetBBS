# anetbbs/web/permalinks.py
"""
Short permalinks for messages — `/m/<slug>` redirects to the canonical
read view of an echomail / netmail / board post / PM.

Helpers:
    slug_for(kind, target_id, user) -> str    # idempotent, returns existing or new
"""
import secrets
from flask import Blueprint, abort, redirect, url_for
from flask_login import login_required, current_user

from ..models import db, MessageSlug


m_bp = Blueprint('permalinks', __name__, url_prefix='/m')


def _gen_slug():
    """6-char base62 token. Plenty of room for any plausible message volume."""
    alpha = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    while True:
        s = ''.join(secrets.choice(alpha) for _ in range(6))
        if MessageSlug.query.filter_by(slug=s).first() is None:
            return s


def slug_for(kind, target_id, user=None):
    """Idempotent slug lookup/creation. Pass `user` to credit the creator."""
    existing = MessageSlug.query.filter_by(kind=kind, target_id=target_id).first()
    if existing:
        return existing.slug
    s = _gen_slug()
    db.session.add(MessageSlug(
        slug=s, kind=kind, target_id=target_id,
        created_by_id=getattr(user, 'id', None)))
    db.session.commit()
    return s


@m_bp.route('/<slug>')
def resolve(slug):
    row = MessageSlug.query.filter_by(slug=slug).first()
    if row is None:
        abort(404)
    if row.kind == 'echomail':
        from ..models import EchomailMessage
        m = EchomailMessage.query.get(row.target_id)
        if m is None:
            abort(404)
        return redirect(url_for('echomail.read',
                                area_id=m.area_id, message_id=m.id))
    if row.kind == 'netmail':
        return redirect(url_for('netmail.read', msg_id=row.target_id))
    if row.kind == 'post':
        return redirect(url_for('boards.view_post', post_id=row.target_id))
    if row.kind == 'pm':
        return redirect(url_for('pm.read', message_id=row.target_id))
    abort(404)


@m_bp.route('/get/<kind>/<int:target_id>')
@login_required
def get_link(kind, target_id):
    """JSON helper — returns the slug URL for this message; creates if needed."""
    s = slug_for(kind, target_id, current_user)
    return {'slug': s, 'url': url_for('permalinks.resolve', slug=s, _external=True)}
