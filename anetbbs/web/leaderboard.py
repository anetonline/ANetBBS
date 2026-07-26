# anetbbs/web/leaderboard.py
"""Top posters / boards / reactors leaderboards."""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request
from flask_login import current_user

from ..models import db, User, Post, Board, PostReaction, ShoutboxPost
from ..features.access_control import evaluate_access


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

    # Boards this viewer is actually allowed to see -- without this,
    # every query below leaked restricted/sysop-only board names and
    # activity (post/reaction counts) to any visitor, same bug class
    # already fixed once in boards.py's list_boards()/search_posts().
    # Real gap found in a pre-release audit.
    visible_board_ids = [b.id for b in Board.query.filter_by(is_active=True).all()
                          if evaluate_access(current_user, b.min_access_level)]

    # Top posters
    q = (db.session.query(User.username, db.func.count(Post.id).label('n'))
         .join(Post, Post.author_id == User.id)
         .filter(Post.board_id.in_(visible_board_ids)))
    if since is not None:
        q = q.filter(Post.created_at >= since)
    top_posters = (q.group_by(User.id)
                   .order_by(db.desc('n')).limit(10).all())

    # Top boards by post count in window
    q = (db.session.query(Board.name, Board.id, db.func.count(Post.id).label('n'))
         .join(Post, Post.board_id == Board.id)
         .filter(Board.id.in_(visible_board_ids)))
    if since is not None:
        q = q.filter(Post.created_at >= since)
    top_boards = (q.group_by(Board.id)
                  .order_by(db.desc('n')).limit(10).all())

    # Top shouters (shoutbox isn't board-scoped -- no access-level concept applies)
    q = (db.session.query(User.username, db.func.count(ShoutboxPost.id).label('n'))
         .join(ShoutboxPost, ShoutboxPost.user_id == User.id))
    if since is not None:
        q = q.filter(ShoutboxPost.created_at >= since)
    top_shouters = (q.group_by(User.id)
                    .order_by(db.desc('n')).limit(10).all())

    # Top reactors (count of reactions given) -- reactions are on board
    # posts, so the same board-visibility filter applies.
    top_reactors = []
    try:
        q = (db.session.query(User.username,
                              db.func.count(PostReaction.id).label('n'))
             .join(PostReaction, PostReaction.user_id == User.id)
             .join(Post, PostReaction.post_id == Post.id)
             .filter(Post.board_id.in_(visible_board_ids)))
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
