# anetbbs/web/gemini.py
"""
Per-user Gemini capsule editor + browser-friendly viewer.

We don't run a real Gemini-protocol server here (TLS+1965); we just expose
the gemtext content over HTTP at /gemini/<username> for now. A real Gemini
listener would call into this same content."""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, Response
from flask_login import login_required, current_user

from ..models import db, GeminiCapsule, User


gemini_bp = Blueprint('gemini', __name__, url_prefix='/gemini')


_GEMINI_DEFAULT = """\
# {username}'s capsule

Welcome to my little corner of the BBS. Edit me at /gemini/edit.

=> https://gemini.circumlunar.space/  Project Gemini

## Recent thoughts

* (write something here)
"""


@gemini_bp.route('/')
def index():
    """List all published capsules."""
    rows = (GeminiCapsule.query.filter_by(is_published=True)
            .order_by(GeminiCapsule.updated_at.desc())
            .limit(100).all())
    return render_template('gemini/index.html', capsules=rows)


@gemini_bp.route('/<username>')
def view(username):
    """View one user's capsule — rendered as gemtext-light HTML."""
    user = User.query.filter_by(username=username).first_or_404()
    cap = GeminiCapsule.query.filter_by(user_id=user.id).first()
    if cap is None or not cap.is_published:
        abort(404)
    return render_template('gemini/view.html', capsule=cap, user=user)


@gemini_bp.route('/<username>.gmi')
def raw_gemtext(username):
    """Serve the capsule as raw gemtext (text/gemini)."""
    user = User.query.filter_by(username=username).first_or_404()
    cap = GeminiCapsule.query.filter_by(user_id=user.id).first()
    if cap is None or not cap.is_published:
        abort(404)
    return Response(cap.content or '', mimetype='text/gemini; charset=utf-8')


@gemini_bp.route('/edit', methods=['GET', 'POST'])
@login_required
def edit():
    """Each user manages their own capsule."""
    cap = GeminiCapsule.query.filter_by(user_id=current_user.id).first()
    if cap is None:
        cap = GeminiCapsule(
            user_id=current_user.id,
            title=f'{current_user.username}\'s capsule',
            content=_GEMINI_DEFAULT.format(username=current_user.username),
            is_published=False)
        db.session.add(cap)
        db.session.commit()

    if request.method == 'POST':
        cap.title = (request.form.get('title') or cap.title).strip()
        cap.content = request.form.get('content') or ''
        cap.is_published = bool(request.form.get('is_published'))
        cap.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Capsule saved.', 'success')
        if cap.is_published:
            return redirect(url_for('gemini.view', username=current_user.username))
    return render_template('gemini/edit.html', capsule=cap)
