# anetbbs/web/leaderboard.py
"""Top posters / boards / reactors leaderboards."""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request

from ..models import db, User, Post, Board, PostReaction, ShoutboxPost


leader_bp = Blueprint('leaderboard', __name__, url_prefix='/leaderboard')


@leader_bp.route('/')
def index():
    period = (request.args.get('period') or 'week').lower()
    if period == 'all':
        since = None
    elif period == 'month':
        since = datetime.utcnow() - timedelta(days=30)
    else:
        period = 'week'
        since = datetime.utcnow() - timedelta(days=7)

    # Top posters
    q = (db.session.query(User.username, db.func.count(Post.id).label('n'))
         .join(Post, Post.author_id == User.id))
    if since is not None:
        q = q.filter(Post.created_at >= since)
    top_posters = (q.group_by(User.id)
                   .order_by(db.desc('n')).limit(10).all())

    # Top boards by post count in window
    q = (db.session.query(Board.name, Board.id, db.func.count(Post.id).label('n'))
         .join(Post, Post.board_id == Board.id))
    if since is not None:
        q = q.filter(Post.created_at >= since)
    top_boards = (q.group_by(Board.id)
                  .order_by(db.desc('n')).limit(10).all())

    # Top shouters
    q = (db.session.query(User.username, db.func.count(ShoutboxPost.id).label('n'))
         .join(ShoutboxPost, ShoutboxPost.user_id == User.id))
    if since is not None:
        q = q.filter(ShoutboxPost.created_at >= since)
    top_shouters = (q.group_by(User.id)
                    .order_by(db.desc('n')).limit(10).all())

    # Top reactors (count of reactions given)
    top_reactors = []
    try:
        q = (db.session.query(User.username,
                              db.func.count(PostReaction.id).label('n'))
             .join(PostReaction, PostReaction.user_id == User.id))
        if since is not None:
            q = q.filter(PostReaction.created_at >= since)
        top_reactors = (q.group_by(User.id)
                        .order_by(db.desc('n')).limit(10).all())
    except Exception:
        db.session.rollback()

    return render_template('leaderboard/index.html',
                           period=period,
                           top_posters=top_posters,
                           top_boards=top_boards,
                           top_shouters=top_shouters,
                           top_reactors=top_reactors)
