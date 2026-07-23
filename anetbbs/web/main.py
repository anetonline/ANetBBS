# anetbbs/web/main.py
"""
Main blueprint for home page and general views
"""
from flask import Blueprint, render_template, request, abort
from flask_login import current_user
from datetime import datetime, timedelta

from ..models import (db, Board, Post, Message, UserSession, User,
                       ShoutboxPost, SysopBroadcast, EchomailMessage,
                       EchoArea, FileArea, FileUpload)
from ..features.access_control import evaluate_access

main_bp = Blueprint('main', __name__)


# Category landing pages for the navbar Tools dropdown -- replaces the
# old single flat dropdown (24 items, screenshot-reported as "so long
# it was crazy") with a short list of categories, same pattern already
# used for the Admin dropdown (see admin.py's ADMIN_HUB_SECTIONS).
# Each tool is (endpoint, title, icon, description) with two optional
# trailing entries: a dict of url_for() kwargs, and an 'auth_required'
# flag for items that were previously only shown to logged-in users
# (preserves the exact same per-item gating the old dropdown had --
# see tools_hub() below).
TOOLS_HUB_SECTIONS = {
    'community': {
        'title': 'Community',
        'icon': 'bi-people',
        'tools': [
            ("who.index", "Who's Online", 'bi-people', 'See who is connected right now', {}, True),
            ('shoutbox.index', 'Shoutbox', 'bi-megaphone', 'Quick public one-liners', {}, True),
            ('groups.index', 'Groups', 'bi-people-fill', 'User-created groups', {}, True),
            ('leaderboard.index', 'Leaderboard', 'bi-trophy-fill', 'Top scores across every game', {}, True),
            ('network_join.apply', 'Join Our Network', 'bi-diagram-3',
             'Apply to join as an echomail/QWK node', {}, True),
        ],
    },
    'directory': {
        'title': 'Network Directory',
        'icon': 'bi-globe2',
        'tools': [
            ('peers.index', 'BBS Directory', 'bi-globe2', 'Browse federated/peer BBSes', {}, True),
            ('nodelist.browse', 'Nodelist', 'bi-diagram-3', 'FidoNet/network nodelist browser', {}, False),
            ('finger.index', 'Finger', 'bi-search', 'Look up a user on this or a peer BBS', {}, True),
            ('stats.index', 'Statistics', 'bi-graph-up', 'Site activity, top users, leaderboards', {}, False),
        ],
    },
    'content': {
        'title': 'Content',
        'icon': 'bi-newspaper',
        'tools': [
            ('bulletins.index', 'Bulletins', 'bi-newspaper', 'Sysop-curated short notices', {}, True),
            ('site_pages.view', 'BBS History', 'bi-clock-history', 'The story of this BBS', {'slug': 'history'}, True),
            ('rss.index', 'RSS Reader', 'bi-rss-fill', 'Built-in feed aggregator', {}, True),
            ('gallery.index', 'Image Galleries', 'bi-images', 'Browser-native image viewer', {}, True),
            ('calendar.index', 'Calendar', 'bi-calendar3', 'Upcoming events', {}, True),
            ('polls.index', 'Polls', 'bi-bar-chart', 'Vote and see results', {}, True),
        ],
    },
    'personal': {
        'title': 'My Stuff',
        'icon': 'bi-person-workspace',
        'tools': [
            ('web_terminal.index', 'Web Terminal', 'bi-terminal-fill', 'Classic ANSI menus in your browser', {}, True),
            ('personal_pages.my_pages', 'My Web Pages', 'bi-globe2', 'User-edited homepages on the BBS', {}, True),
            ('gemini.index', 'Gemini Capsules', 'bi-stars', 'Your gemtext capsule', {}, True),
            ('saved.index', 'Saved Messages', 'bi-bookmark-star', 'Messages you bookmarked', {}, True),
        ],
    },
    'info': {
        'title': 'Info & Help',
        'icon': 'bi-info-circle',
        'tools': [
            ('main.tour', 'Tour', 'bi-compass', 'A brief walkthrough of the BBS', {}, False),
            ('docs.index', 'Documentation', 'bi-book', 'Sysop + developer documentation', {}, False),
            ('wiki.index', 'Wiki', 'bi-journal-text', 'Collaborative community wiki', {}, False),
            ('guru.index', 'Ask Anet', 'bi-question-circle', 'Search the wiki with a Q&A door', {}, False),
            ('main.about', 'About', 'bi-info-circle', 'About this BBS', {}, False),
        ],
    },
}


