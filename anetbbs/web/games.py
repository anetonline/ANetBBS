# anetbbs/web/games.py
"""
Games blueprint — user-facing Game Center views for ANetBBS.
"""
import json
import logging
import os
from datetime import datetime

from flask import Blueprint, render_template, request, current_app, send_from_directory, abort, make_response
from flask_login import login_required, current_user
from flask_socketio import join_room, emit

from ..models import db, Game, GameSession, GameScore, GameCategory
from ..web_app import socketio

logger = logging.getLogger(__name__)

games_bp = Blueprint('games', __name__, url_prefix='/games')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _active_sessions():
    """Return query of currently active game sessions."""
    return GameSession.query.filter_by(status='active')


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@games_bp.route('/')
def lobby():
    """Game lobby — list all active games grouped by category."""
    from flask_login import current_user

    category_filter = request.args.get('category', '')
    search = request.args.get('q', '').strip()

    user_access = 0
    if current_user.is_authenticated:
        user_access = getattr(current_user, 'access_level', 10) or 10

    query = (Game.query
             .filter_by(is_active=True)
             .filter(Game.min_access_level <= user_access))
    if category_filter:
        query = query.filter_by(category=category_filter)
    if search:
        query = query.filter(Game.name.ilike(f'%{search}%'))
    games = query.order_by(Game.sort_order, Game.name).all()

    # Load categories from DB for ordering and display names
    db_cats = GameCategory.query.order_by(GameCategory.sort_order, GameCategory.name).all()
    cat_names = {c.slug: c.name for c in db_cats}
    cat_order = [c.slug for c in db_cats]

    # Group by category preserving DB order
    cat_buckets = {}
    for game in games:
        cat_buckets.setdefault(game.category or 'other', []).append(game)
    ordered_slugs = [s for s in cat_order if s in cat_buckets]
    ordered_slugs += [s for s in cat_buckets if s not in ordered_slugs]
    categories = {s: cat_buckets[s] for s in ordered_slugs}

    now_playing = _active_sessions().all()

    return render_template(
        'games/lobby.html',
        categories=categories,
        cat_names=cat_names,
        db_cats=db_cats,
        now_playing=now_playing,
        category_filter=category_filter,
        search=search,
    )


@games_bp.route('/<slug>')
def detail(slug):
    """Game detail page with leaderboard."""
    game = Game.query.filter_by(slug=slug, is_active=True).first_or_404()
    top_scores = (GameScore.query
                  .filter_by(game_id=game.id)
                  .order_by(GameScore.score.desc())
                  .limit(20).all())
    active_sessions = _active_sessions().filter_by(game_id=game.id).all()
    return render_template(
        'games/detail.html',
        game=game,
        top_scores=top_scores,
        active_sessions=active_sessions,
    )


@games_bp.route('/<slug>/play')
@login_required
def play(slug):
    """Launch a game for the current user."""
    game = Game.query.filter_by(slug=slug, is_active=True).first_or_404()

    if game.game_type == 'builtin_web':
        module = game.web_game_module or slug
        return render_template(
            'games/play_web.html',
            game=game,
            module=module,
        )

    if game.game_type == 'door_dos_browser':
        return render_template('games/play_jsdos.html', game=game)

    # Terminal (door) games
    return render_template('games/play_terminal.html', game=game)


@games_bp.route('/dos-data/<path:filename>')
@login_required
def dos_data(filename):
    """Serve game ZIP bundles for in-browser DOS games (js-dos)."""
    # Only serve plain filenames — no path traversal.
    if os.sep in filename or filename.startswith('.'):
        abort(400)
    install_root = current_app.config.get('INSTALL_DIR') or \
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dos_games_dir = os.path.join(install_root, 'data', 'dos-games')
    return send_from_directory(dos_games_dir, filename)


