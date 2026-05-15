# anetbbs/web/saved.py
"""
Saved messages — bookmark/star messages across echomail, netmail, board posts,
and PMs. Renders a unified list with links back to the source.
"""
from flask import Blueprint, request, redirect, url_for, flash, abort, render_template
from flask_login import login_required, current_user

from ..models import (db, SavedMessage, EchomailMessage, NetmailMessage,
                      Post, Message)

saved_bp = Blueprint('saved', __name__, url_prefix='/saved')


_KIND_LABELS = {
    'echomail': 'Echomail',
    'netmail': 'Netmail',
    'post': 'Board Post',
    'pm': 'Private Message',
}


def _resolve(kind, target_id):
    """Look up the source message by kind+id. Returns None if not found."""
    if kind == 'echomail':
        return EchomailMessage.query.get(target_id)
    if kind == 'netmail':
        return NetmailMessage.query.get(target_id)
    if kind == 'post':
        return Post.query.get(target_id)
    if kind == 'pm':
        return Message.query.get(target_id)
    return None


def _link_for(kind, target_id):
    """Best-effort URL back to the source view."""
    if kind == 'echomail':
        # Best-effort: the message's actual area_id isn't stored on
        # SavedMessage, so jump to the echomail index and let the user
        # navigate. (Anchor is informational — index doesn't render it.)
        return url_for('echomail.index') + f'#msg{target_id}'
    if kind == 'netmail':
        return url_for('netmail.read', msg_id=target_id)
    if kind == 'post':
        return url_for('boards.view_post', post_id=target_id)
    if kind == 'pm':
        return url_for('pm.read', message_id=target_id)
    return '#'


@saved_bp.route('/')
@login_required
def index():
    rows = (SavedMessage.query
            .filter_by(user_id=current_user.id)
            .order_by(SavedMessage.created_at.desc())
            .all())
    decorated = []
    for r in rows:
        target = _resolve(r.kind, r.target_id)
        decorated.append({
            'row': r,
            'kind_label': _KIND_LABELS.get(r.kind, r.kind),
            'subject': (getattr(target, 'subject', None)
                        or getattr(target, 'title', None)
                        or '(missing)'),
            'sender': (getattr(target, 'from_name', None)
                       or getattr(getattr(target, 'author', None), 'username', None)
                       or '?'),
            'link': _link_for(r.kind, r.target_id) if target else '#',
            'exists': target is not None,
        })
    return render_template('saved/index.html', items=decorated)


@saved_bp.route('/add', methods=['POST'])
@login_required
def add():
    kind = (request.form.get('kind') or '').strip()
    target_id = request.form.get('target_id', type=int)
    notes = (request.form.get('notes') or '').strip()
    if kind not in _KIND_LABELS or not target_id:
        flash('Invalid save target.', 'danger')
        return redirect(request.referrer or url_for('saved.index'))
    if _resolve(kind, target_id) is None:
        flash('Target not found.', 'danger')
        return redirect(request.referrer or url_for('saved.index'))
    existing = SavedMessage.query.filter_by(
        user_id=current_user.id, kind=kind, target_id=target_id).first()
    if existing:
        if notes:
            existing.notes = notes
            db.session.commit()
        flash('Already saved.', 'info')
    else:
        db.session.add(SavedMessage(
            user_id=current_user.id, kind=kind,
            target_id=target_id, notes=notes or None))
        db.session.commit()
        flash('Saved.', 'success')
    return redirect(request.referrer or url_for('saved.index'))


@saved_bp.route('/<int:saved_id>/delete', methods=['POST'])
@login_required
def delete(saved_id):
    s = SavedMessage.query.get_or_404(saved_id)
    if s.user_id != current_user.id:
        abort(403)
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for('saved.index'))
