# anetbbs/web/main.py
"""
Main blueprint for home page and general views
"""
from flask import Blueprint, render_template, request
from datetime import datetime, timedelta

from ..models import (db, Board, Post, Message, UserSession, User,
                       ShoutboxPost, SysopBroadcast, EchomailMessage,
                       FileArea)

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    """Home page"""
    # Get recent posts
    recent_posts = Post.query.order_by(Post.created_at.desc()).limit(10).all()
    
    # Get pinned messages/bulletins
    bulletins = Message.query.filter_by(is_pinned=True).order_by(Message.created_at.desc()).limit(5).all()
    
    # Get board statistics
    boards = Board.query.filter_by(is_active=True).order_by(Board.order).all()
    
    # "Active Users" = non-banned users who have logged in within the
    # last 30 days. Matches the admin dashboard's definition. Previously
    # this counted distinct local-board post authors, which had nothing
    # to do with active membership and made the homepage say "Active
    # Users: 3" when there were 6 real users.
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    stats = {
        'total_boards': Board.query.filter_by(is_active=True).count(),
        'total_posts': Post.query.count(),
        'total_users': User.query.filter_by(is_active=True)
                                 .filter(User.last_login >= thirty_days_ago)
                                 .count(),
    }

    # Get online users (last seen within 5 minutes)
    five_min_ago = datetime.utcnow() - timedelta(minutes=5)
    online_sessions = UserSession.query.filter(UserSession.last_seen >= five_min_ago).all()
    online_user_ids = [s.user_id for s in online_sessions]
    online_users = User.query.filter(User.id.in_(online_user_ids)).all() if online_user_ids else []

    # Dashboard widgets — best-effort, tolerate missing tables on fresh installs.
    def _safe(query_callable, default=None):
        try:
            return query_callable()
        except Exception:
            db.session.rollback()
            return default

    recent_shouts = _safe(lambda: (ShoutboxPost.query
                                   .filter_by(is_hidden=False)
                                   .order_by(ShoutboxPost.created_at.desc())
                                   .limit(8).all()), [])
    today = datetime.utcnow() - timedelta(hours=24)
    active_broadcasts = _safe(lambda: (SysopBroadcast.query
                                       .filter(db.or_(SysopBroadcast.expires_at.is_(None),
                                                      SysopBroadcast.expires_at > datetime.utcnow()))
                                       .order_by(SysopBroadcast.created_at.desc())
                                       .limit(3).all()), [])
    latest_echomail = _safe(lambda: (EchomailMessage.query
                                     .order_by(EchomailMessage.created_at.desc())
                                     .limit(8).all()), [])
    file_area_count = _safe(lambda: FileArea.query.filter_by(is_active=True).count(), 0)
    today_stats = {
        'posts': _safe(lambda: Post.query.filter(Post.created_at >= today).count(), 0),
        'echomail': _safe(lambda: EchomailMessage.query
                          .filter(EchomailMessage.created_at >= today).count(), 0),
        'shouts': _safe(lambda: ShoutboxPost.query
                        .filter(ShoutboxPost.created_at >= today).count(), 0),
        'new_users': _safe(lambda: User.query
                           .filter(User.created_at >= today).count(), 0),
    }
    # Active poll teaser (most recently created open poll)
    active_poll = None
    try:
        from ..models import Poll
        active_poll = (Poll.query.filter_by(is_active=True)
                       .filter(db.or_(Poll.closes_at.is_(None),
                                      Poll.closes_at > datetime.utcnow()))
                       .order_by(Poll.created_at.desc()).first())
    except Exception:
        db.session.rollback()

    # Birthdays today (matches month + day, ignoring year)
    todays_birthdays = []
    try:
        from sqlalchemy import extract
        now = datetime.utcnow()
        rows = User.query.filter(
            User.date_of_birth.isnot(None),
            extract('month', User.date_of_birth) == now.month,
            extract('day', User.date_of_birth) == now.day,
        ).all()
        todays_birthdays = rows
    except Exception:
        pass

    return render_template('main/index.html',
                         recent_posts=recent_posts,
                         bulletins=bulletins,
                         boards=boards,
                         stats=stats,
                         online_users=online_users,
                         recent_shouts=recent_shouts,
                         active_broadcasts=active_broadcasts,
                         latest_echomail=latest_echomail,
                         file_area_count=file_area_count,
                         today_stats=today_stats,
                         active_poll=active_poll,
                         todays_birthdays=todays_birthdays)


