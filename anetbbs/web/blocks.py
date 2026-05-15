# anetbbs/web/blocks.py
"""
User block list. Each user maintains a personal block list — blocked
users cannot send them PMs, can't @-mention them, and the user's
notifications stop firing for them.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from ..models import db, User, UserBlock


blocks_bp = Blueprint('blocks', __name__, url_prefix='/blocks')


@blocks_bp.route('/')
@login_required
def index():
    rows = (UserBlock.query.filter_by(blocker_id=current_user.id)
            .order_by(UserBlock.created_at.desc()).all())
    return render_template('blocks/index.html', blocks=rows)


@blocks_bp.route('/add', methods=['POST'])
@login_required
def add():
    name = (request.form.get('username') or '').strip()
    reason = (request.form.get('reason') or '').strip() or None
    if not name:
        flash('Username required.', 'danger')
        return redirect(url_for('blocks.index'))
    target = User.query.filter(User.username.ilike(name)).first()
    if not target:
        flash('User not found.', 'danger')
        return redirect(url_for('blocks.index'))
    if target.id == current_user.id:
        flash("You can't block yourself.", 'warning')
        return redirect(url_for('blocks.index'))
    existing = UserBlock.query.filter_by(blocker_id=current_user.id,
                                         blocked_id=target.id).first()
    if existing:
        flash('Already blocked.', 'info')
        return redirect(url_for('blocks.index'))
    db.session.add(UserBlock(blocker_id=current_user.id,
                             blocked_id=target.id,
                             reason=reason))
    db.session.commit()
    flash(f'Blocked {target.username}.', 'success')
    return redirect(url_for('blocks.index'))


@blocks_bp.route('/<int:block_id>/remove', methods=['POST'])
@login_required
def remove(block_id):
    b = UserBlock.query.get_or_404(block_id)
    if b.blocker_id != current_user.id:
        abort(403)
    db.session.delete(b)
    db.session.commit()
    flash('Block removed.', 'success')
    return redirect(url_for('blocks.index'))


def is_blocked(blocker_id, blocked_id):
    """Return True if `blocker_id` has blocked `blocked_id`. Best-effort."""
    if not blocker_id or not blocked_id:
        return False
    try:
        return db.session.query(UserBlock.id).filter_by(
            blocker_id=blocker_id, blocked_id=blocked_id).first() is not None
    except Exception:
        db.session.rollback()
        return False
