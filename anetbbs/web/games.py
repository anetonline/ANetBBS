# anetbbs/web/games.py
"""
Games blueprint — user-facing Game Center views for ANetBBS.
"""
import json
import logging
import os
import queue
from datetime import datetime

from flask import Blueprint, render_template, request, current_app, send_from_directory, abort, make_response, jsonify
from flask_login import login_required, current_user
from flask_socketio import join_room, emit

from ..models import db, Game, GameSession, GameScore, GameCategory, WebGameWallet
from ..web_app import socketio

logger = logging.getLogger(__name__)

games_bp = Blueprint('games', __name__, url_prefix='/games')

# Maps SocketIO socket-id → GameSession.id so disconnect can terminate the right session
_socket_to_session: dict = {}


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

    # Filter mirrors evaluate_access(current_user, game.min_access_level,
    # bypass_admin=False) -- kept as a SQL predicate, not a per-item
    # Python call (bulk listing query). bypass_admin=False preserves
    # existing behavior: this route has no @login_required, and an
    # anonymous visitor here defaults to access_level 0 (not
    # evaluate_access()'s own default of 10 for a None user) -- a real,
    # deliberate difference from echomail.py's convention, not something
    # this migration should silently unify. Game has no is_sysop_only
    # column to gate on separately.
    user_access = 0
    if current_user.is_authenticated:
        user_access = getattr(current_user, 'access_level', 10) or 10

    query = (Game.query
             .filter_by(is_active=True, web_enabled=True)
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
    game = Game.query.filter_by(slug=slug, is_active=True, web_enabled=True).first_or_404()
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
        is_casino=game.slug in CASINO_SLUGS,
    )