@games_bp.route('/dos-frame/<slug>')
@login_required
def dos_frame(slug):
    """Standalone isolated page for EmulatorJS — served with COOP/COEP headers
    so dosbox_pure can use SharedArrayBuffer without affecting the main BBS pages."""
    game = Game.query.filter_by(slug=slug, is_active=True).first_or_404()
    if game.game_type != 'door_dos_browser':
        abort(400)
    resp = make_response(render_template('games/play_jsdos_frame.html', game=game))
    resp.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    resp.headers['Cross-Origin-Embedder-Policy'] = 'credentialless'
    return resp


@games_bp.route('/<slug>/score', methods=['POST'])
@login_required
def submit_score(slug):
    """Accept a score submission from a web game (AJAX)."""
    game = Game.query.filter_by(slug=slug, is_active=True).first_or_404()
    data = request.get_json(silent=True) or {}
    score_value = int(data.get('score', 0))
    details = data.get('details', {})

    entry = GameScore(
        game_id=game.id,
        user_id=current_user.id,
        score=score_value,
        details=json.dumps(details) if details else None,
        achieved_at=datetime.utcnow(),
    )
    db.session.add(entry)
    game.play_count = (game.play_count or 0) + 1
    db.session.commit()

    return {'status': 'ok', 'score': score_value}


@games_bp.route('/scores')
def scores():
    """Global leaderboard across all games."""
    period = request.args.get('period', 'all')  # 'all' or 'monthly'
    game_id = request.args.get('game_id', type=int)

    query = GameScore.query
    if game_id:
        query = query.filter_by(game_id=game_id)
    if period == 'monthly':
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=30)
        query = query.filter(GameScore.achieved_at >= cutoff)

    top_scores = query.order_by(GameScore.score.desc()).limit(50).all()
    all_games = Game.query.filter_by(is_active=True).order_by(Game.name).all()

    return render_template(
        'games/scores.html',
        top_scores=top_scores,
        all_games=all_games,
        period=period,
        game_id=game_id,
    )


# ---------------------------------------------------------------------------
# SocketIO — namespace /game
# ---------------------------------------------------------------------------

@socketio.on('connect', namespace='/game')
def game_connect():
    if not current_user.is_authenticated:
        return False
    logger.debug('Game socket connected: user=%s', current_user.username)