@main_bp.route('/about')
def about():
    """About page"""
    return render_template('main/about.html')


@main_bp.route('/help')
def help():
    """Help page"""
    return render_template('main/help.html')


@main_bp.route('/tour')
def tour():
    """New-user tour: a brief walkthrough of the BBS features."""
    return render_template('main/tour.html')


@main_bp.route('/tutorial')
def tutorial():
    """Bootstrap-carousel slideshow walkthrough for new sysops."""
    return render_template('main/tutorial.html')


_BOOT_AT = datetime.utcnow()


# /healthz moved to anetbbs/web/healthz.py — full body with version,
# listener probe, db status, and a proper 200/503 split. Kept the
# _BOOT_AT above in case other endpoints reference it.


@main_bp.route('/users/suggest')
def users_suggest():
    """JSON: usernames matching prefix `q`. Used for @-mention autocomplete."""
    from flask import jsonify
    q = (request.args.get('q') or '').strip()
    out = []
    if len(q) >= 1:
        try:
            rows = (User.query.filter(User.username.ilike(q + '%'))
                    .order_by(User.username).limit(8).all())
            out = [{'username': u.username} for u in rows]
        except Exception:
            db.session.rollback()
    return jsonify(out)


@main_bp.route('/search/suggest')
def search_suggest():
    """JSON suggestions for the search bar — usernames + board names."""
    from flask import jsonify
    q = (request.args.get('q') or '').strip()
    out = []
    if len(q) >= 2:
        like = f'%{q}%'
        try:
            for u in User.query.filter(User.username.ilike(like)).limit(5).all():
                out.append({'kind': 'user', 'label': u.username,
                            'url': f'/profile/{u.username}'})
        except Exception:
            db.session.rollback()
        try:
            from ..models import Board as _B
            for b in (_B.query.filter(_B.name.ilike(like))
                      .filter_by(is_active=True).limit(5).all()):
                out.append({'kind': 'board', 'label': b.name,
                            'url': f'/boards/{b.id}'})
        except Exception:
            db.session.rollback()
    return jsonify(out)


@main_bp.route('/robots.txt')
def robots_txt():
    from flask import Response, request as _r
    base = _r.host_url.rstrip('/')
    body = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Disallow: /pm/\n"
        "Disallow: /profile/edit\n"
        "Disallow: /profile/change-password\n"
        "Disallow: /blocks/\n"
        "Disallow: /notifications/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return Response(body, mimetype='text/plain')


@main_bp.route('/humans.txt')
def humans_txt():
    from flask import Response
    body = (
        "/* TEAM */\n"
        f"Sysop: {request.host}\n"
        "Built on: ANetBBS\n"
        "Stack: Python, Flask, SQLAlchemy, eventlet, asyncssh, aiohttp\n"
        "Vibe: 1990s but with HTTPS\n"
    )
    return Response(body, mimetype='text/plain')


