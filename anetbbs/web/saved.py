# anetbbs/web/saved.py
"""
Saved messages — bookmark/star messages across echomail, netmail, board posts,
and PMs. Renders a unified list with links back to the source.
"""
from flask import Blueprint, request, redirect, url_for, flash, abort, render_template
from flask_login import login_required, current_user

from ..models import (db, SavedMessage, EchomailMessage, NetmailMessage,
                      Post, PrivateMessage, EchoArea)

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
        # Real bug found in a security/performance audit: this looked
        # up the wrong model -- Message (bulletins) instead of the
        # actual private-message table, PrivateMessage. The "Save"
        # button on templates/pm/read.html posts a real
        # PrivateMessage.id, so this either failed to resolve (target
        # is None, "Target not found") or, worse, silently matched an
        # unrelated Message row that happened to share the same id --
        # displaying (and, before the fix below, granting access to)
        # the wrong message's data with no ownership check either way.
        return PrivateMessage.query.get(target_id)
    return None


def _can_view(kind, target):
    """Real gap found in a security/performance audit: only 'post'
    bookmarking had an access check (see the comment in add() below,
    which explicitly documented the other three kinds as NOT covered
    by that pass) -- echomail (sysop-only/min-access-gated areas) and
    netmail (private point-to-point mail) had none at all, so any user
    could permanently bookmark and view the subject+sender of a
    message they could never actually open. Mirrors the exact access
    checks web/echomail.py's own read() and web/netmail.py's own
    _user_owns() already enforce on the real message-view routes, so
    bookmarking can never see more than reading directly would."""
    if kind == 'post':
        from ..features.access_control import evaluate_access
        return evaluate_access(current_user, target.board.min_access_level)
    if kind == 'echomail':
        from ..features.access_control import evaluate_access
        from .echomail import _owns_netmail_echomail
        echo_area = EchoArea.query.get(target.area_id)
        if echo_area is None:
            return False
        if not evaluate_access(current_user, echo_area.min_access_level,
                               is_sysop_only=echo_area.is_sysop_only,
                               bypass_admin=True):
            return False
        if echo_area.tag == 'NETMAIL':
            return _owns_netmail_echomail(target, current_user)
        return True
    if kind == 'netmail':
        if current_user.is_admin:
            return True
        from .netmail import _user_owns
        return _user_owns(target)
    if kind == 'pm':
        if current_user.is_admin:
            return True
        return target.sender_id == current_user.id or target.recipient_id == current_user.id
    return False


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
                       # PrivateMessage (kind='pm') has neither of the
                       # above -- its sender is the `sender` User
                       # relationship, not `from_name`/`author`.
                       or getattr(getattr(target, 'sender', None), 'username', None)
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
    target = _resolve(kind, target_id)
    if target is None:
        flash('Target not found.', 'danger')
        return redirect(request.referrer or url_for('saved.index'))
    # Real gap found in a full message-boards security audit: bookmarking
    # a board post had no access check at all -- a below-level user
    # could save any post_id and have its subject/author permanently
    # displayed on their own /saved/ page even though visiting the real
    # thread would correctly 403. That first pass was scoped to 'post'
    # only, leaving echomail/netmail/pm with no check at all -- an IDOR
    # closed in a later audit pass via the shared _can_view() above,
    # which applies to every kind uniformly now.
    if not _can_view(kind, target):
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