@games_bp.route('/<slug>/play')
@login_required
def play(slug):
    """Launch a game for the current user."""
    game = Game.query.filter_by(slug=slug, is_active=True, web_enabled=True).first_or_404()

    if game.game_type == 'builtin_web':
        module = game.web_game_module or slug
        return render_template(
            'games/play_web.html',
            game=game,
            module=module,
        )

    if game.game_type == 'door_dos_browser':
        return render_template('games/play_jsdos.html', game=game)

    if game.game_type == 'builtin_python':
        return render_template('games/play_terminal.html', game=game)

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

    try:
        from ..echomail.interbbs_sync import post_score_to_interbbs
        post_score_to_interbbs(entry)
    except Exception:
        logger.exception('post_score_to_interbbs failed for score %s', entry.id)

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
        casino_slugs=CASINO_SLUGS,
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

    # PTY output previously called socketio.emit() directly from
    # _pty_reader's own thread (anetbbs/games/door_runner.py). Under
    # eventlet.monkey_patch() that thread already runs cooperatively,
    # so this wasn't as unsafe as a raw-OS-thread emit would be on a
    # non-monkey-patched app -- but it's still not the documented
    # Flask-SocketIO pattern for emitting from outside a request/socket
    # handler context. Marshal every emit through a thread-safe queue,
    # drained by a proper socketio background task, instead -- purely
    # additive, same order, same content, only the call stack changes.
    out_q = queue.Queue()

    def _emit_output(output_bytes):
        try:
            text = output_bytes.decode('cp437', errors='replace')
        except Exception:
            logger.exception('emit_output decode failed')
            return
        # Diagnostic only -- fires solely on sixel/DCS-shaped chunks, so
        # this is silent for every normal text-output door. Distinguishes
        # 7-bit DCS framing (ESC P ... ESC \) from 8-bit C1 framing
        # (\x90 ... \x9c) -- cp437 decode above does NOT preserve 8-bit
        # C1 control-code semantics (it remaps \x90/\x9c to printable
        # Latin letters), which would silently corrupt 8-bit-framed
        # sixel data before it ever reaches the browser. Needed to
        # confirm/refute this against a real sixel-capable door (e.g.
        # DSR) -- can't be reproduced in a sandbox with no real
        # PTY+Node+ImageMagick+browser chain available.
        if b'\x1bP' in output_bytes or b'\x90' in output_bytes:
            logger.debug('door output contains a possible sixel/DCS '
                        'introducer: %r', output_bytes[:80])
        with _buf_lock:
            if _buffering[0]:
                _pre_join_buf.append(text)
                return
            sid = sid_box[0]
        if sid is None:
            return
        out_q.put((sid, text))

    def _flush_pre_join_buffer():
        """Drain anything the door wrote before the room was joined.
        Called once after join_room + game_started."""
        with _buf_lock:
            pending = ''.join(_pre_join_buf)
            _pre_join_buf.clear()
            _buffering[0] = False
        if pending and sid_box[0] is not None:
            out_q.put((sid_box[0], pending))

    def _drain_queue():
        """Background task (real eventlet greenthread via
        socketio.start_background_task) that owns every socketio.emit()
        call for this game session -- _emit_output/_flush_pre_join_buffer
        only ever push onto out_q, never emit directly. Exits once the
        session is no longer active and the queue has drained, so this
        doesn't leak a greenthread per session forever."""
        idle_checks = 0
        while True:
            try:
                item = out_q.get(timeout=5)
            except queue.Empty:
                idle_checks += 1
                sid = sid_box[0]
                if sid is not None:
                    try:
                        gs = GameSession.query.get(sid)
                        if gs is None or gs.status != 'active':
                            break
                    except Exception:
                        logger.exception('drain_queue: session status check failed')
                        break
                # No session yet (still launching) -- keep waiting, but
                # don't spin forever if something went very wrong.
                elif idle_checks > 12:  # ~60s with no session ever assigned
                    break
                continue
            idle_checks = 0
            sid, text = item
            try:
                socketio.emit('game_output', {'output': text},
                              namespace='/game', room=str(sid))
            except Exception:
                logger.exception('drain_queue emit failed')

    socketio.start_background_task(_drain_queue)

    from ..games.door_runner import (launch_door_game, launch_rlogin_session,
                                     launch_telnet_session, _build_command)

    # door_rlogin / door_telnet have no local subprocess — skip the
    # _build_command validation (it's DOS-door specific) and use the
    # matching remote launcher.
    if game.game_type in ('door_rlogin', 'door_telnet'):
        launcher = (launch_rlogin_session if game.game_type == 'door_rlogin'
                   else launch_telnet_session)
        proto = 'rlogin' if game.game_type == 'door_rlogin' else 'telnet'
        try:
            session_id = launcher(game, current_user, _emit_output, bbs_name)
        except Exception as exc:  # pylint: disable=broad-except
            emit('game_error', {'message': f'Cannot start: {exc}'})
            return
        if session_id is None:
            emit('game_error', {'message': 'All game nodes are currently '
                                           'in use. Please try again later.'})
            return
        sid_box[0] = session_id
        join_room(str(session_id))
        _socket_to_session[request.sid] = session_id
        emit('game_started', {'session_id': session_id,
                              'cmd': f'{proto} {game.executable_path}'})
        _flush_pre_join_buffer()
        return

    # Validate config first — surface launch errors as a game_error event so
    # the browser shows them instead of silently doing nothing.
    try:
        cmd, cwd = _build_command(game, 1, bbs_name)
    except Exception as exc:
        import traceback as _tb
        logger.error('_build_command failed for game %s (type=%s): %s\n%s',
                     getattr(game, 'slug', '?'),
                     getattr(game, 'game_type', '?'),
                     exc, _tb.format_exc())
        emit('game_error', {'message': f'Cannot start: {exc}'})
        return

    session_id = launch_door_game(game, current_user, _emit_output, bbs_name)
    sid_box[0] = session_id

    if session_id is None:
        emit('game_error', {'message': 'All game nodes are currently in use. '
                                       'Please try again later.'})
        return

    join_room(str(session_id))
    _socket_to_session[request.sid] = session_id
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
    session_id = _socket_to_session.pop(request.sid, None)
    if session_id:
        logger.debug('Game socket disconnected: sid=%s session=%s — terminating', request.sid, session_id)
        try:
            from ..games.door_runner import terminate_session
            terminate_session(session_id)
        except Exception:
            logger.exception('Error terminating session %s on disconnect', session_id)
    else:
        logger.debug('Game socket disconnected: sid=%s (no session)', request.sid)


# ---------------------------------------------------------------------------
# Casino wallet API
# ---------------------------------------------------------------------------

_CASINO_DEFAULTS = {'blackjack': 500, 'slots': 200, 'videopoker': 200, 'holdem': 1000}
CASINO_SLUGS = set(_CASINO_DEFAULTS)