@socketio.on('start_game', namespace='/game')
def handle_start_game(data):
    """Start a door game session and join the SocketIO room."""
    if not current_user.is_authenticated:
        return

    slug = data.get('game_slug', '')
    game = Game.query.filter_by(slug=slug, is_active=True).first()
    if not game or game.game_type == 'builtin_web':
        emit('game_error', {'message': 'Game not found or not a terminal game.'})
        return

    bbs_name = current_app.config.get('BBS_NAME', 'ANetBBS')

    # Closure box for the session_id — needed because launch_door_game starts
    # the PTY reader thread synchronously, which immediately calls _emit_output.
    # If we just used `session_id` from the enclosing scope, it would be
    # unbound at that moment. Box-and-set lets us assign before reads land.
    sid_box = [None]

    # Buffer for output emitted BEFORE the client has joined the socketio
    # room. Without this, the door's welcome screen lands on the wire
    # before `join_room()` runs and gets silently dropped — which is why
    # ANetSIMS / Sixel TV "showed nothing until I pressed Enter" (the
    # first user keystroke triggered new output that DID make it through
    # because by then the room was joined). Anything written before the
    # room is ready stays in this list; after we emit `game_started` we
    # flush it as a single chunk and switch to direct-emit mode.
    import threading as _thr
    _pre_join_buf = []
    _buf_lock = _thr.Lock()
    _buffering = [True]

    def _emit_output(output_bytes):
        try:
            text = output_bytes.decode('cp437', errors='replace')
        except Exception:
            logger.exception('emit_output decode failed')
            return
        with _buf_lock:
            if _buffering[0]:
                _pre_join_buf.append(text)
                return
            sid = sid_box[0]
        if sid is None:
            return
        try:
            socketio.emit('game_output', {'output': text},
                          namespace='/game', room=str(sid))
        except Exception:
            logger.exception('emit_output failed')

    def _flush_pre_join_buffer():
        """Drain anything the door wrote before the room was joined.
        Called once after join_room + game_started."""
        with _buf_lock:
            pending = ''.join(_pre_join_buf)
            _pre_join_buf.clear()
            _buffering[0] = False
        if pending and sid_box[0] is not None:
            try:
                socketio.emit('game_output', {'output': pending},
                              namespace='/game', room=str(sid_box[0]))
            except Exception:
                logger.exception('flush_pre_join_buffer emit failed')

    from ..games.door_runner import launch_door_game, launch_rlogin_session, _build_command

    # door_rlogin has no local subprocess — skip the _build_command
    # validation (it's DOS-door specific) and use the rlogin launcher.
    if game.game_type == 'door_rlogin':
        try:
            session_id = launch_rlogin_session(game, current_user,
                                                _emit_output, bbs_name)
        except Exception as exc:  # pylint: disable=broad-except
            emit('game_error', {'message': f'Cannot start: {exc}'})
            return
        if session_id is None:
            emit('game_error', {'message': 'All game nodes are currently '
                                           'in use. Please try again later.'})
            return
        sid_box[0] = session_id
        join_room(str(session_id))
        emit('game_started', {'session_id': session_id,
                              'cmd': f'rlogin {game.executable_path}'})
        _flush_pre_join_buffer()
        return

    # Validate config first — surface launch errors as a game_error event so
    # the browser shows them instead of silently doing nothing.
    try:
        cmd, cwd = _build_command(game, 1, bbs_name)
    except Exception as exc:
        emit('game_error', {'message': f'Cannot start: {exc}'})
        return

    session_id = launch_door_game(game, current_user, _emit_output, bbs_name)
    sid_box[0] = session_id

    if session_id is None:
        emit('game_error', {'message': 'All game nodes are currently in use. '
                                       'Please try again later.'})
        return

    join_room(str(session_id))
    emit('game_started', {'session_id': session_id, 'cmd': ' '.join(cmd[:5])})
    _flush_pre_join_buffer()


@socketio.on('game_input', namespace='/game')
def handle_game_input(data):
    """Forward keystroke from browser to PTY."""
    if not current_user.is_authenticated:
        return
    session_id = data.get('session_id')
    user_input = data.get('input', '')
    if session_id and user_input:
        from ..games.door_runner import send_input
        send_input(session_id, user_input)


@socketio.on('game_resize', namespace='/game')
def handle_game_resize(data):
    """Handle terminal resize event."""
    if not current_user.is_authenticated:
        return
    session_id = data.get('session_id')
    rows = data.get('rows', 24)
    cols = data.get('cols', 80)
    if session_id:
        from ..games.door_runner import resize_terminal
        resize_terminal(session_id, rows, cols)


@socketio.on('disconnect', namespace='/game')
def game_disconnect():
    """Terminate the game session on WebSocket disconnect."""
    logger.debug('Game socket disconnected')


@games_bp.route('/scoreboard')
def scoreboard():
    """Cross-game leaderboard — top scores per game."""
    from ..models import db as _db, GameScore as _GS, Game as _G, User as _U
    by_game = {}
    try:
        games = _G.query.filter_by(is_active=True).order_by(_G.name).all()
        for g in games:
            top = (_GS.query.filter_by(game_id=g.id)
                   .order_by(_GS.score.desc())
                   .limit(10).all())
            rows = []
            for s in top:
                u = _U.query.get(s.user_id)
                rows.append({
                    'username': u.username if u else '?',
                    'score': s.score, 'when': s.achieved_at,
                })
            if rows:
                by_game[g] = rows
    except Exception:
        _db.session.rollback()
    return render_template('games/scoreboard.html', by_game=by_game)
