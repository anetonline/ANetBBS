# anetbbs/web/oneliners.py
"""Last-N callers + logoff one-liners — Mystic/Synchronet style."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from ..models import db, OneLiner, CallerLog


ol_bp = Blueprint('oneliners', __name__, url_prefix='/oneliners')


@ol_bp.route('/')
def index():
    rows = (OneLiner.query.filter_by(is_hidden=False)
            .order_by(OneLiner.created_at.desc()).limit(50).all())
    last_callers = (CallerLog.query
                    .order_by(CallerLog.started_at.desc()).limit(10).all())
    return render_template('oneliners/index.html', rows=rows,
                           last_callers=last_callers)


@ol_bp.route('/post', methods=['POST'])
@login_required
def post():
    text = (request.form.get('text') or '').strip()
    if not text:
        flash('Empty one-liner.', 'warning')
        return redirect(url_for('oneliners.index'))
    text = text[:120]
    try:
        from ..features import word_filter as _wf
        text = _wf.apply(text)
    except Exception:
        pass
    db.session.add(OneLiner(user_id=current_user.id, text=text))
    db.session.commit()
    flash('Posted.', 'success')
    return redirect(url_for('oneliners.index'))


@ol_bp.route('/<int:row_id>/hide', methods=['POST'])
@login_required
def hide(row_id):
    row = OneLiner.query.get_or_404(row_id)
    if row.user_id != current_user.id and not getattr(current_user,
                                                       'is_admin', False):
        abort(403)
    row.is_hidden = True
    db.session.commit()
    return redirect(url_for('oneliners.index'))
