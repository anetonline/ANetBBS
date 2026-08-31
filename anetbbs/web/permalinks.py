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
@login_required
def resolve(slug):
    # Real gap found in a security/performance audit: this route had
    # no @login_required at all (get_link() below, which mints the
    # slug, already requires it) -- an entirely anonymous visitor with
    # a leaked/guessed slug could confirm whether a PM or netmail
    # permalink resolves to something real (a 302 redirect) versus
    # never having existed (404 right here) with zero identity or
    # rate-limit exposure. This alone doesn't close the narrower
    # same-account "exists but isn't yours" distinction (that target
    # route's own 403 is still distinguishable from a 404) -- fully
    # closing that would mean duplicating each target module's full
    # ownership logic here (netmail.py's _user_owns() also checks
    # AKAs/name-matching, not just a simple id compare), which risks
    # silent drift from the real check more than it's worth for a
    # slug space (base62^6) that isn't brute-forceable in the first
    # place. Requiring login at least attributes and rate-limits any
    # probing attempt to a real account instead of leaving it fully
    # anonymous.
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