def _casino_start_bal(slug):
    default = _CASINO_DEFAULTS.get(slug, 500)
    try:
        return int(current_app.config.get(f'CASINO_{slug.upper()}_START', default))
    except (ValueError, TypeError):
        return default


def _week_start():
    from datetime import date, timedelta
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


def _next_monday():
    from datetime import date, timedelta
    today = date.today()
    return (today + timedelta(days=7 - today.weekday())).isoformat()


def _wallet_json(w):
    broke = w.balance <= 0
    return jsonify({
        'balance': w.balance,
        'peak': w.peak_balance,
        'starting_balance': w.starting_balance,
        'broke': broke,
        'resets': _next_monday() if broke else None,
    })


@games_bp.route('/<slug>/wallet')
@login_required
def get_wallet(slug):
    if slug not in CASINO_SLUGS:
        abort(404)
    week = _week_start()
    start = _casino_start_bal(slug)
    wallet = WebGameWallet.query.filter_by(
        user_id=current_user.id, game_slug=slug).first()
    if wallet is None:
        wallet = WebGameWallet(
            user_id=current_user.id, game_slug=slug,
            balance=start, peak_balance=start,
            starting_balance=start, week_start=week,
            last_active=datetime.utcnow(),
        )
        db.session.add(wallet)
        db.session.commit()
    elif wallet.week_start != week:
        wallet.balance = start
        wallet.starting_balance = start
        wallet.week_start = week
        wallet.last_active = datetime.utcnow()
        db.session.commit()
    return _wallet_json(wallet)


@games_bp.route('/<slug>/wallet', methods=['POST'])
@login_required
def update_wallet(slug):
    if slug not in CASINO_SLUGS:
        abort(404)
    game = Game.query.filter_by(slug=slug, is_active=True).first_or_404()
    data = request.get_json(silent=True) or {}
    new_bal = max(0, int(data.get('balance', 0)))
    week = _week_start()
    start = _casino_start_bal(slug)

    entry = None
    wallet = WebGameWallet.query.filter_by(
        user_id=current_user.id, game_slug=slug).first()
    if wallet is None:
        wallet = WebGameWallet(
            user_id=current_user.id, game_slug=slug,
            balance=new_bal, peak_balance=new_bal,
            starting_balance=start, week_start=week,
            last_active=datetime.utcnow(),
        )
        db.session.add(wallet)
    else:
        if wallet.week_start != week:
            wallet.balance = start
            wallet.starting_balance = start
            wallet.week_start = week
            wallet.last_active = datetime.utcnow()
            db.session.commit()
            return _wallet_json(wallet)
        wallet.balance = new_bal
        wallet.last_active = datetime.utcnow()
        if new_bal > wallet.peak_balance:
            wallet.peak_balance = new_bal
            entry = GameScore(
                game_id=game.id,
                user_id=current_user.id,
                score=wallet.peak_balance,
                details=json.dumps({'type': 'peak_balance', 'week': week}),
                achieved_at=datetime.utcnow(),
            )
            db.session.add(entry)
    db.session.commit()

    if entry is not None:
        try:
            from ..echomail.interbbs_sync import post_score_to_interbbs
            post_score_to_interbbs(entry)
        except Exception:
            logger.exception('post_score_to_interbbs failed for score %s', entry.id)

    return _wallet_json(wallet)


@games_bp.route('/scoreboard')
def scoreboard():
    """Cross-game leaderboard — top scores per game."""
    from ..models import db as _db, GameScore as _GS, Game as _G
    by_game = {}
    try:
        games = _G.query.filter_by(is_active=True).order_by(_G.name).all()
        for g in games:
            top = (_GS.query.filter_by(game_id=g.id)
                   .order_by(_GS.score.desc())
                   .limit(10).all())
            rows = []
            for s in top:
                # display_username picks the right name whether this
                # score was earned locally or imported via InterBBS
                # score sharing (see GameScore.display_username).
                rows.append({
                    'username': s.display_username,
                    'score': s.score, 'when': s.achieved_at,
                    'origin_bbs': s.origin_bbs,
                    'is_casino': g.slug in CASINO_SLUGS,
                })
            if rows:
                by_game[g] = rows
    except Exception:
        _db.session.rollback()
    return render_template('games/scoreboard.html', by_game=by_game)