@main_bp.route('/tools/<section>')
def tools_hub(section):
    """Category landing page for the navbar Tools dropdown."""
    cfg = TOOLS_HUB_SECTIONS.get(section)
    if not cfg:
        abort(404)
    tools = cfg['tools']
    if not current_user.is_authenticated:
        tools = [t for t in tools if not t[5]]
    cfg = dict(cfg, tools=tools)
    return render_template('main/tools_hub.html', section=section, cfg=cfg)


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
               'echomail': [], 'shouts': [], 'files': []}

    # Every category below that can return access-gated content is
    # filtered through evaluate_access() after fetch. Confirmed pre-
    # existing gap fixed here: this route used to return sysop-only
    # boards, their posts, and access-gated echomail to any user
    # (including logged-out) -- neither branch checked min_access_level /
    # is_sysop_only / is_admin at all. Filtering post-fetch (not at the
    # SQL level) means a gated result can shrink a capped .limit(N)
    # batch below N accessible items -- acceptable for a search box,
    # not worth the extra per-category SQL-predicate complexity here.
    if q and len(q) >= 2:
        like = f'%{q}%'
        try:
            qry = (Post.query
                   .join(Board, Post.board_id == Board.id)
                   .filter((Post.subject.ilike(like)) | (Post.content.ilike(like))))
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
            posts = qry.order_by(Post.created_at.desc()).limit(40).all()
            results['posts'] = [p for p in posts if evaluate_access(
                current_user, p.board.min_access_level, bypass_admin=True)]
        except Exception:
            db.session.rollback()
        try:
            results['users'] = User.query.filter(
                User.username.ilike(like)).limit(10).all()
        except Exception:
            db.session.rollback()
        try:
            boards = Board.query.filter(
                (Board.name.ilike(like)) | (Board.description.ilike(like))
            ).filter_by(is_active=True).limit(10).all()
            results['boards'] = [b for b in boards if evaluate_access(
                current_user, b.min_access_level, bypass_admin=True)]
        except Exception:
            db.session.rollback()
        try:
            qry = (EchomailMessage.query
                   .join(EchoArea, EchomailMessage.area_id == EchoArea.id)
                   .filter((EchomailMessage.subject.ilike(like)) |
                           (EchomailMessage.body.ilike(like)) |
                           (EchomailMessage.from_name.ilike(like))))
            if df is not None:
                qry = qry.filter(EchomailMessage.created_at >= df)
            if dt_ is not None:
                qry = qry.filter(EchomailMessage.created_at < dt_)
            echomail = (qry.order_by(EchomailMessage.created_at.desc())
                       .limit(20).all())
            results['echomail'] = [m for m in echomail if evaluate_access(
                current_user, m.area.min_access_level,
                is_sysop_only=m.area.is_sysop_only, bypass_admin=True)]
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
        try:
            # Scoped to the DB-backed FileUpload gallery (anetbbs/web/files.py)
            # only -- the FTN-style file-area browser (file_areas.py) has no
            # per-file DB rows to query cheaply, see its module docstring.
            files = (FileUpload.query
                    .filter((FileUpload.filename.ilike(like)) |
                            (FileUpload.original_filename.ilike(like)) |
                            (FileUpload.description.ilike(like)))
                    .filter_by(is_public=True)
                    .order_by(FileUpload.created_at.desc())
                    .limit(20).all())

            def _file_visible(upload):
                if upload.file_area_id is None:
                    return True
                area = upload.file_area
                return area is not None and evaluate_access(
                    current_user, area.min_access_level,
                    is_sysop_only=area.is_sysop_only, bypass_admin=True)

            results['files'] = [f for f in files if _file_visible(f)]
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
