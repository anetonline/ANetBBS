# anetbbs/web/stats.py
"""
Public stats dashboard — surface meaningful aggregates the BBS community
might want to see. Anonymous-friendly (no PII)."""
from datetime import datetime, timedelta
from flask import Blueprint, render_template

from ..models import (db, User, Post, Message, EchomailMessage,
                      NetmailMessage, ShoutboxPost, FileArea, CallerLog,
                      UserAchievement)


stats_bp = Blueprint('stats', __name__, url_prefix='/stats')


def _safe(fn, default=0):
    try:
        return fn()
    except Exception:
        db.session.rollback()
        return default


@stats_bp.route('/')
def index():
    now = datetime.utcnow()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)
    last_30d = now - timedelta(days=30)

    overall = {
        'users':         _safe(lambda: User.query.count()),
        'posts':         _safe(lambda: Post.query.count()),
        'pms':           _safe(lambda: Message.query.count()),
        'echomail':      _safe(lambda: EchomailMessage.query.count()),
        'netmail':       _safe(lambda: NetmailMessage.query.count()),
        'shouts':        _safe(lambda: ShoutboxPost.query.count()),
        'file_areas':    _safe(lambda: FileArea.query.count()),
        'achievements':  _safe(lambda: UserAchievement.query.count()),
    }
    last24 = {
        'logins':   _safe(lambda: CallerLog.query.filter(CallerLog.started_at >= last_24h).count()),
        'posts':    _safe(lambda: Post.query.filter(Post.created_at >= last_24h).count()),
        'echomail': _safe(lambda: EchomailMessage.query.filter(EchomailMessage.created_at >= last_24h).count()),
        'shouts':   _safe(lambda: ShoutboxPost.query.filter(ShoutboxPost.created_at >= last_24h).count()),
    }

    # Top posters (last 7 days)
    top_posters = []
    try:
        rows = (db.session.query(Post.author_id, db.func.count(Post.id))
                .filter(Post.created_at >= last_7d)
                .group_by(Post.author_id)
                .order_by(db.func.count(Post.id).desc())
                .limit(10).all())
        for uid, cnt in rows:
            u = User.query.get(uid)
            if u:
                top_posters.append({'username': u.username, 'count': cnt})
    except Exception:
        db.session.rollback()

    # Recent achievement earners
    recent_awards = []
    try:
        rows = (UserAchievement.query
                .order_by(UserAchievement.earned_at.desc())
                .limit(15).all())
        for ua in rows:
            if ua.user and ua.achievement:
                recent_awards.append({
                    'user': ua.user.username,
                    'name': ua.achievement.name,
                    'icon': ua.achievement.icon or 'patch-check',
                    'when': ua.earned_at,
                })
    except Exception:
        db.session.rollback()

    # Daily logins for the last 30 days
    daily_logins = []
    try:
        rows = (db.session.query(
                    db.func.date(CallerLog.started_at),
                    db.func.count(CallerLog.id))
                .filter(CallerLog.started_at >= last_30d)
                .group_by(db.func.date(CallerLog.started_at))
                .all())
        for day, cnt in rows:
            daily_logins.append({'day': str(day), 'count': cnt})
        daily_logins.sort(key=lambda r: r['day'])
    except Exception:
        db.session.rollback()

    return render_template('stats/index.html',
                           overall=overall, last24=last24,
                           top_posters=top_posters,
                           recent_awards=recent_awards,
                           daily_logins=daily_logins)