@main_bp.route('/sitemap.xml')
def sitemap_xml():
    from flask import Response, request as _r
    from xml.sax.saxutils import escape as _xe
    from ..models import Board as _B, Post as _P
    base = _r.host_url.rstrip('/')
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    static_paths = ['/', '/about', '/help', '/tour', '/boards/',
                    '/file-areas/', '/calendar/', '/leaderboard/',
                    '/groups/', '/bulletins/']
    for p in static_paths:
        out.append(f'<url><loc>{_xe(base + p)}</loc></url>')
    try:
        for b in _B.query.filter_by(is_active=True).all():
            out.append(f'<url><loc>{_xe(base)}/boards/{b.id}</loc></url>')
    except Exception:
        db.session.rollback()
    try:
        recent = (_P.query.filter_by(parent_id=None)
                  .order_by(_P.created_at.desc()).limit(500).all())
        for p in recent:
            lm = p.updated_at or p.created_at
            stamp = lm.strftime('%Y-%m-%d') if lm else ''
            out.append(
                f'<url><loc>{_xe(base)}/boards/post/{p.id}</loc>'
                f'<lastmod>{stamp}</lastmod></url>')
    except Exception:
        db.session.rollback()
    out.append('</urlset>')
    return Response('\n'.join(out), mimetype='application/xml')


@main_bp.route('/search')
def search():
    """Site-wide search across posts, boards, users, echomail, shouts.
    Optional filters: date_from, date_to (YYYY-MM-DD), board (id),
    user (username) — restrict the post results.
    """
    q = request.args.get('q', '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    board_filter = (request.args.get('board') or '').strip()
    user_filter = (request.args.get('user') or '').strip()

    def _parse(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d')
        except (ValueError, TypeError):
            return None

    df = _parse(date_from)
    dt_ = _parse(date_to)
    if dt_ is not None:
        dt_ = dt_ + timedelta(days=1)  # inclusive

    results = {'posts': [], 'users': [], 'boards': [],
               'echomail': [], 'shouts': []}

    if q and len(q) >= 2:
        like = f'%{q}%'
        from ..models import Post, Board
        try:
            qry = Post.query.filter(
                (Post.subject.ilike(like)) | (Post.content.ilike(like)))
            if df is not None:
                qry = qry.filter(Post.created_at >= df)
            if dt_ is not None:
                qry = qry.filter(Post.created_at < dt_)
            if board_filter.isdigit():
                qry = qry.filter(Post.board_id == int(board_filter))
            if user_filter:
                u = User.query.filter(User.username.ilike(user_filter)).first()
                if u:
                    qry = qry.filter(Post.author_id == u.id)
                else:
                    qry = qry.filter(db.false())
            results['posts'] = qry.order_by(Post.created_at.desc()).limit(40).all()
        except Exception:
            db.session.rollback()
        try:
            results['users'] = User.query.filter(
                User.username.ilike(like)).limit(10).all()
        except Exception:
            db.session.rollback()
        try:
            results['boards'] = Board.query.filter(
                (Board.name.ilike(like)) | (Board.description.ilike(like))
            ).filter_by(is_active=True).limit(10).all()
        except Exception:
            db.session.rollback()
        try:
            qry = (EchomailMessage.query
                   .filter((EchomailMessage.subject.ilike(like)) |
                           (EchomailMessage.body.ilike(like)) |
                           (EchomailMessage.from_name.ilike(like))))
            if df is not None:
                qry = qry.filter(EchomailMessage.created_at >= df)
            if dt_ is not None:
                qry = qry.filter(EchomailMessage.created_at < dt_)
            results['echomail'] = (qry.order_by(EchomailMessage.created_at.desc())
                                   .limit(20).all())
        except Exception:
            db.session.rollback()
        try:
            qry = (ShoutboxPost.query
                   .filter(ShoutboxPost.text.ilike(like))
                   .filter_by(is_hidden=False))
            if df is not None:
                qry = qry.filter(ShoutboxPost.created_at >= df)
            if dt_ is not None:
                qry = qry.filter(ShoutboxPost.created_at < dt_)
            results['shouts'] = (qry.order_by(ShoutboxPost.created_at.desc())
                                 .limit(20).all())
        except Exception:
            db.session.rollback()

    boards = []
    try:
        from ..models import Board as _B
        boards = _B.query.filter_by(is_active=True).order_by(_B.name).all()
    except Exception:
        db.session.rollback()

    return render_template('main/search.html', q=q, results=results,
                           date_from=date_from, date_to=date_to,
                           board_filter=board_filter, user_filter=user_filter,
                           boards=boards)
