# anetbbs/web/notifications.py
"""User notifications inbox."""
from flask import Blueprint, render_template, redirect, url_for, request, abort
from flask_login import login_required, current_user

from ..models import db, Notification


notif_bp = Blueprint('notifications', __name__, url_prefix='/notifications')


@notif_bp.route('/')
@login_required
def index():
    rows = (Notification.query.filter_by(user_id=current_user.id)
            .order_by(Notification.created_at.desc()).limit(200).all())
    return render_template('notifications/index.html', items=rows)


@notif_bp.route('/<int:nid>/read', methods=['POST'])
@login_required
def mark_read(nid):
    n = Notification.query.get_or_404(nid)
    if n.user_id != current_user.id:
        abort(403)
    n.is_read = True
    db.session.commit()
    return redirect(n.target_url or url_for('notifications.index'))


@notif_bp.route('/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    Notification.query.filter_by(
        user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return redirect(url_for('notifications.index'))


_NOTIFY_KINDS = [
    ('mention',      "@-mentions in posts/PMs/shouts"),
    ('reply',        "Replies to your posts"),
    ('pm',           "New private messages"),
    ('subscription', "New posts in subscribed boards"),
    ('achievement',  "Achievement unlocks"),
]


@notif_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Per-kind notification toggles."""
    import json as _json
    prefs = {}
    if current_user.notify_prefs:
        try:
            prefs = _json.loads(current_user.notify_prefs) or {}
        except (ValueError, TypeError):
            prefs = {}
    if request.method == 'POST':
        new_prefs = {}
        for kind, _ in _NOTIFY_KINDS:
            new_prefs[kind] = bool(request.form.get(kind))
        current_user.notify_prefs = _json.dumps(new_prefs)
        db.session.commit()
        from flask import flash
        flash('Notification preferences saved.', 'success')
        return redirect(url_for('notifications.settings'))
    return render_template('notifications/settings.html',
                           kinds=_NOTIFY_KINDS, prefs=prefs)
