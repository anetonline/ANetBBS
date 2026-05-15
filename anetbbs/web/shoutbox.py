# anetbbs/web/shoutbox.py
"""
Shoutbox — short-form site-wide message strip.

Visible on the home page; one-line shouts (≤280 chars) post immediately.
Posters can delete their own shouts; admins can delete any. Hidden flag is
set when a sysop wants to suppress without losing the row for auditing."""
from datetime import datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash, abort, render_template
from flask_login import login_required, current_user

from ..models import db, ShoutboxPost


# Per-user rate limit. Sysops are exempt.
_SHOUT_LIMIT = 5            # max shouts per window
_SHOUT_WINDOW = 60          # seconds

shoutbox_bp = Blueprint('shoutbox', __name__, url_prefix='/shoutbox')


@shoutbox_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        text = (request.form.get('text') or '').strip()
        if not text:
            flash('Empty shout.', 'warning')
        elif len(text) > 280:
            flash('Shout too long (max 280 chars).', 'danger')
        elif not getattr(current_user, 'is_admin', False):
            # Per-user rate limit. Sysops are exempt because they may
            # need to broadcast multiple announcements quickly.
            window_start = datetime.utcnow() - timedelta(seconds=_SHOUT_WINDOW)
            recent = (ShoutboxPost.query
                      .filter_by(user_id=current_user.id)
                      .filter(ShoutboxPost.created_at >= window_start)
                      .count())
            if recent >= _SHOUT_LIMIT:
                flash(f'Slow down — max {_SHOUT_LIMIT} shouts per minute.', 'danger')
            else:
                _post_shout(text)
        else:
            _post_shout(text)
        return redirect(url_for('shoutbox.index'))


    posts = (ShoutboxPost.query
             .filter_by(is_hidden=False)
             .order_by(ShoutboxPost.created_at.desc())
             .limit(100).all())
    return render_template('shoutbox/index.html', posts=posts)


def _post_shout(text):
    """Common path: word-filter, save row, fire webhook, scan @mentions."""
    try:
        from ..features.word_filter import apply as _filter
        text = _filter(text)
    except Exception:
        pass
    db.session.add(ShoutboxPost(user_id=current_user.id, text=text))
    db.session.commit()
    _fire_shout(current_user.username, text)
    try:
        from ..features.notify import notify_mentions
        from flask import url_for as _u
        notify_mentions(text, current_user.username,
                        target_url=_u('shoutbox.index'))
    except Exception:
        pass
    try:
        from ..features.achievements import check_for_user
        check_for_user(current_user)
    except Exception:
        db.session.rollback()


def _fire_shout(username, text):
    try:
        from ..features.webhooks import fire
        fire('shout', {'user': username, 'text': text,
                       'content': f'{username}: {text}'})
    except Exception:
        pass


@shoutbox_bp.route('/<int:post_id>/delete', methods=['POST'])
@login_required
def delete(post_id):
    p = ShoutboxPost.query.get_or_404(post_id)
    if p.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(p)
    db.session.commit()
    return redirect(request.referrer or url_for('shoutbox.index'))


@shoutbox_bp.route('/<int:post_id>/hide', methods=['POST'])
@login_required
def hide(post_id):
    if not current_user.is_admin:
        abort(403)
    p = ShoutboxPost.query.get_or_404(post_id)
    p.is_hidden = not p.is_hidden
    db.session.commit()
    return redirect(request.referrer or url_for('shoutbox.index'))
