# anetbbs/web/bulletins.py
"""
Public bulletin viewer. Bulletins are stored as Message rows with the
is_pinned flag, optionally with an expires_at. Sysop creates them in
/admin/bulletins/, users browse them here.
"""
from datetime import datetime
from flask import Blueprint, render_template, abort

from ..models import db, Message

bulletins_bp = Blueprint('bulletins', __name__, url_prefix='/bulletins')


@bulletins_bp.route('/')
def index():
    """List all currently-active bulletins, pinned first."""
    now = datetime.utcnow()
    q = Message.query.filter(
        db.or_(Message.expires_at.is_(None), Message.expires_at > now)
    )
    bulletins = q.order_by(Message.is_pinned.desc(),
                           Message.created_at.desc()).all()
    return render_template('bulletins/index.html', bulletins=bulletins)


@bulletins_bp.route('/<int:bulletin_id>')
def view(bulletin_id):
    b = Message.query.get_or_404(bulletin_id)
    if b.expires_at and b.expires_at < datetime.utcnow():
        abort(404)
    return render_template('bulletins/view.html', b=b)
