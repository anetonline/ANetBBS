# anetbbs/web_app.py
"""
Flask web application for ANetBBS
Main entry point for the web interface
"""
# eventlet monkey-patch must run before anything else imports stdlib
# socket / threading / ssl. NOTE: Gunicorn 25 deprecates the eventlet
# worker — we tried gevent + geventwebsocket in v174–v175 but
# gevent-websocket 0.10.1 (2018) hangs websocket upgrades against
# modern gevent. Revisit when flask-socketio ships a maintained
# alternative or we move to ASGI/uvicorn.
import eventlet
eventlet.monkey_patch()

import os
import sys
import secrets
import logging
import json
from datetime import datetime
from flask import Flask, request, render_template
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_socketio import SocketIO, join_room
from flask_wtf.csrf import CSRFProtect

from .models import db, User, Theme, UserSession
from .config import get_config


# Initialize extensions
login_manager = LoginManager()
migrate = Migrate()
# Bump heartbeat to 60s timeout / 25s interval so a slow IRC MOTD or
# brief network blip doesn't kick the user. Defaults (5s timeout) were
# dropping users immediately after MOTD floods.
socketio = SocketIO(async_mode='eventlet',
                    ping_timeout=60,
                    ping_interval=25)
csrf = CSRFProtect()


@socketio.on('connect')
def _handle_default_namespace_connect():
    """The default-namespace socket base.html already opens for every
    authenticated page (used for sysop_broadcast/sysop_page) also joins a
    room named after its own user id here -- zero extra client-side
    connections needed. This is what lets features/notify.py's notify()
    push a live 'user_notification' toast to an already-open browser tab
    the instant a Notification row is created (echomail/QWK replies,
    @mentions, netmail, etc.), rather than only surfacing on next page
    load. No-op for anonymous visitors -- base.html never opens this
    socket for them in the first place, so this is just defense in depth."""
    if current_user.is_authenticated:
        join_room(str(current_user.id))


def create_app(config_name=None):
    """Application factory pattern"""
    app = Flask(__name__)
    
    # Load configuration
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')
    
    config_class = get_config(config_name)
    app.config.from_object(config_class)

    # Real gap found in a full auth-security audit: without this,
    # request.remote_addr behind install.sh's own generated nginx reverse
    # proxy is ALWAYS nginx's own loopback address, and every piece of
    # code that wants the real visitor IP (web/auth.py's IP ban/country-
    # block/rate-limit-auto-ban, the who's-online session tracker just
    # below) either silently operates on the wrong address or -- worse,
    # as auth.py did before this fix -- falls back to manually trusting
    # the client-supplied X-Forwarded-For header with no proxy-trust
    # boundary at all, which any direct connection can spoof. ProxyFix
    # is the standard, well-tested fix: trust exactly one X-Forwarded-For
    # hop and rewrite request.remote_addr itself, so every downstream
    # consumer gets the correct value with no per-call-site changes.
    # Strictly opt-in (see TRUST_PROXY_HEADERS's own comment in
    # config.py) -- unsafe to enable on an install where Flask is
    # directly reachable from the internet.
    if app.config.get('TRUST_PROXY_HEADERS'):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # ANETBBS_SCHEMA_MIGRATE_ONLY=1 marks this process as a one-shot CLI
    # command (a schema migration, or a standalone tool like anetbbs-cfg)
    # rather than the long-running web/BBS server -- used below both to
    # bypass the SECRET_KEY production check (a one-shot command doesn't
    # serve traffic) AND to skip starting every background service
    # (echomail/RSS pollers, MSP/SYSTAT listeners, registry heartbeat,
    # events scheduler, ...). Real bug found running anetbbs-cfg on a live
    # install: without this, `create_app()` started a SECOND full copy of
    # every one of those background services/threads alongside the
    # already-running anetbbs-web process -- double-polling echomail,
    # double-firing scheduled events, a second registry heartbeat racing
    # the real one -- just to open a local config screen. Set by
    # anetbbs/cfg/app.py for exactly this reason; also already set by
    # update.sh's schema-migration step and the upgrade wizard.
    _one_shot_cli = bool(os.environ.get('ANETBBS_SCHEMA_MIGRATE_ONLY'))

    # Refuse to boot in production with the dev SECRET_KEY fallback.
    # In dev, just log a loud warning so the sysop sees it.
    # Bypass with ANETBBS_SCHEMA_MIGRATE_ONLY=1 — the upgrade wizard's
    # migration step doesn't serve traffic and shouldn't be blocked.
    _bad_defaults = {
        'dev-secret-key-change-in-production',
        'changeme',
        'your-secret-key-here',
        '',
    }
    if (app.config.get('SECRET_KEY') in _bad_defaults
            and not os.environ.get('ANETBBS_SCHEMA_MIGRATE_ONLY')):
        _is_prod = (config_name == 'production'
                    or os.environ.get('FLASK_ENV') == 'production'
                    or app.config.get('ENV') == 'production')
        if _is_prod:
            raise RuntimeError(
                'SECRET_KEY is the development default. '
                'Set the SECRET_KEY env var (e.g. `python -c "import secrets;'
                'print(secrets.token_urlsafe(48))"`) before starting in '
                'production. The upgrade wizard auto-generates one if .env '
                'is missing it; set ANETBBS_SCHEMA_MIGRATE_ONLY=1 to bypass '
                'this check for one-shot CLI / migration commands.')
        import logging as _lg
        _lg.getLogger(__name__).warning(
            'SECRET_KEY is a known-insecure default — fine for local dev; '
            'export SECRET_KEY=... before exposing this to the internet.')

    # Ensure data directory exists
    os.makedirs(app.config['DATA_DIR'], exist_ok=True)
    os.makedirs(app.config.get('UPLOADS_DIR', os.path.join(app.config['DATA_DIR'], 'uploads')), exist_ok=True)
    os.makedirs(app.config.get('AVATARS_DIR', os.path.join(app.config['DATA_DIR'], 'avatars')), exist_ok=True)
    os.makedirs(app.config.get('NETWORK_JOIN_DIR', os.path.join(app.config['DATA_DIR'], 'network_join')), exist_ok=True)
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    # Real gap found in a security/performance audit: cors_allowed_origins
    # explicitly overrode python-engineio's own default with "*", allowing
    # ANY origin's page to open a Socket.IO connection here carrying the
    # user's session cookie -- every real client in this app connects
    # same-origin (io('/', ...) in base.html/terminal/index.html etc.),
    # so there is no legitimate cross-origin use case. Omitting the
    # kwarg restores engineio's built-in default (None), which derives
    # the allowed origin from the request's own scheme+host (and the
    # X-Forwarded-* pair when present, so it still works behind a
    # reverse proxy) -- same-origin only, no config needed.
    socketio.init_app(app, async_mode='eventlet',
                      ping_timeout=60, ping_interval=25,
                      logger=False, engineio_logger=False)
    csrf.init_app(app)
    
    # Configure login manager
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    
    @login_manager.user_loader
    def load_user(user_id):
        # Real gap found in a security/performance audit: this is the
        # ONLY place Flask-Login re-establishes current_user on every
        # request (both the regular session cookie AND the "remember
        # me" cookie path, which login_user(remember=True) below always
        # sets, terminate here) -- it never checked is_active/is_locked,
        # which were previously only ever consulted at the MOMENT of a
        # fresh login (web/auth.py's /auth/login route). A sysop
        # banning or locking a user via the admin panel (admin.py's
        # toggle_ban / lock_user) flipped the DB column but had zero
        # effect on that user's ALREADY-established session -- they
        # could keep posting/PMing/chatting until their cookie
        # naturally expired (REMEMBER_COOKIE_DURATION isn't configured
        # anywhere, so Flask-Login's own 365-day default applies).
        # Returning None here makes Flask-Login treat the request as
        # logged-out (AnonymousUserMixin) starting on the very next
        # request after a ban/lock takes effect, for both the plain
        # session and the remember-cookie path alike.
        user = User.query.get(int(user_id))
        if user is None:
            return None
        if not user.is_active or user.is_locked:
            return None
        return user

    @app.before_request
    def track_user_session():
        """Track authenticated user sessions for online presence"""
        from flask_login import current_user
        from flask import request
        if current_user.is_authenticated:
            # Skip static-asset requests (fonts/CSS/JS/images) entirely --
            # browsers fetch these on their own schedule (a font in
            # particular often loads lazily, well after the real page
            # request), so a static fetch can easily be the LAST request
            # this session made. Without this guard, /who/ (sysops see the
            # raw path) would sometimes show a user "on"
            # /static/fonts/Ac437_IBM_VGA_9x16.woff instead of whatever
            # page they're actually on -- reported live. `request.endpoint
            # == 'static'` is Flask's own built-in static-route endpoint
            # name, not a path string match, so this can't be fooled by a
            # real route that merely starts with /static.
            if request.endpoint == 'static':
                return
            # Real bug found live: this used to look up "the" UserSession
            # row for this user_id (unique=True at the time) -- a second
            # simultaneous connection for the same account (e.g. an SSH
            # session while already logged in on the web) collided on
            # that same row instead of getting its own, so "who's
            # online" only ever showed whichever one wrote most
            # recently. session_key -- a random token stashed once per
            # browser session in the signed Flask session cookie, same
            # stash-a-correlation-id idiom auth.py's login route already
            # uses for caller_log_id -- gives each BROWSER SESSION (not
            # just each protocol) its own row.
            import uuid as _uuid
            from flask import session as _flask_session
            key = _flask_session.get('presence_session_key')
            if not key:
                key = _uuid.uuid4().hex
                _flask_session['presence_session_key'] = key
            user_session = UserSession.query.filter_by(session_key=key).first()
            if user_session is None:
                user_session = UserSession(user_id=current_user.id, session_key=key)
                db.session.add(user_session)
            user_session.user_id = current_user.id
            user_session.last_seen = datetime.utcnow()
            user_session.ip_address = request.remote_addr
            user_session.user_agent = request.user_agent.string[:255] if request.user_agent.string else None
            user_session.page = request.path
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    @app.context_processor
    def _inject_anetbbs_version():
        """Expose ``anetbbs_version`` to every template so the footer +
        About page can show what's running. Read once at import; the
        per-request cost is just a dict lookup."""
        from .version import VERSION
        return {'anetbbs_version': VERSION}

    @app.context_processor
    def _inject_effective_theme():
        """Resolve which Theme should actually be applied this request.

        Admin's "Default Theme" checkbox (Theme.is_default) correctly
        toggled on/off in the DB, but nothing ever READ it — base.html
        only ever applied `current_user.theme`, which is None for any
        user who never explicitly picked one (including every new
        signup) and for every logged-out visitor. Setting a new default
        in admin therefore visibly saved but never changed what anyone
        actually saw -- reported live ("when you set the 'default
        theme' it does not make a difference. it does not change the
        default"). base.html now uses `effective_theme` (this) instead
        of `current_user.theme` directly: a signed-in user's own pick
        still wins if they have one, otherwise this falls back to
        whichever Theme has is_default=True, for BOTH logged-in users
        without a personal choice and logged-out visitors.
        """
        from flask_login import current_user
        try:
            if current_user.is_authenticated and current_user.theme:
                return {'effective_theme': current_user.theme}
            return {'effective_theme': Theme.query.filter_by(is_default=True, is_active=True).first()}
        except Exception:
            return {'effective_theme': None}

    @app.context_processor
    def inject_online_count():
        from datetime import timedelta
        five_min_ago = datetime.utcnow() - timedelta(minutes=5)
        try:
            # Distinct users, not raw UserSession rows -- one person
            # connected via both web and SSH at once (now that
            # UserSession supports more than one row per user, see
            # models.UserSession's docstring) must still count as 1
            # here, not 2.
            count = (db.session.query(UserSession.user_id)
                    .filter(UserSession.last_seen >= five_min_ago)
                    .distinct().count())
        except Exception:
            count = 0
        return {'online_count': count}

    @app.context_processor
    def _inject_qotd():
        """Pick one MOTD per request and expose as {{ qotd }} in templates."""
        qotd = ''
        try:
            from .models import MotdEntry
            import random
            pool = MotdEntry.query.filter_by(is_active=True).all()
            if pool:
                weighted = []
                for m in pool:
                    weighted.extend([m.text] * max(1, m.weight or 1))
                qotd = random.choice(weighted)
        except Exception:
            pass
        return {'qotd': qotd}

    @app.context_processor
    def _inject_network_join_enabled():
        """Expose {{ network_join_enabled }} so the Tools nav dropdown
        can link to /join/ only when it's actually reachable (hub
        install + sysop has turned it on) -- cheap config check first,
        DB query only on hub installs, since this runs every request."""
        if not app.config.get('REGISTRY_MODE_ENABLED'):
            return {'network_join_enabled': False}
        try:
            from .models import NetworkJoinConfig
            cfg = NetworkJoinConfig.query.first()
            return {'network_join_enabled': bool(cfg and cfg.enabled)}
        except Exception:
            return {'network_join_enabled': False}

    @app.template_filter('from_json')
    def from_json_filter(value):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}

    @app.template_filter('eastern')
    def eastern_filter(value, fmt='%Y-%m-%d %H:%M', default=''):
        """Display a stored (naive-UTC) datetime column in US/Eastern
        (EST/EDT, DST-aware) instead of raw UTC. Storage stays UTC
        everywhere -- this is a display-only conversion, see
        anetbbs/core/tz.py. `default` is returned as-is when value is
        None, matching the `X.strftime(...) if X else 'y'` idiom this
        replaces throughout the templates -- pass each site's own
        existing fallback text through unchanged.

        Use `%Z` in fmt to include the correct current abbreviation
        (EST or EDT) instead of a hardcoded literal "UTC"/"ET" string.
        """
        from .core.tz import fmt_eastern
        return fmt_eastern(value, fmt, default)

    @app.template_filter('linkify_images')
    def linkify_images(value):
        """Auto-embed image URLs (jpg/png/gif/webp) in plain text.

        Replaces matching URLs with an <img> tag while escaping the rest
        of the text. Existing newlines are preserved.
        """
        from markupsafe import Markup, escape
        import re as _re
        if not value:
            return Markup('')
        url_re = _re.compile(
            r'(https?://[^\s<>"\']+\.(?:jpg|jpeg|png|gif|webp|svg))',
            _re.IGNORECASE)
        out = []
        last = 0
        for m in url_re.finditer(value):
            out.append(escape(value[last:m.start()]))
            url = m.group(1)
            out.append('<br><img src="' + escape(url) +
                       '" alt="" style="max-width:100%;max-height:600px;'
                       'border:1px solid var(--theme-border);"><br>')
            last = m.end()
        out.append(escape(value[last:]))
        return Markup(''.join(out).replace('\n', '<br>'))  # nosec B704 -- every segment escape()d above

    from .web.render_msg import (render_msg_body as _render_msg_body,
                                   render_msg_body_rich as _render_msg_body_rich)
    from .web.votes import get_tally as _get_tally
    @app.context_processor
    def _inject_vote_helpers():
        from flask_login import current_user as _cu
        def vote_tally(msg_type, msg_id):
            uid = _cu.id if _cu.is_authenticated else None
            return _get_tally(msg_type, msg_id, uid)
        return {'vote_tally': vote_tally}
    @app.template_filter('msgbody')
    def msgbody_filter(value, chrs=''):
        """Render a message body with CP437 high-byte translation and
        ANSI SGR -> HTML conversion. Pass the message's chrs kludge value
        as the second argument to honor UTF-8/Latin-1 declarations."""
        return _render_msg_body(value, chrs)

    @app.template_filter('msgbody_rich')
    def msgbody_rich_filter(value, chrs=''):
        """Like msgbody but also embeds image URLs as <img> tags.
        Use for local boards/PMs where users may paste image links."""
        return _render_msg_body_rich(value, chrs)

    @app.template_filter('ansi_art')
    def ansi_art_filter(value):
        """Render a pre-decoded CP437/ANSI string (e.g. FILE_ID.DIZ) as HTML.

        Unlike msgbody, the description is already a proper Unicode string
        (decoded from CP437 bytes by archive_meta._decode) — no latin-1
        round-trip needed.  Just runs ANSI SGR -> HTML spans and pipe codes.
        Newlines are left as-is so the caller's <pre> block preserves them.
        """
        from markupsafe import Markup
        from .web.render_msg import _ansi_to_html, _pipe_to_ansi
        if not value:
            return Markup('')
        return Markup(_ansi_to_html(_pipe_to_ansi(str(value))))  # nosec B704 -- _ansi_to_html() escape()s all literal text

    @app.template_filter('strip_ansi')
    def strip_ansi_filter(value):
        """Strip ANSI escape sequences, returning plain text."""
        import re as _re
        from markupsafe import Markup, escape
        if not value:
            return Markup('')
        clean = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(value))
        return Markup(str(escape(clean)))  # nosec B704 -- escape()d immediately above

    from .web.markdown_render import render_markdown as _render_markdown
    @app.template_filter('markdown')
    def markdown_filter(value):
        """Render Markdown to safe HTML. See web/markdown_render.py for the
        real implementation (shared with the per-message lazy-render JSON
        endpoints -- echomail/netmail/boards/pm)."""
        return _render_markdown(value)

    # Register blueprints
    from .web.auth import auth_bp
    from .web.main import main_bp
    from .web.boards import boards_bp
    from .web.profile import profile_bp
    from .web.mrc_web import mrc_bp
    from .web.admin import admin_bp
    from .web.pm import pm_bp
    from .web.files import files_bp
    from .web.echomail import echomail_bp
    from .web.echomail_admin import echomail_admin_bp
    from .web.games import games_bp
    from .web.games_admin import games_admin_bp
    from .web.ebooks import ebooks_bp
    from .web.meadowlark import meadowlark_bp
    from .web.darkforces import darkforces_bp
    from .web.gallery import gallery_bp
    from .web.gallery_admin import gallery_admin_bp
    from .web.rss import rss_bp, redirect_bp
    from .web.rss_admin import rss_admin_bp
    from .web.menu_admin import menu_admin_bp
    from .web.petscii_menu_admin import petscii_menu_admin_bp
    from .web.nodelist import nodelist_bp
    from .web.registry import registry_bp
    from .web.netmail import netmail_bp
    from .web.votes import votes_bp
    from .web.imsg import imsg_bp
    from .web.irc_web import irc_bp, register_socketio_handlers as _register_irc_handlers
    from .web.contacts import contacts_bp
    from .web.polls import polls_bp
    from .web.shoutbox import shoutbox_bp
    from .web.saved import saved_bp
    from .web.file_areas import file_areas_bp
    from .web.permalinks import m_bp
    from .web.who import who_bp
    from .web.bulletins import bulletins_bp
    from .web.site_pages import site_pages_bp
    from .web.finger import finger_bp
    from .web.telegram import telegram_bp
    from .web.peers import peers_bp
    from .web.ansi_editor import ansi_bp
    from .web.web_terminal import term_bp, register_socketio_handlers as _register_term_handlers
    from .web.stats import stats_bp
    from .web.feeds import feeds_bp
    from .web.gemini import gemini_bp
    from .web.notifications import notif_bp
    from .web.calendar import cal_bp
    from .web.file_queue import queue_bp as file_queue_bp
    from .web.blocks import blocks_bp
    from .web.groups import groups_bp
    from .web.leaderboard import leader_bp
    from .web.oneliners import ol_bp
    from .web.qwk_user import qwk_user_bp
    from .web.control import control_bp
    from .web.pulse import pulse_bp
    from .web.upgrades import upgrades_api_bp, upgrades_admin_bp
    from .web.healthz import healthz_bp
    from .web.preflight import preflight_bp
    from .web.peer_health import peers_health_bp
    from .web.events_admin import events_admin_bp
    from .web.security_admin import security_bp
    from .web.door_errors import door_errors_bp
    from .web.backups_admin import backups_bp
    from .web.login_modules_admin import login_modules_admin_bp
    from .web.file_bulletins_admin import file_bulletins_admin_bp
    from .web.wall_admin import wall_admin_bp
    from .web.lastcallers_admin import lastcallers_admin_bp
    from .web.games_interbbs_admin import games_interbbs_admin_bp
    from .web.personal_pages import pages_bp, serve_root_page
    from .web.docs import docs_bp
    from .web.wiki import wiki_bp
    from .web.guru import guru_bp
    from .web.hub_admin import hub_admin_bp
    from .web.qwk_hub import qwk_hub_bp
    from .web.network_join import network_join_bp
    from .web.watch import watch_bp
    from .web.postcards import postcards_bp
    from .web.social_admin import social_admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(boards_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(mrc_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(pm_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(echomail_bp)
    app.register_blueprint(votes_bp)
    app.register_blueprint(imsg_bp)
    app.register_blueprint(echomail_admin_bp)
    app.register_blueprint(hub_admin_bp)
    app.register_blueprint(qwk_hub_bp)
    csrf.exempt(qwk_hub_bp)   # QWK nodes use HTTP Basic Auth, not CSRF tokens
    app.register_blueprint(network_join_bp)  # real browser form -- keeps CSRF
    app.register_blueprint(games_bp)
    app.register_blueprint(games_admin_bp)
    app.register_blueprint(ebooks_bp)
    app.register_blueprint(meadowlark_bp)
    app.register_blueprint(darkforces_bp)
    app.register_blueprint(gallery_bp)
    app.register_blueprint(gallery_admin_bp)
    app.register_blueprint(rss_bp)
    app.register_blueprint(rss_admin_bp)
    app.register_blueprint(redirect_bp)
    app.register_blueprint(menu_admin_bp)
    app.register_blueprint(petscii_menu_admin_bp)
    app.register_blueprint(nodelist_bp)
    app.register_blueprint(registry_bp)
    # Federation registry API is called by peer ANetBBS hosts, not
    # browsers — exempt from the CSRF check that protects the rest of
    # the site. Admin-side `/admin/registry/` routes still get CSRF
    # via the admin blueprint.
    csrf.exempt(registry_bp)
    app.register_blueprint(netmail_bp)
    app.register_blueprint(irc_bp)
    _register_irc_handlers(socketio)
    app.register_blueprint(contacts_bp)
    app.register_blueprint(polls_bp)
    app.register_blueprint(shoutbox_bp)
    app.register_blueprint(saved_bp)
    app.register_blueprint(file_areas_bp)
    app.register_blueprint(m_bp)
    app.register_blueprint(who_bp)
    app.register_blueprint(bulletins_bp)
    app.register_blueprint(site_pages_bp)
    app.register_blueprint(finger_bp)
    app.register_blueprint(telegram_bp)
    app.register_blueprint(peers_bp)
    app.register_blueprint(ansi_bp)
    app.register_blueprint(term_bp)
    _register_term_handlers(socketio)
    app.register_blueprint(stats_bp)
    app.register_blueprint(feeds_bp)
    app.register_blueprint(gemini_bp)
    app.register_blueprint(notif_bp)
    app.register_blueprint(cal_bp)
    app.register_blueprint(file_queue_bp)
    app.register_blueprint(blocks_bp)
    app.register_blueprint(groups_bp)
    app.register_blueprint(leader_bp)
    app.register_blueprint(ol_bp)
    app.register_blueprint(qwk_user_bp)
    app.register_blueprint(control_bp)
    app.register_blueprint(pulse_bp)
    # Upgrades: federation API + sysop "Check for updates" UI.
    # The API is called by peer ANetBBS hosts (no browser) so exempt
    # it from CSRF for the same reason the registry API is.
    app.register_blueprint(upgrades_api_bp)
    csrf.exempt(upgrades_api_bp)
    app.register_blueprint(upgrades_admin_bp)
    # /healthz — public, unauth, JSON. Used by update.sh post-restart
    # probe + external uptime monitors. CSRF-exempt because monitors
    # don't carry tokens.
    app.register_blueprint(healthz_bp)
    csrf.exempt(healthz_bp)
    # Preflight checklist — admin-only one-page "is this BBS ready to be
    # public?" probe board. Pure GET, no CSRF concern.
    app.register_blueprint(preflight_bp)
    # Federation peer health — admin-only liveness lens on RegistryEntry.
    # Only meaningful on REGISTRY_MODE_ENABLED installs.
    app.register_blueprint(peers_health_bp)
    # Scheduled-events admin: CRUD + run-now for ScheduledEvent rows.
    # The runner thread for these is started further down with the
    # other background threads.
    app.register_blueprint(events_admin_bp)
    # Security: viewer for the daily-scan JSON report. Admin-only.
    app.register_blueprint(security_bp)
    # Door-errors: parses logs/door-errors.log into readable entries.
    app.register_blueprint(door_errors_bp)
    # Backups: list + restore + delete /tmp/anetbbs-backup-* snapshots.
    app.register_blueprint(backups_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(docs_bp)
    app.register_blueprint(wiki_bp)
    app.register_blueprint(guru_bp)
    app.register_blueprint(watch_bp)
    app.register_blueprint(postcards_bp)
    app.register_blueprint(social_admin_bp)
    # Logon/logoff modules and graffiti wall admin.
    app.register_blueprint(login_modules_admin_bp)
    app.register_blueprint(file_bulletins_admin_bp)
    app.register_blueprint(wall_admin_bp)
    app.register_blueprint(lastcallers_admin_bp)
    app.register_blueprint(games_interbbs_admin_bp)

    # Public downloads — auto-listing of the sysop's release directory.
    # No DB rows; scans DOWNLOADS_DIR on each (cached) request.
    from .web.downloads import downloads_bp
    app.register_blueprint(downloads_bp)

    # Personal pages 404 fallback — catches /<dir>/ URLs that no real
    # blueprint handled and tries data/personal_pages/<dir>/index.html.
    # If that doesn't match either, render the branded 404 template.
    @app.errorhandler(404)
    def _personal_pages_404(e):
        try:
            path = (request.path or '').lstrip('/')
            resp = serve_root_page(path)
            if resp is not None:
                return resp
        except Exception:
            pass
        try:
            return render_template('errors/404.html'), 404
        except Exception:
            return e

    @app.errorhandler(403)
    def _forbidden(e):
        try:
            return render_template('errors/403.html'), 403
        except Exception:
            return e

    @app.errorhandler(500)
    def _server_error(e):
        app.logger.exception('500: %s', e)
        try:
            return render_template('errors/500.html'), 500
        except Exception:
            return e

    # Create database tables
    with app.app_context():
        db.create_all()
        _lightweight_migrate(app)
        try:
            _create_default_data()
        except Exception as exc:
            app.logger.exception(
                'Default-data seeding failed (likely a schema lag — '
                'the migrate just patched a column the seeder needed): %s',
                exc)
            db.session.rollback()
            # Try once more now that migrate has run.
            try:
                _create_default_data()
            except Exception:
                app.logger.exception('Default-data seeding still failed; '
                                     'continuing — fix manually.')
                db.session.rollback()
        # Auto-seed default BBS menu so the data-driven menu engine works
        # immediately on a fresh install (idempotent — no-op if any exist).
        try:
            from .features.menu_engine import seed_default_menus
            seed_default_menus()
        except Exception:
            app.logger.exception('Failed to seed default BBS menus')
        # Per-node scratch dirs (data/temp/node1..nodeN) so door games
        # always have somewhere to drop DOOR.SYS / DOOR32.SYS / DORINFO.
        try:
            from .games.node_paths import ensure_node_dirs
            ensure_node_dirs(app.config.get('BBS_NODES'))
        except Exception:
            app.logger.exception('Failed to create per-node temp dirs')
        # Mark any 'active' game sessions from before this restart as stale.
        # After a restart the in-memory PTY state is gone — those sessions
        # will never self-terminate, so clean them out now.
        try:
            from .models import GameSession as _GS
            stale_count = _GS.query.filter_by(status='active').update(
                {'status': 'stale', 'ended_at': datetime.utcnow()})
            db.session.commit()
            if stale_count:
                app.logger.info('Marked %d stale game session(s) from previous run', stale_count)
        except Exception:
            db.session.rollback()
            app.logger.warning('Could not clean up stale game sessions on startup')
    
    # Configure logging
    _configure_logging(app)
    
    # Start background echomail poller
    if app.config.get('ECHOMAIL_ENABLED', True) and not (app.config.get('TESTING', False) or _one_shot_cli):
        from .echomail.poller import start_poller
        start_poller(app)

    # Start background RSS poller (refreshes feeds on RSS_POLL_INTERVAL,
    # default 30 min). No-op in TESTING mode.
    if not (app.config.get('TESTING', False) or _one_shot_cli):
        try:
            from .rss.poller import start_poller as start_rss_poller
            start_rss_poller(app)
        except Exception:
            app.logger.exception('RSS poller failed to start')

    # Start the inter-BBS instant message (MSP / RFC 1312) listener
    if app.config.get('MSP_ENABLED', True) and not (app.config.get('TESTING', False) or _one_shot_cli):
        from .msp.server import start_msp_server
        start_msp_server(app)

    # SYSTAT / ActiveUser UDP service (Synchronet IMSG companion to MSP)
    if app.config.get('SYSTAT_ENABLED', True) and not (app.config.get('TESTING', False) or _one_shot_cli):
        from .msp.systat import start_systat_server
        start_systat_server(app)

    # Daily refresh of the inter-BBS directory (sbbsimsg.lst)
    if app.config.get('SBBSIMSG_AUTO_REFRESH', True) and not (app.config.get('TESTING', False) or _one_shot_cli):
        from .msp.directory import start_refresher
        start_refresher(app)

    # Daily refresh of the ANetBBS federation directory (anetbbs.lst).
    # Independent of the registry-hub role: any install with a
    # REGISTRY_URL set will pull the upstream list so its users can see
    # peer ANetBBS systems in /imsg/directory/.
    if not (app.config.get('TESTING', False) or _one_shot_cli):
        from .msp.anetbbs_directory import start_anetbbs_directory_refresher
        start_anetbbs_directory_refresher(app)

    # Federation hub — SYSTAT prober that keeps anetbbs.lst pruned of
    # dead peers. Only runs when this install is the central hub
    # (REGISTRY_MODE_ENABLED). No-op on peer installs.
    if app.config.get('REGISTRY_MODE_ENABLED') and not (app.config.get('TESTING', False) or _one_shot_cli):
        from .msp.probe import start_probe_thread
        start_probe_thread(app)

    # Federation hub — seed + heartbeat the hub's OWN RegistryEntry so
    # /anetbbs.lst includes the hub itself. Without this, the hub's
    # own /imsg/directory shows no self-entry until a sysop creates
    # the row by hand. Pre-verified + pre-approved (the hub trusts
    # itself). No-op on peer installs.
    if app.config.get('REGISTRY_MODE_ENABLED') and not (app.config.get('TESTING', False) or _one_shot_cli):
        from .msp.hub_self_register import start_hub_self_register_thread
        start_hub_self_register_thread(app)

    # Federation hub — daily self-test that fetches our own public
    # surfaces (anetbbs.lst, /api/releases/latest, /healthz) over the
    # configured REGISTRY_URL. Catches reverse-proxy regressions
    # before peers start complaining.
    if app.config.get('REGISTRY_MODE_ENABLED') and not (app.config.get('TESTING', False) or _one_shot_cli):
        try:
            from .msp.hub_selftest import start_hub_selftest_thread
            start_hub_selftest_thread(app)
        except Exception:
            app.logger.exception('Hub selftest thread failed to start')

    # Service Control Center — per-PID CPU% / RSS / thread sampler that
    # feeds the live graphs at /admin/control/. Reads MainPID via
    # `systemctl show` and /proc via psutil; no privileges required.
    # Soft-no-ops if psutil isn't installed.
    if not (app.config.get('TESTING', False) or _one_shot_cli):
        try:
            from .web.metrics import start_sampler as _start_metrics_sampler
            _start_metrics_sampler()
        except Exception:
            app.logger.exception('SCC metrics sampler failed to start')

    # Scheduled-events runner — generic cron-style task scheduler.
    # Replaces the .43 TW2-specific maint thread. On boot we seed
    # default rows (TW2 maint, weekly VACUUM, log rotation) so a fresh
    # install gets reasonable cadence out of the box. Idempotent —
    # existing rows are never overwritten.
    if not (app.config.get('TESTING', False) or _one_shot_cli):
        try:
            from .events.runner import (start_event_scheduler,
                                        ensure_default_events)
            ensure_default_events(app)
            start_event_scheduler(app)
        except Exception:
            app.logger.exception('Event scheduler failed to start')

    # Self-registration against the federation hub. Off by default;
    # peer sysops opt in by setting REGISTRY_SELF_REGISTER=true and
    # filling in SYSOP_EMAIL / BBS_DOMAIN so the hub can email them
    # the verify token.
    if app.config.get('REGISTRY_SELF_REGISTER') and not (app.config.get('TESTING', False) or _one_shot_cli):
        from .msp.registry_client import start_self_register_thread
        start_self_register_thread(app)

    # Cross-protocol "X just logged in/out" alerts — relays terminal-
    # originated PresenceEvent rows to connected browser tabs over
    # SocketIO. Terminal sessions poll PresenceEvent directly on their
    # own (core/session.py); this thread exists because telnet/SSH/
    # rlogin run in a separate process from the web app in production.
    if not (app.config.get('TESTING', False) or _one_shot_cli):
        try:
            from .core.presence import start_presence_alert_relay
            start_presence_alert_relay(app)
        except Exception:
            app.logger.exception('Presence alert relay failed to start')

    return app


def _configure_logging(app):
    """Configure application logging.

    Two real bugs found live (2026-08-28, Jerry's dev laptop repeatedly
    OOM-killed): bbs.log had grown to 6.1GB / 80M+ lines with a plain
    logging.FileHandler and no rotation at all. Separately, and the
    bigger reason it got that large in the first place: this function
    used to unconditionally addHandler() a NEW FileHandler every call,
    but Flask's app.logger is cached by name -- Python's logging module
    never garbage-collects a named logger -- so within one long-running
    process (this repo's own test suite calls create_app() once per
    test file, dozens of times in a single `pytest a.py b.py c.py ...`
    invocation), handlers piled up on the SAME logger across every
    call. logging dispatches every record to every attached handler, so
    each pileup meant every subsequent log line got written once per
    accumulated handler -- an ordinary log's line count compounding
    across a whole dev session is how it reached 80 million lines.
    Fixed together: clear any handlers this function itself previously
    attached before adding fresh ones (so repeated create_app() calls
    in one process can't accumulate), and cap the file with rotation so
    even a single handler can't grow the file unbounded again.
    """
    from logging.handlers import RotatingFileHandler

    log_level = getattr(logging, app.config['LOG_LEVEL'])

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_formatter)

    # File handler -- rotating (20MB x 5 backups, ~100MB max on disk)
    # instead of an unbounded plain FileHandler.
    file_handler = RotatingFileHandler(
        app.config['LOG_FILE'], maxBytes=20 * 1024 * 1024, backupCount=5)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(console_formatter)

    # Configure app logger -- drop any StreamHandler/FileHandler this
    # function attached on a PRIOR call (e.g. an earlier create_app()
    # in the same process) before adding this call's pair, so repeated
    # calls can't pile up duplicate handlers.
    app.logger.handlers = [
        h for h in app.logger.handlers
        if not isinstance(h, (logging.StreamHandler, logging.FileHandler))]
    app.logger.setLevel(log_level)
    app.logger.addHandler(console_handler)
    app.logger.addHandler(file_handler)

    # Also configure root logger for SQLAlchemy, etc. force=True
    # replaces any handlers a prior call already installed on the root
    # logger, for the same reason as app.logger above -- plain
    # basicConfig() is a no-op once the root logger already has any
    # handlers, which would otherwise leave root pointing at a PRIOR
    # call's (possibly already-rotated-away) file handler forever.
    logging.basicConfig(level=log_level, handlers=[console_handler, file_handler], force=True)


def _lightweight_migrate(app):
    """Best-effort schema patcher: add new columns that the model
    declares but the existing SQLite/Postgres table is missing.

    This avoids forcing every sysop to run alembic for tiny additive
    migrations like adding a boolean flag. Failures are logged and
    swallowed — a real migration with downgrades is still recommended
    for destructive changes.
    """
    import sqlalchemy as _sa
    from sqlalchemy import inspect as _inspect

    engine = db.engine
    insp = _inspect(engine)

    def _ensure_column(table, col_name, ddl):
        try:
            existing = {c['name'] for c in insp.get_columns(table)}
            if col_name in existing:
                return
            with engine.begin() as conn:
                conn.execute(_sa.text(
                    f'ALTER TABLE {table} ADD COLUMN {col_name} {ddl}'))
            app.logger.info(f'Added missing column {table}.{col_name}')
        except Exception as exc:
            app.logger.warning(
                f'Could not add column {table}.{col_name}: {exc}')

    def _ensure_index(table, index_name, col_name):
        """Mirrors _ensure_column above, but for an index on an EXISTING
        column -- create_all() only creates indexes declared on a model
        when it's creating the table itself for the first time; it does
        not retroactively add a newly-declared index=True to a table
        that already exists on an upgraded install. Real gap found live:
        NetmailMessage.received_at gained index=True in v1.0b2.145 (a
        content-based dedup query added in v1.0b2.143 filters on it),
        but every already-installed sysop's database needed this
        explicit backfill to actually get the index -- without it, that
        query silently kept doing an unindexed scan after every upgrade.
        """
        try:
            existing = {i['name'] for i in insp.get_indexes(table)}
            if index_name in existing:
                return
            with engine.begin() as conn:
                conn.execute(_sa.text(
                    f'CREATE INDEX {index_name} ON {table} ({col_name})'))
            app.logger.info(f'Added missing index {index_name} on {table}.{col_name}')
        except Exception as exc:
            app.logger.warning(
                f'Could not add index {index_name} on {table}.{col_name}: {exc}')

    # Posts: pinning + locking
    _ensure_column('posts', 'is_pinned',
                   'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('posts', 'is_locked',
                   'BOOLEAN NOT NULL DEFAULT 0')
    # Users: per-kind notification prefs (JSON text).
    _ensure_column('users', 'notify_prefs', 'TEXT')
    # Users: numeric access level + codepage + language preference.
    _ensure_column('users', 'access_level', 'INTEGER NOT NULL DEFAULT 10')
    _ensure_column('users', 'codepage', "VARCHAR(8) DEFAULT 'cp437'")
    _ensure_column('users', 'language', "VARCHAR(8) DEFAULT 'en'")
    # Boards: optional ANSI banner shown at top of board page.
    _ensure_column('boards', 'ansi_banner', 'TEXT')
    # Boards: sub-conference / category grouping.
    _ensure_column('boards', 'category', "VARCHAR(80) DEFAULT ''")
    # Users: NUV verification flag.
    _ensure_column('users', 'is_verified', 'BOOLEAN NOT NULL DEFAULT 1')
    # Users: sysop account-lock flag. Real bug found in a full audit --
    # this column never existed on old installs, so admin.py's "Lock
    # User" toggle silently never persisted anything.
    _ensure_column('users', 'is_locked', 'BOOLEAN NOT NULL DEFAULT 0')
    # Users: opt out of the public /watch live-activity page.
    _ensure_column('users', 'public_watch_optout', 'BOOLEAN NOT NULL DEFAULT 0')
    # Games: no-login guest play, off by default.
    _ensure_column('games', 'guest_playable', 'BOOLEAN NOT NULL DEFAULT 0')
    # User profile fields (added across releases; many old DBs missing them)
    _ensure_column('users', 'display_name', 'VARCHAR(100)')
    _ensure_column('users', 'bio', 'TEXT')
    _ensure_column('users', 'location', 'VARCHAR(100)')
    _ensure_column('users', 'website', 'VARCHAR(255)')
    _ensure_column('users', 'avatar_url', 'VARCHAR(500)')
    _ensure_column('users', 'avatar_upload', 'VARCHAR(255)')
    _ensure_column('users', 'signature', 'TEXT')
    _ensure_column('users', 'tagline', 'VARCHAR(160)')
    _ensure_column('users', 'date_of_birth', 'DATE')
    _ensure_column('users', 'show_email', 'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('users', 'theme_id', 'INTEGER')
    # FileUpload: is_public defaulted to True in Python but old rows have NULL.
    _ensure_column('file_uploads', 'is_public', 'BOOLEAN NOT NULL DEFAULT 1')
    try:
        with db.engine.connect() as _conn:
            _conn.execute(db.text(
                "UPDATE file_uploads SET is_public=1 WHERE is_public IS NULL"))
            _conn.commit()
    except Exception:
        pass
    # File areas: nodelist-source auto-import flag + which domain to tag with.
    _ensure_column('file_areas', 'is_nodelist_source',
                   'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('file_areas', 'nodelist_domain', 'VARCHAR(40)')
    # BbsDirectoryEntry — federation pull adds software/sysop/location
    # / port columns so anetbbs.lst entries are first-class alongside
    # sbbsimsg.lst entries.
    _ensure_column('bbs_directory', 'sysop', 'VARCHAR(120)')
    _ensure_column('bbs_directory', 'location', 'VARCHAR(120)')
    _ensure_column('bbs_directory', 'software', 'VARCHAR(40)')
    _ensure_column('bbs_directory', 'software_version', 'VARCHAR(40)')
    _ensure_column('bbs_directory', 'msp_port', 'INTEGER DEFAULT 18')
    _ensure_column('bbs_directory', 'systat_port', 'INTEGER DEFAULT 11')
    _ensure_column('bbs_directory', 'source',
                   "VARCHAR(20) DEFAULT 'sbbsimsg'")
    # ANetBBS federation registry — new table, no column additions
    # needed because the model defines it from scratch. db.create_all()
    # below handles creation on fresh installs; on upgrading installs
    # the metadata.create_all() pass at the end of this function picks
    # it up because we add it to the metadata via the import.
    # Echomail networks: QWK URL overrides for non-standard upstreams.
    _ensure_column('echomail_networks', 'qwk_download_url', 'VARCHAR(500)')
    # PeerBbs — extended for richer local BBS directory
    _ensure_column('peer_bbses', 'telnet_port', 'INTEGER DEFAULT 23')
    _ensure_column('peer_bbses', 'web_url', 'VARCHAR(400)')
    _ensure_column('peer_bbses', 'location', 'VARCHAR(120)')
    _ensure_column('peer_bbses', 'software', 'VARCHAR(80)')
    _ensure_column('peer_bbses', 'submitted_by_user_id', 'INTEGER')
    _ensure_column('peer_bbses', 'is_approved', 'BOOLEAN NOT NULL DEFAULT 1')
    _ensure_column('echomail_networks', 'qwk_upload_url', 'VARCHAR(500)')
    _ensure_column('echomail_networks', 'qwk_hub_id', 'VARCHAR(16)')
    # SBBSecho parity (v132): default-recipient + auth/transport options
    _ensure_column('echomail_networks', 'default_recipient', 'VARCHAR(100)')
    _ensure_column('echomail_networks', 'cram_md5',
                   'BOOLEAN NOT NULL DEFAULT 1')
    _ensure_column('echomail_networks', 'binkp_tls',
                   'BOOLEAN NOT NULL DEFAULT 0')
    # Optional short domain-suffix override -- see the field's own comment
    # in models.py. NULL for existing networks preserves current behavior.
    _ensure_column('echomail_networks', 'ftn_domain', 'VARCHAR(8)')
    # Per-network netmail flavor defaults (Crash/Hold/Direct) + FTS-0001
    # packet-level password -- see models.py's EchomailNetwork comment.
    _ensure_column('echomail_networks', 'default_crash', 'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('echomail_networks', 'default_hold', 'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('echomail_networks', 'default_direct', 'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('echomail_networks', 'packet_password', 'VARCHAR(20)')
    # Compress outbound .pkt bundles per peer -- see the two columns'
    # own model comments (ArcMail/FTS-0006 bundle-naming convention).
    _ensure_column('echomail_networks', 'compress_outbound', 'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('binkp_nodes', 'compress_outbound', 'BOOLEAN NOT NULL DEFAULT 0')
    # WaZOO FREQ (FTS-0006) opt-in per file area -- see FileArea's own
    # comment and echomail/freq.py.
    _ensure_column('file_areas', 'freq_enabled', 'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('file_areas', 'freq_password', 'VARCHAR(80)')
    # Scheduled hub-initiated polling of downstream nodes (previously
    # manual-only via "Poll Now") -- see BinkPNode's own comment.
    _ensure_column('binkp_nodes', 'poll_interval_minutes', 'INTEGER')
    _ensure_column('binkp_nodes', 'last_poll_at', 'DATETIME')
    # Delivery flavor for file-echo/TIC distribution, mirroring
    # NetmailMessage.is_crash/is_hold -- see HatchQueue's own comment.
    _ensure_column('hatch_queue', 'is_crash', 'BOOLEAN NOT NULL DEFAULT 0')
    _ensure_column('hatch_queue', 'is_hold', 'BOOLEAN NOT NULL DEFAULT 0')
    # BadAreaLog now also records the 'unsubscribed' drop reason (area
    # exists locally but is_subscribed/is_active is False), not just
    # 'unknown' -- see models.py's BadAreaLog comment.
    _ensure_column('bad_area_log', 'reason', "VARCHAR(20) NOT NULL DEFAULT 'unknown'")
    # InterBBS score sharing: per-game opt-in, defaults on (needs an
    # explicit boolean default like the other flags above -- the
    # generic nullable-column auto-sweep below only synthesizes
    # permissive, no-default DDL).
    _ensure_column('games', 'share_scores_interbbs',
                   'BOOLEAN NOT NULL DEFAULT 1')
    # See _ensure_index's own docstring above -- create_all() does not
    # retroactively add a newly-declared index to an already-existing
    # table on an upgraded install.
    _ensure_index('netmail_messages', 'ix_netmail_messages_received_at',
                  'received_at')
    # Real perf bug found live: inject_online_count() below runs a
    # `last_seen >= five_min_ago` range query on EVERY page view from
    # every visitor site-wide -- was an unindexed full-table scan.
    _ensure_index('user_sessions', 'ix_user_sessions_last_seen',
                  'last_seen')

    # ------------------------------------------------------------------
    # Auto-sweep: any model column that the DB is missing — add it.
    # This covers all the per-release additions we keep forgetting to list.
    # We synthesize a permissive DDL (NULL-allowed, no default) so the
    # ALTER TABLE always succeeds; ORM defaults still take effect for
    # newly-inserted rows. Skips primary keys (those need create_all).
    # ------------------------------------------------------------------
    try:
        for table_obj in db.metadata.sorted_tables:
            try:
                existing = {c['name'] for c in insp.get_columns(table_obj.name)}
            except Exception:
                continue  # table not yet created
            for col in table_obj.columns:
                if col.name in existing or col.primary_key:
                    continue
                try:
                    type_sql = col.type.compile(engine.dialect)
                except Exception:
                    type_sql = 'TEXT'
                # Permissive: nullable + no default, so ALTER works on SQLite.
                ddl = type_sql
                _ensure_column(table_obj.name, col.name, ddl)
    except Exception as exc:
        app.logger.warning('Auto-column sweep failed: %s', exc)

    # InterBBS Wall/Last Callers: the auto-sweep above adds new nullable
    # columns but never indexes/constraints. remote_msg_id needs a real
    # unique index so the inbound-sync scheduled job can't double-insert
    # on a race between overlapping runs (nullable-safe -- SQLite and
    # Postgres both allow multiple NULLs under a unique index). Must run
    # AFTER the auto-sweep above, which is what actually creates the
    # column on an upgrading install.
    for _idx_table, _idx_col, _idx_name in (
            ('wall_posts', 'remote_msg_id', 'ix_wall_posts_remote_msg_id_unique'),
            ('caller_log', 'remote_msg_id', 'ix_caller_log_remote_msg_id_unique'),
            ('game_scores', 'remote_msg_id', 'ix_game_scores_remote_msg_id_unique')):
        try:
            if _idx_col not in {c['name'] for c in insp.get_columns(_idx_table)}:
                continue
            with engine.begin() as conn:
                conn.execute(_sa.text(
                    f'CREATE UNIQUE INDEX IF NOT EXISTS {_idx_name} '
                    f'ON {_idx_table} ({_idx_col})'))
        except Exception as exc:
            app.logger.warning('Could not create %s.%s unique index: %s',
                               _idx_table, _idx_col, exc)

    # Activity-log drill-down (models.UserActivity.caller_log_id) and
    # per-node echomail poll-log filtering (models.EchomailPollLog.node_id)
    # -- both FK columns the auto-sweep above adds automatically, but
    # index=True is never retroactive (see _ensure_index's own docstring).
    _ensure_index('user_activities', 'ix_user_activities_caller_log_id',
                  'caller_log_id')
    _ensure_index('echomail_poll_logs', 'ix_echomail_poll_logs_node_id',
                  'node_id')

    # Real bug found live: UserSession.user_id used to be unique=True --
    # a hard one-row-per-user constraint, so a second simultaneous
    # connection (e.g. SSH while already logged in on the web) found and
    # overwrote the FIRST connection's row instead of getting its own;
    # "who's online" only ever showed one of them. session_key (the
    # auto-sweep above already added the bare column) is the new
    # per-connection identity; drop the stale UNIQUE index on an
    # upgrading install -- confirmed via schema inspection this is a
    # separate `CREATE UNIQUE INDEX`, not an inline column constraint,
    # so a plain DROP INDEX is safe and needs no table rebuild -- then
    # let _ensure_index recreate a plain (non-unique) one under the
    # SAME name so fresh-install and upgraded-install schemas converge.
    # A fresh install never had the unique index (the model no longer
    # declares unique=True), so this is a no-op there.
    try:
        existing_idx = {i['name']: i for i in insp.get_indexes('user_sessions')}
        idx = existing_idx.get('ix_user_sessions_user_id')
        if idx and idx.get('unique'):
            # Drop + recreate in one connection/transaction rather than
            # dropping here and relying on _ensure_index's own
            # existence check below to notice and recreate it -- the
            # `insp` object shared across this whole function caches
            # reflection results from before the drop, so a later
            # get_indexes() call through it can still report the
            # just-dropped index as present, causing _ensure_index to
            # wrongly skip recreating it (confirmed live while testing
            # this migration).
            with engine.begin() as conn:
                conn.execute(_sa.text('DROP INDEX ix_user_sessions_user_id'))
                conn.execute(_sa.text(
                    'CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id)'))
            app.logger.info(
                'Replaced UNIQUE index on user_sessions.user_id with a plain '
                'one -- multiple simultaneous connections per user are now supported')
    except Exception as exc:
        app.logger.warning('Could not migrate ix_user_sessions_user_id: %s', exc)
    # No-op on an upgrading install (just handled above) or a fresh
    # install (create_all() already created the plain index since the
    # model no longer declares unique=True).
    _ensure_index('user_sessions', 'ix_user_sessions_user_id', 'user_id')
    _ensure_index('user_sessions', 'ix_user_sessions_session_key', 'session_key')

    # Ask Anet guru door: FTS5 search index over wiki_pages (SQLite only —
    # see anetbbs/guru/fts.py). Created empty here; fresh installs populate
    # it naturally when seed_initial_pages() inserts wiki rows later in
    # startup (fires the AFTER INSERT trigger), upgrading installs get a
    # one-time backfill inside ensure_fts_index() itself.
    try:
        from .guru.fts import ensure_fts_index
        ensure_fts_index(engine, _sa.text, app.logger)
    except Exception as exc:
        app.logger.warning('Could not create/sync guru FTS5 index: %s', exc)

    # Multi-hub-identity support (see models.HubIdentity): must run AFTER
    # the auto-sweep above, which is what actually adds the
    # hub_identity_id column to echomail_networks/binkp_nodes/qwk_nodes/
    # network_join_config/network_join_requests/qwk_node_requests on an
    # upgrading install.
    _seed_default_hub_identity(app, engine, _sa)
    # Must run AFTER _seed_default_hub_identity (needs hub_identity_id
    # backfilled) and AFTER the auto-sweep above (adds binkp_nodes.network_id).
    _backfill_binkp_node_network_id(app, engine, _sa)


def _seed_default_hub_identity(app, engine, _sa):
    """Idempotent one-time seed for multi-hub-identity support.

    hub_identity_id's Python-side default (models._default_hub_identity_id)
    needs exactly one HubIdentity row with is_default=True to resolve
    against, or every newly-constructed EchomailNetwork/BinkPNode/QWKNode/
    NetworkJoinConfig/NetworkJoinRequest/QWKNodeRequest would get
    hub_identity_id=None.
    Creates that row on first run (reading zone/net from the pre-existing
    NetworkJoinConfig singleton if a sysop had already customized it, else
    the zone=1200/net=1/hub_node=1/'ANotherNetwork' values this project
    has always hardcoded), then backfills every hub_identity_id IS NULL
    row left over from before the column existed -- ALTER TABLE ADD
    COLUMN always gives existing rows a literal NULL regardless of the
    ORM-side default, which only governs new INSERTs, not rows that
    already existed when the column was added.
    """
    from .models import HubIdentity, NetworkJoinConfig
    try:
        default_row = HubIdentity.query.filter_by(is_default=True).first()
        if default_row is None:
            # Raw query, not NetworkJoinConfig.get() -- .get() itself now
            # depends on a default HubIdentity existing, which is exactly
            # what we're in the middle of creating.
            legacy_cfg = NetworkJoinConfig.query.first()
            zone = (legacy_cfg.binkp_zone if legacy_cfg and legacy_cfg.binkp_zone else 1200)
            net = (legacy_cfg.binkp_net if legacy_cfg and legacy_cfg.binkp_net else 1)
            default_row = HubIdentity(
                name='ANotherNetwork', slug='anothernetwork',
                qwk_hub_id=os.environ.get('QWK_HUB_ID', '') or 'ANET',
                binkp_zone=zone, binkp_net=net, binkp_hub_node=1,
                # Starts inactive, matching the seeded ANotherNetwork
                # EchomailNetwork rows (web_app.py's _create_default_data,
                # both start is_active=False) -- turning on
                # REGISTRY_MODE_ENABLED alone must not make this identity
                # look live; the sysop activates it deliberately once
                # they've actually configured it as their real hub.
                is_active=False, is_default=True,
            )
            db.session.add(default_row)
            db.session.commit()
            app.logger.info(
                'Seeded default HubIdentity %r (zone=%s net=%s) for '
                'multi-hub-identity support', default_row.name, zone, net)

        default_id = default_row.id
        for _table in ('echomail_networks', 'binkp_nodes', 'qwk_nodes',
                       'network_join_config', 'network_join_requests',
                       'qwk_node_requests'):
            try:
                with engine.begin() as conn:
                    # _table is one of the fixed literal names in the
                    # tuple above, never request-influenced; SQL bind
                    # params can't parameterize identifiers, so f-string
                    # interpolation of a hardcoded name list is the
                    # correct approach here, not a shortcut.
                    conn.execute(_sa.text(
                        f'UPDATE {_table} SET hub_identity_id = :hid '  # nosec B608
                        f'WHERE hub_identity_id IS NULL'), {'hid': default_id})
            except Exception as exc:
                app.logger.warning(
                    'Could not backfill %s.hub_identity_id: %s', _table, exc)
    except Exception as exc:
        app.logger.warning('Could not seed default HubIdentity: %s', exc)


def _backfill_binkp_node_network_id(app, engine, _sa):
    """One-time backfill for BinkPNode.network_id on upgrading installs.

    Real bug found live: a HubIdentity can own more than one binkp
    EchomailNetwork row (a sysop can be hub of one network and a leaf
    member of several others, all under the one default identity) --
    inbound BinkP sessions used to resolve a downstream node's network
    purely from hub_identity_id, which is ambiguous in that case and
    silently misattributed real inbound mail/poll-log entries to whichever
    binkp network happened to sort first by id (see binkp_server.py and
    routing.self_hub_binkp_network). This backfills every existing
    BinkPNode row (network_id IS NULL, added fresh as NULL by the generic
    auto-sweep above) to the one binkp network under its hub identity
    where we're actually the hub -- the only case a downstream node could
    have legitimately polled in for. Rows where that's still ambiguous
    (zero or 2+ candidates) are left NULL; binkp_server.py's runtime
    fallback still applies to those, and a sysop can resolve one by hand
    via the node edit form.
    """
    from .echomail.routing import self_hub_binkp_network
    try:
        with engine.begin() as conn:
            node_rows = conn.execute(_sa.text(
                'SELECT id, hub_identity_id FROM binkp_nodes '
                'WHERE network_id IS NULL AND hub_identity_id IS NOT NULL')).fetchall()
        for node_id, hub_identity_id in node_rows:
            network = self_hub_binkp_network(hub_identity_id)
            if network is None:
                continue
            with engine.begin() as conn:
                conn.execute(_sa.text(
                    'UPDATE binkp_nodes SET network_id = :nid WHERE id = :id'),
                    {'nid': network.id, 'id': node_id})
            app.logger.info(
                'Backfilled binkp_nodes.network_id for node %s -> network %r',
                node_id, network.name)
    except Exception as exc:
        app.logger.warning('Could not backfill binkp_nodes.network_id: %s', exc)


def _create_default_data():
    """Create default boards and admin user if they don't exist"""
    from .models import Board, User, Game, GameCategory
    
    # Create default boards
    default_boards = [
        {'name': 'General Discussion', 'description': 'General topics and discussions', 'order': 1},
        {'name': 'Announcements', 'description': 'Important announcements and news', 'order': 2},
        {'name': 'Technical Support', 'description': 'Get help with technical issues', 'order': 3},
        {'name': 'Off-Topic', 'description': 'Off-topic discussions', 'order': 4},
    ]
    
    for board_data in default_boards:
        if not Board.query.filter_by(name=board_data['name']).first():
            board = Board(**board_data)
            db.session.add(board)
    
    # Create a fallback admin user only if NO admin account exists at all.
    # Real gap found in a full install/update re-verify audit: this used
    # to check specifically for a user named literally 'admin' -- but
    # install.sh's own wizard creates the sysop's chosen account (e.g.
    # "ANetBBS Sysop", from the SYSOP_NAME prompt) by calling
    # create_app() first, which runs this function BEFORE install.sh's
    # own explicit account-creation code even gets a chance to run. Since
    # the wizard's account is (almost) never literally named "admin",
    # every fresh install.sh install silently ended up with TWO
    # full-admin accounts: the sysop's chosen one, and this unlisted
    # fallback with a random password only ever shown in a log line /
    # data/admin_password.txt. Checking is_admin instead of the literal
    # username means any already-provisioned admin (by install.sh, by
    # Docker's entrypoint, or by hand) correctly suppresses this
    # fallback; only a truly bare `db.create_all()` with zero admins
    # (e.g. running the Flask app directly with no setup at all) still
    # gets one bootstrapped, same as before.
    if not User.query.filter_by(is_admin=True).first():
        # Generate a one-time random password instead of hardcoding 'admin123'.
        # Print + persist it so the sysop can find it on first install.
        # (secrets is imported at module level now — used again below for
        # the A-Net Game Server door seed's password. A local `import
        # secrets` here previously shadowed that module-level import for
        # this whole function's scope, since Python treats any name
        # assigned/imported anywhere in a function body as local to the
        # entire function — even on code paths that don't execute it.)
        from flask import current_app as _ca
        gen_pw = secrets.token_urlsafe(12)
        admin = User(
            username='admin',
            email='admin@anetbbs.local',
            is_admin=True
        )
        admin.set_password(gen_pw)
        db.session.add(admin)
        try:
            data_dir = _ca.config.get('DATA_DIR') or '.'
            os.makedirs(data_dir, exist_ok=True)
            pw_file = os.path.join(data_dir, 'admin_password.txt')
            with open(pw_file, 'w') as f:
                f.write(gen_pw + '\n')
            os.chmod(pw_file, 0o600)
            _ca.logger.warning(
                '=' * 60 + '\n'
                'INITIAL ADMIN ACCOUNT CREATED\n'
                f'  username: admin\n'
                f'  password: {gen_pw}\n'
                f'  also written to: {pw_file}\n'
                'CHANGE THIS PASSWORD on first login!\n'
                + '=' * 60)
        except Exception:
            _ca.logger.warning(
                'Initial admin password (CHANGE IT): %s', gen_pw)

    # Create default themes
    # Themes — each defines bg, primary (accent), text (body), text-muted, card.
    # Audited for readability: every theme passes WCAG AA on body text.
    default_themes = [
        {
            'name': 'modern-dark',
            'display_name': 'Modern Dark (Recommended)',
            'description': 'High-contrast dark theme — easy to read everywhere',
            'is_default': True,
            'css_variables': (
                '{"--theme-bg": "#1e1e2e", "--theme-bg-dark": "#11111b",'
                ' "--theme-primary": "#89b4fa", "--theme-primary-dark": "#74a4ea",'
                ' "--theme-text": "#cdd6f4", "--theme-text-muted": "#a6adc8",'
                ' "--theme-card-bg": "#252537", "--theme-input-bg": "#181825",'
                ' "--theme-input-focus": "#1e1e2e", "--theme-border": "#45475a"}'
            ),
        },
        {
            'name': 'classic-green',
            'display_name': 'Classic Green',
            'description': 'The classic BBS green-on-black terminal look',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#0a1810", "--theme-bg-dark": "#000000",'
                ' "--theme-primary": "#00ff00", "--theme-primary-dark": "#00cc00",'
                ' "--theme-text": "#c0ffc0", "--theme-text-muted": "#7faa7f",'
                ' "--theme-card-bg": "#0e1f15", "--theme-input-bg": "#000000",'
                ' "--theme-input-focus": "#001100", "--theme-border": "#2a4030"}'
            ),
        },
        {
            'name': 'amber-terminal',
            'display_name': 'Amber Terminal',
            'description': 'Warm amber on dark brown',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#1a1000", "--theme-bg-dark": "#0d0800",'
                ' "--theme-primary": "#FFB000", "--theme-primary-dark": "#cc8c00",'
                ' "--theme-text": "#ffd790", "--theme-text-muted": "#b08855",'
                ' "--theme-card-bg": "#1f1408", "--theme-input-bg": "#0d0800",'
                ' "--theme-input-focus": "#1a1000", "--theme-border": "#3a2a10"}'
            ),
        },
        {
            'name': 'blue-ice',
            'display_name': 'Blue Ice',
            'description': 'Cyan accent on deep navy',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#0a0a2a", "--theme-bg-dark": "#050518",'
                ' "--theme-primary": "#00BFFF", "--theme-primary-dark": "#0099cc",'
                ' "--theme-text": "#cce8ff", "--theme-text-muted": "#7ca8c4",'
                ' "--theme-card-bg": "#0d0e35", "--theme-input-bg": "#050518",'
                ' "--theme-input-focus": "#0a0a3a", "--theme-border": "#1a2050"}'
            ),
        },
        {
            'name': 'matrix',
            'display_name': 'Matrix',
            'description': 'Bright neon green on pure black with glow',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#000000", "--theme-bg-dark": "#000000",'
                ' "--theme-primary": "#39FF14", "--theme-primary-dark": "#2ecc11",'
                ' "--theme-text": "#a0ff90", "--theme-text-muted": "#608860",'
                ' "--theme-card-bg": "#050505", "--theme-input-bg": "#000000",'
                ' "--theme-input-focus": "#001100", "--theme-border": "#103010"}'
            ),
        },
        {
            'name': 'synthwave',
            'display_name': 'Synthwave',
            'description': 'Hot pink + cyan accents on dark purple',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#1a0030", "--theme-bg-dark": "#0d0020",'
                ' "--theme-primary": "#FF6EC7", "--theme-primary-dark": "#cc58a0",'
                ' "--theme-text": "#ffd0ee", "--theme-text-muted": "#a880a0",'
                ' "--theme-card-bg": "#22063b", "--theme-input-bg": "#0d0020",'
                ' "--theme-input-focus": "#200040", "--theme-border": "#3d1060"}'
            ),
        },
        {
            'name': 'paper-white',
            'display_name': 'Paper White (Light)',
            'description': 'Light theme for daytime use — easy on the eyes outdoors',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#fafafa", "--theme-bg-dark": "#ececec",'
                ' "--theme-primary": "#1a73e8", "--theme-primary-dark": "#1558b8",'
                ' "--theme-text": "#202124", "--theme-text-muted": "#5f6368",'
                ' "--theme-card-bg": "#ffffff", "--theme-input-bg": "#ffffff",'
                ' "--theme-input-focus": "#f0f7ff", "--theme-border": "#dadce0"}'
            ),
        },
        {
            'name': 'enhanced',
            'display_name': 'VOID SIGNAL ◈',
            'description': 'Triple neon (green + cyan + magenta) on pure black — scanlines, brand glitch, animated card borders',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#000000", "--theme-bg-dark": "#000000",'
                ' "--theme-primary": "#00ff88", "--theme-primary-dark": "#00cc66",'
                ' "--theme-text": "#d8ffe8", "--theme-text-muted": "#60aa80",'
                ' "--theme-card-bg": "#020a05", "--theme-input-bg": "#000a03",'
                ' "--theme-input-focus": "#01120a", "--theme-border": "#00ff88",'
                ' "--theme-stylesheet": "enhanced"}'
            ),
        },
        {
            'name': 'hackers',
            'display_name': 'HACKERS (1995) ◈',
            'description': 'Hack the Planet — neon violet + lime + cyan rave cyberpunk. For those who ride the information superhighway.',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#000000", "--theme-bg-dark": "#000000",'
                ' "--theme-primary": "#cc00ff", "--theme-primary-dark": "#9900cc",'
                ' "--theme-text": "#ffffff", "--theme-text-muted": "#bb88ff",'
                ' "--theme-card-bg": "#05000a", "--theme-input-bg": "#000000",'
                ' "--theme-input-focus": "#080010", "--theme-border": "#cc00ff",'
                ' "--theme-stylesheet": "hackers"}'
            ),
        },
        {
            'name': 'graphite-teal',
            'display_name': 'Graphite Teal',
            'description': 'A neutral graphite dark theme with a single refined teal accent and Manrope headings — built for long reading sessions.',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#161a1f", "--theme-bg-dark": "#0d0f12",'
                ' "--theme-primary": "#2dd4a7", "--theme-primary-dark": "#21a880",'
                ' "--theme-text": "#e7e9ea", "--theme-text-muted": "#9aa0a6",'
                ' "--theme-card-bg": "#1c2127", "--theme-input-bg": "#101317",'
                ' "--theme-input-focus": "#171b20", "--theme-border": "#2b3138",'
                ' "--theme-stylesheet": "graphite-teal"}'
            ),
        },
        {
            'name': 'ivory-editorial',
            'display_name': 'Ivory Editorial',
            'description': 'A warm cream daytime theme with a deep forest-teal accent and serif headlines — reads like a well-set publication.',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#faf6ee", "--theme-bg-dark": "#efe7d6",'
                ' "--theme-primary": "#1f4d3d", "--theme-primary-dark": "#163a2d",'
                ' "--theme-text": "#2b2620", "--theme-text-muted": "#6b6255",'
                ' "--theme-card-bg": "#ffffff", "--theme-input-bg": "#ffffff",'
                ' "--theme-input-focus": "#fbf3e3", "--theme-border": "#ded4c0",'
                ' "--theme-stylesheet": "ivory-editorial"}'
            ),
        },
        {
            'name': 'retro-web',
            'display_name': 'Retro Web \'99',
            'description': 'Tiled pinstripe background, beveled Windows-95 chrome, underlined blue/purple links, Times New Roman. Best viewed in Netscape Navigator 4.0.',
            'is_default': False,
            'css_variables': (
                '{"--theme-bg": "#c8d4e8", "--theme-bg-dark": "#000080",'
                ' "--theme-primary": "#0000ee", "--theme-primary-dark": "#000099",'
                ' "--theme-text": "#000000", "--theme-text-muted": "#555555",'
                ' "--theme-card-bg": "#ffffff", "--theme-input-bg": "#ffffff",'
                ' "--theme-input-focus": "#ffffcc", "--theme-border": "#808080",'
                ' "--theme-stylesheet": "retro-web"}'
            ),
        },
    ]

    for theme_data in default_themes:
        if not Theme.query.filter_by(name=theme_data['name']).first():
            theme = Theme(**theme_data)
            db.session.add(theme)

    db.session.commit()

    # Seed ANotherNetwork (Zone 1200) — ships with ANetBBS like Dove-Net
    # ships with Synchronet.  Two entries: BinkP + QWK (FTP).  Both start
    # inactive; sysop fills in their node address/packet-id and password to
    # activate.  All areas are shared between the two network entries via
    # the hub tosser.
    from .models import EchomailNetwork, EchoArea
    if not EchomailNetwork.query.filter_by(name='ANotherNetwork').first():
        ann_binkp = EchomailNetwork(
            name='ANotherNetwork',
            network_type='binkp',
            description=(
                'ANetBBS echomail network — BinkP transport (Zone 1200). '
                'Apply for a node number at bbs.a-net.fyi.'
            ),
            binkp_host='bbs.a-net.fyi',
            binkp_port=24554,
            hub_address='1200:1/1',
            ftn_domain='anet',
            is_active=False,
        )
        db.session.add(ann_binkp)

    if not EchomailNetwork.query.filter_by(name='ANotherNetwork (QWK)').first():
        ann_qwk = EchomailNetwork(
            name='ANotherNetwork (QWK)',
            network_type='qwk',
            description=(
                'ANetBBS echomail network — QWK/FTP transport. '
                'FTP to bbs.a-net.fyi with your assigned packet ID and password. '
                'Download ANET.qwk, upload <YOURID>.rep. '
                'Apply at bbs.a-net.fyi.'
            ),
            qwk_host='bbs.a-net.fyi',
            qwk_port=21,
            qwk_hub_id='ANET',
            qwk_download_url='ftp://bbs.a-net.fyi/{hub_id}.qwk',
            qwk_upload_url='ftp://bbs.a-net.fyi/{packet}.rep',
            is_active=False,
        )
        db.session.add(ann_qwk)

    db.session.flush()

    # 26 echo areas across 8 groups.  Seeded once; shared between BinkP and
    # QWK network entries via the hub tosser on bbs.a-net.fyi.  All start
    # is_subscribed=False so nothing polls until the sysop activates.
    _ANN_AREAS = [
        # (tag, name, description, category, sysop_only, order)
        ('ANN.GENERAL',   'General Discussion',
         'General topics for ANotherNetwork members',
         'General',   False,  0),
        ('ANN.INTRO',     'Introductions',
         'New to the network? Introduce yourself here',
         'General',   False,  1),
        ('ANN.HUMOR',     'Humor & Jokes',
         "Jokes, memes, and things that made you snort-laugh",
         'General',   False,  2),
        ('ANN.DEBATE',    'Friendly Debate',
         'Disagree? Do it civilly here — all topics welcome',
         'General',   False,  3),
        ('ANN.FEEDBACK',  'Network Feedback',
         'Suggestions, complaints, and kudos for ANotherNetwork',
         'General',   False,  4),

        ('ANN.TECH',      'Technology',
         'Gadgets, hardware, software — anything tech',
         'Technology', False, 10),
        ('ANN.LINUX',     'Linux & Open Source',
         'Linux, BSD, Unix, and the open-source world',
         'Technology', False, 11),
        ('ANN.SECURITY',  'Security & Privacy',
         'InfoSec, hacking, privacy tools, and CTFs',
         'Technology', False, 12),
        ('ANN.NET',       'Networking & Internet',
         'TCP/IP, protocols, ISPs, and internet history',
         'Technology', False, 13),

        ('ANN.BBS',       'BBS News & Discussion',
         'General BBS scene discussion, news, and nostalgia',
         'BBS Scene',  False, 20),
        ('ANN.BBSDEV',    'BBS Software Development',
         'Writing BBS software? Share code, ask questions',
         'BBS Scene',  False, 21),
        ('ANN.ANETBBS',   'ANetBBS Support',
         'Help, tips, and discussion specific to ANetBBS',
         'BBS Scene',  False, 22),
        ('ANN.DOORS',     'Door Games',
         'LORD, TradeWars, Barren Realms, and all the classics',
         'BBS Scene',  False, 23),
        ('ANN.ANSIART',   'ANSI / ASCII Art',
         'Share, critique, and discuss ANSI and ASCII art',
         'BBS Scene',  False, 24),

        ('ANN.RETRO',     'Retro Computing',
         'C64, Amiga, Apple II, DOS, and vintage hardware',
         'Retro',      False, 30),
        ('ANN.GAMES',     'Games & Gaming',
         'Video games, tabletop, arcade — past and present',
         'Retro',      False, 31),
        ('ANN.MUSIC',     'Music',
         'MODs, chiptunes, any genre — share what you\'re listening to',
         'Retro',      False, 32),

        ('ANN.MOVIES',    'Movies & TV',
         'Reviews, recommendations, and discussion',
         'Hobby',      False, 40),
        ('ANN.BOOKS',     'Books & Reading',
         'Fiction, non-fiction, technical books, zines',
         'Hobby',      False, 41),
        ('ANN.FOOD',      'Food & Cooking',
         'Recipes, restaurants, and food talk',
         'Hobby',      False, 42),
        ('ANN.SPORTS',    'Sports',
         'Sports, fitness, and outdoor activities',
         'Hobby',      False, 43),

        ('ANN.ADS',       'For Sale / Wanted / Trades',
         'Buy, sell, or trade — old hardware welcome',
         'Trading',    False, 50),

        ('ANN.DATA',      'Data & File Discussion',
         'Datasets, file requests, sharing links',
         'Data',       False, 60),

        ('ANN.SYSOP',     'SysOp Discussion',
         'Sysop-to-sysop — running a BBS, best practices',
         'SysOp',      True,  70),
        ('ANN.SYSOP.HELP','SysOp Help & Tips',
         'Questions and answers for BBS operators',
         'SysOp',      True,  71),

        ('ANN.TEST',      'Test Messages',
         'Post a test, get a reply — no real content required',
         'Test',       False, 80),
    ]

    # Find the network IDs to attach to (may have just been created above).
    _ann_binkp_row = EchomailNetwork.query.filter_by(name='ANotherNetwork').first()
    _ann_qwk_row   = EchomailNetwork.query.filter_by(name='ANotherNetwork (QWK)').first()

    # QWK cannot carry a symbolic tag like 'ANN.LINUX' -- MESSAGES.DAT
    # only ever stores a numeric conference number, end to end. The BinkP
    # side keeps the symbolic tag (FTN AREA: kludges are arbitrary
    # strings); the QWK side needs its own STABLE, fixed number per area,
    # the same for every downstream node, matching how a real QWK-net's
    # CONTROL.DAT is a shared catalog, not something reassigned per
    # subscriber. `order` is already unique per area in this list, so
    # `order + 1` (offset to keep conf 0 reserved for netmail/personal
    # mail) is used as that fixed number.
    #
    # Live-caught, not hypothetical: a sysop posting from a QWK-connected
    # node into one of these areas got a silent no-op -- the outbound
    # conf-number resolver couldn't parse 'ANN.LINUX' as a number, fell
    # back to conference 0, and the hub's REP importer has no subscription
    # ever registered at conf 0, so the message was dropped, not just
    # misfiled. This affected all 26 areas on every install that had ever
    # activated the QWK side of this network, not one area in isolation.
    existing_binkp_names = {
        a.name for a in EchoArea.query.filter_by(network_id=_ann_binkp_row.id).all()
    } if _ann_binkp_row else set()
    existing_qwk_by_name = {
        a.name: a for a in EchoArea.query.filter_by(network_id=_ann_qwk_row.id).all()
    } if _ann_qwk_row else {}

    for (_tag, _name, _desc, _cat, _sop, _ord) in _ANN_AREAS:
        _qwk_conf_tag = str(_ord + 1)
        if _ann_binkp_row and _name not in existing_binkp_names:
            db.session.add(EchoArea(
                network_id=_ann_binkp_row.id,
                tag=_tag,
                name=_name,
                description=_desc,
                category=_cat,
                is_active=True,
                is_subscribed=False,
                is_sysop_only=_sop,
                order=_ord,
            ))
        if _ann_qwk_row:
            _qwk_existing = existing_qwk_by_name.get(_name)
            if _qwk_existing is None:
                db.session.add(EchoArea(
                    network_id=_ann_qwk_row.id,
                    tag=_qwk_conf_tag,
                    name=_name,
                    description=_desc,
                    category=_cat,
                    is_active=True,
                    is_subscribed=False,
                    is_sysop_only=_sop,
                    order=_ord,
                ))
            elif _qwk_existing.tag != _qwk_conf_tag:
                # Self-heal an already-seeded install: this row was
                # created before this fix with the symbolic tag copied
                # from the BinkP side. Rename it to the correct fixed
                # conference number, and bring any existing subscriptions
                # in line so every node ends up on the same shared number.
                from flask import current_app as _ca3
                _ca3.logger.info(
                    "ANotherNetwork QWK area migration: %r tag %r -> %r",
                    _name, _qwk_existing.tag, _qwk_conf_tag)
                _qwk_existing.tag = _qwk_conf_tag
                from .models import QWKNodeLastSent
                (QWKNodeLastSent.query
                 .filter_by(echo_area_id=_qwk_existing.id)
                 .update({QWKNodeLastSent.conf_number: _ord + 1}))
    db.session.commit()

    # ANotherNetwork file areas (TIC file-echo distribution) — 9 areas,
    # same shared-between-BinkP-and-QWK pattern as the message areas
    # above. `is_nodelist_source` flags the one area whose inbound TICs
    # should auto-populate the Nodelist table.
    from .models import FileArea
    from flask import current_app as _ca2
    _ANN_FILE_AREAS = [
        # (tag, name, description, storage_slug, is_nodelist_source)
        ('ANN.FILES.NODELIST', 'Weekly Nodelists',
         'Weekly ANotherNetwork nodelists', 'annet_nodelist', True),
        ('ANN.FILES.INFOPACK', 'ANotherNetwork Infopacks',
         'Weekly/monthly ANotherNetwork infopacks', 'annet_infopack', False),
        ('ANN.FILES.BBSSOFT', 'BBS Software',
         'ANetBBS releases and other BBS software', 'annet_bbssoft', False),
        ('ANN.FILES.DOORS', 'Door Games & Utilities',
         'Door games and utilities for BBS use', 'annet_doors', False),
        ('ANN.FILES.EBOOKS', 'eBooks',
         'eBooks', 'annet_ebooks', False),
        ('ANN.FILES.LINUX', 'Linux / Open Source Files',
         'Linux and open-source files', 'annet_linux', False),
        ('ANN.FILES.RETRO', 'Retro Computing Files',
         'Retro computing files', 'annet_retro', False),
        ('ANN.FILES.ANSIART', 'ANSI / ASCII Art Collections',
         'ANSI and ASCII art collections', 'annet_ansiart', False),
        ('ANN.FILES.TEST', 'Testing Only',
         'Testing only', 'annet_test', False),
    ]
    existing_file_tags = {
        a.tag for a in FileArea.query.filter(
            FileArea.tag.like('ANN.FILES.%')).all()
    }
    # Unlike EchoArea, FileArea.tag has a database-level UNIQUE
    # constraint (it's the lookup key the TIC processor uses), so each
    # area can only belong to one network row -- not duplicated across
    # both like the message areas above. TIC file-echo distribution is
    # a BinkP-native mechanism, so these attach to the BinkP entry.
    if _ann_binkp_row is not None:
        for (_ftag, _fname, _fdesc, _fslug, _fnodelist) in _ANN_FILE_AREAS:
            if _ftag not in existing_file_tags:
                db.session.add(FileArea(
                    network_id=_ann_binkp_row.id,
                    tag=_ftag,
                    name=_fname,
                    description=_fdesc,
                    storage_path=os.path.join(
                        _ca2.config['DATA_DIR'], 'files', _fslug),
                    is_active=True,
                    is_subscribed=False,
                    is_nodelist_source=_fnodelist,
                    nodelist_domain='anothernetwork' if _fnodelist else None,
                ))
    db.session.commit()

    # Weekly nodelist generation -- only meaningful on the hub install
    # (REGISTRY_MODE_ENABLED); a peer install has no downstream BinkPNode
    # data of its own, so seeding this everywhere would just be noise.
    if _ca2.config.get('REGISTRY_MODE_ENABLED'):
        from .models import ScheduledEvent
        if not ScheduledEvent.query.filter_by(
                handler_key='hub_generate_nodelist').first():
            db.session.add(ScheduledEvent(
                name='ANotherNetwork: weekly nodelist',
                handler_key='hub_generate_nodelist',
                params_json='{}',
                schedule_json='{"kind": "weekly", "day": 6, "time": "05:00"}',
                is_enabled=True,
            ))
            db.session.commit()

    # Create default built-in web games. Self-corrects an EXISTING row's
    # routing-critical fields only (game_type/web_game_module) -- same
    # narrow scope the BUNDLED_DOORS loop below already uses for door
    # games, deliberately NOT touching name/description/category/icon/
    # sort_order so a sysop's own admin-UI customizations to those fields
    # survive a restart. Caught live: a slug that used to belong to a
    # DIFFERENT game_type in an older release (ANetDarkForces's slug was
    # 'darkforces'/builtin_python here before it got a real web entry and
    # the terminal door moved to 'darkforces-term') would otherwise
    # silently keep pointing at the old game forever on an in-place
    # upgrade -- "insert only if the slug doesn't exist yet" has no way
    # to notice the row's TYPE is now wrong for that slug.
    from .games.web_games import WEB_GAMES
    for game_data in WEB_GAMES:
        game = Game.query.filter_by(slug=game_data['slug']).first()
        if game is None:
            db.session.add(Game(
                name=game_data['name'],
                slug=game_data['slug'],
                description=game_data['description'],
                category=game_data['category'],
                icon=game_data.get('icon', 'bi-controller'),
                game_type='builtin_web',
                web_game_module=game_data['web_game_module'],
                sort_order=game_data.get('sort_order', 0),
                is_active=True,
                guest_playable=game_data.get('guest_playable', False),
            ))
        else:
            game.game_type = 'builtin_web'
            game.web_game_module = game_data['web_game_module']
    db.session.commit()

    # Pre-seed bundled door games. The binaries / scripts ship inside the
    # vendor/games/<slug>/ tree so a fresh install has working doors out
    # of the box without the sysop having to track down + configure them.
    # We only insert the DB row if the binary actually exists on disk —
    # otherwise the door would 500 when launched.
    #
    # Exception: the door_synchronet (JSON-RPC interbbs) entries below
    # are NOT shipped in the release tarball at all (see .gitignore —
    # anetbbs/games/sbbs_doors/<slug>/ for each one is deliberately
    # excluded). These are real, free, open-source Synchronet doors
    # from their own original authors, not ANetBBS's to redistribute —
    # the point of bundling them locally during development was to
    # prove the Synchronet compat shim (synchronet_compat.py) runs them
    # correctly, not to ship copies. A sysop who wants one of these
    # downloads it themselves and drops it at the documented path (see
    # docs/26-synchronet-json-rpc-doors.md); the exact same
    # must_exist-gated logic below then auto-detects and registers it
    # with zero extra code, identically to how a bundled door's own
    # binary being present/absent already works.
    from flask import current_app as _ca
    base_dir = _ca.config.get('BASE_DIR') or _ca.root_path
    vendor_dir = os.path.join(base_dir, '..', 'vendor', 'games')
    vendor_dir = os.path.normpath(vendor_dir)

    BUNDLED_DOORS = [
        {
            'name': 'BotWars',
            'slug': 'botwars',
            'description': 'Synchronet BotWars — multi-player hacking RPG. '
                           'Battle bots, deploy patches, run guilds.',
            'category': 'rpg',
            'icon': 'bi-cpu',
            'game_type': 'door_synchronet',
            'synchronet_script_path':
                os.path.join(vendor_dir, 'botwars', 'botwars.js'),
            'synchronet_exec_dir':
                os.path.join(vendor_dir, 'botwars'),
            'sort_order': 50,
            'must_exist': os.path.join(vendor_dir, 'botwars', 'botwars.js'),
        },
        {
            'name': 'ANetSIMS',
            'slug': 'anetsims',
            'description': 'ANetSIMS — original ANetBBS simulation door.',
            'category': 'other',
            'icon': 'bi-joystick',
            'game_type': 'door_native',
            'executable_path':
                os.path.join(vendor_dir, 'anetsims', 'anetsims'),
            'working_directory':
                os.path.join(vendor_dir, 'anetsims'),
            'drop_file_type': 'door32.sys',
            'drop_file_path':
                os.path.join(vendor_dir, 'anetsims', 'door32.sys'),
            'sort_order': 60,
            'must_exist': os.path.join(vendor_dir, 'anetsims', 'anetsims'),
        },
        {
            # anetbbs-cfg, the standalone curses full-screen sysop config
            # tool (setup.py console_scripts entry), made reachable from
            # a live terminal session's Sysop Menu (SSH only — see
            # bbs_ui.py's _sysop_menu()) instead of needing separate
            # real shell access. Registered as an ordinary door_native
            # Game row purely so it can reuse door_runner.py's
            # already-hardened PTY-bridging (launch_door_game /
            # play_door_game_telnet) rather than reimplementing PTY
            # fork/exec + I/O pumping + abort-key handling from scratch.
            # `is_active: False` (via `_active_default` below) keeps it
            # out of the normal player-facing games list on both web and
            # terminal — launch_door_game/play_door_game_telnet don't
            # check is_active themselves, so the Sysop Menu's own direct
            # call still works. No drop file — anetbbs-cfg reads the DB
            # directly (anetbbs.cfg.db_bootstrap), not a dropfile.
            'name': 'ANetBBS Config Tool',
            'slug': 'anetbbs-cfg',
            'description': 'Sysop-only full-screen config tool (boards, '
                           'echomail, users, games, system settings). '
                           'Launched from the terminal Sysop Menu over '
                           'SSH only.',
            'category': 'system',
            'icon': 'bi-gear',
            'game_type': 'door_native',
            'executable_path':
                os.path.join(os.path.dirname(sys.executable), 'anetbbs-cfg'),
            'working_directory': base_dir,
            'min_access_level': 255,
            'max_nodes': 1,
            'web_enabled': False,
            'sort_order': 900,
            '_active_default': False,
            'must_exist':
                os.path.join(os.path.dirname(sys.executable), 'anetbbs-cfg'),
        },
        {
            # Trade Wars 2002 — Synchronet's JS port by Deuce, bundled
            # native (no external rlogin game-server needed). State lives
            # in anetbbs/games/sbbs_doors/tw2/db/tw2.json via the
            # ANetBBS-specific file-backed json-client.js drop-in.
            # Auto-inits the universe on first launch.
            'name': 'Trade Wars 2002',
            'slug': 'tw2',
            'description': (
                'Trade Wars 2002 — classic 1986 space-trading game, '
                'Synchronet JS port. Runs natively under ANetBBS Node '
                'compat. Auto-initializes on first launch.'),
            'category': 'space',
            'icon': 'bi-rocket-takeoff',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'tw2', 'tw2.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'tw2'),
            'sort_order': 20,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'tw2', 'tw2.js'),
            '_active_default': True,
        },
        {
            # ANetCRAFT — built-in Python terminal game, ships with ANetBBS.
            # No external binary needed; must_exist points at the module file.
            'name': 'ANetCRAFT',
            'slug': 'anetcraft',
            'description': "ANetBBS's own Minecraft-inspired 2D survival game. "
                           "Mine blocks, craft tools, explore a procedurally "
                           "generated world with ores, caves, trees and water. "
                           "Runs natively in the terminal — SSH, telnet, or "
                           "the built-in web terminal. Each player gets their "
                           "own persistent world.",
            'category': 'adventure',
            'icon': 'bi-layers',
            'game_type': 'builtin_python',
            'web_game_module': 'anetbbs.features.anetcraft:launch_anetcraft',
            'sort_order': 1,
            'must_exist': os.path.join(
                _ca.root_path, 'features', 'anetcraft.py'),
            '_active_default': True,
        },
        # ANetDarkForces (Terminal Edition) is deliberately NOT in this
        # list right now -- pulled back out of the live product per a
        # live playtest report (readability/visual issues bad enough to
        # warrant offline iteration rather than shipping it half-baked).
        # The module (anetbbs/features/darkforces_term.py) and its own
        # test suite (tests/test_darkforces_term.py) are untouched and
        # still fully functional -- this is purely a registry/seeding
        # change so it stops appearing in the Game Center and native SSH/
        # telnet game listings. See the deactivation block right after
        # this loop for how an install that already seeded the row (e.g.
        # a prior release) gets it turned back off. The canvas/web edition
        # (slug 'darkforces', games/web_games.py) is untouched and stays
        # the only reachable way to play ANetDarkForces until the
        # terminal edition is ready to come back.
        {
            # LORD — the canonical door game, Synchronet's JS port.
            # The game files ship inside the anetbbs package itself
            # (anetbbs/games/sbbs_doors/lord/), so this seed always runs.
            # `must_exist` points at the entry script so the row is only
            # inserted if the LORD source actually shipped.
            'name': 'Legend of the Red Dragon',
            'slug': 'lord',
            'description': 'The 1989 classic by Seth Robinson — battle '
                           'forest monsters, woo Violet, slay the Red '
                           'Dragon. Synchronet JavaScript port, runs '
                           'natively under ANetBBS Node compat. Real '
                           'Synchronet jsexec also auto-detected if '
                           'present (set SBBS_JSEXEC to override).',
            'category': 'rpg',
            'icon': 'bi-fire',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'lord', 'lord.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'lord'),
            'sort_order': 10,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'lord', 'lord.js'),
            # Active by default — runs under Node via the compat shim.
            '_active_default': True,
        },
        {
            # Chicken Delivery — first door wired up to ANetBBS's new
            # Synchronet JSON-RPC (port 10088) interbbs client support
            # (anetbbs/games/jsonrpc_client.py +
            # anetbbs/games/sbbs_stubs/json-client.js — see that
            # shim's own docstring for the full compatibility-contract
            # story). The game ships its own server.ini pointed at
            # game.a-net-online.lol:10088 — StingRay's real live
            # Synchronet JSON hub, already serving other sysops'
            # installs — so this door's score-write/leaderboard-read
            # calls go straight to real shared cross-BBS data, not a
            # sandbox. Real Electronic Chicken Software door, source
            # unmodified from the vendor's own xtrn/ package.
            'name': 'Chicken Delivery',
            'slug': 'chickendelivery',
            'description': 'Platformer by Electronic Chicken Software — '
                           'guide the chicken through obstacle courses to '
                           'the door. Cross-BBS high score leaderboard via '
                           'Synchronet JSON-RPC interbbs.',
            'category': 'arcade',
            'icon': 'bi-controller',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'chickendelivery',
                'chickendelivery.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'chickendelivery'),
            'sort_order': 15,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'chickendelivery',
                'chickendelivery.js'),
            '_active_default': True,
        },
        {
            # Bubble Boggle — second door wired up to the Synchronet
            # JSON-RPC interbbs client (same compat contract as Chicken
            # Delivery above). Ships its own server.ini already pointed
            # at game.a-net-online.lol:10088. Real The BRoKEN BUBBLe
            # Software (Matt Johnson) door, source unmodified from the
            # vendor's own xtrn/ package. Entry point is boggle.js (not
            # game.js — boggle.js reads server.ini, opens the JSON-RPC
            # client, then load()s game.js itself).
            'name': 'Bubble Boggle',
            'slug': 'bublbogl',
            'description': 'Timed word-search puzzle by The BRoKEN BUBBLe '
                           'Software — find words on a letter grid before '
                           'the clock runs out. Cross-BBS monthly '
                           'leaderboard via Synchronet JSON-RPC interbbs.',
            'category': 'arcade',
            'icon': 'bi-grid-3x3-gap',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'bublbogl',
                'boggle.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'bublbogl'),
            'sort_order': 16,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'bublbogl',
                'boggle.js'),
            '_active_default': True,
        },
        {
            # Synchronetris — third door on the Synchronet JSON-RPC
            # interbbs client, and the first genuinely real-time
            # multiplayer one: its lobby shows other players' games
            # live and in-game syncs a shared piece queue between
            # players, both built entirely on subscribe()-driven push
            # updates. This is what motivated building real persistent-
            # connection support into the JSON-RPC client (see
            # jsonrpc_client.py's run_listen_session() and
            # json-client.js's ensureDaemon()) rather than the earlier
            # one-shot-per-call-only model Chicken Delivery/Bubble
            # Boggle never needed more than. Entry point is tetris.js
            # (reads server.ini, opens the JSON-RPC client, then
            # load()s lobby.js). Real door, source unmodified from the
            # vendor's own xtrn/ package.
            'name': 'Synchronetris',
            'slug': 'synchronetris',
            'description': 'Real-time multiplayer Tetris over Synchronet '
                           'JSON-RPC interbbs — live game lobby with chat, '
                           'synced piece queues between players.',
            'category': 'arcade',
            'icon': 'bi-grid-3x3',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'synchronetris',
                'tetris.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'synchronetris'),
            'sort_order': 17,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'synchronetris',
                'tetris.js'),
            '_active_default': True,
        },
        {
            # Jeopardized — fourth door on the Synchronet JSON-RPC
            # interbbs client, and the first to also need real outbound
            # HTTP (its own func.js checks answers against a Web API
            # via a real HTTPRequest call, not just JSON-RPC game
            # state). Needed two new pieces of compat-shim
            # infrastructure beyond what Chicken Delivery/Bubble
            # Boggle/Synchronetris required: an ANetBBS-authored
            # http.js replacement (same "keep the real Socket-free
            # logic, replace only the actual I/O" pattern as
            # json-client.js) and a minimal Socket stub (sockdefs.js,
            # a real vendored file loaded by anything network-adjacent,
            # unconditionally reads Socket.PF_INET etc at load time —
            # a real gap no earlier bundled door happened to trigger).
            # Entry point is jeopardized.js. Real door, source
            # unmodified from the vendor's own xtrn/ package.
            #
            # MsgBase is NOT wired up (func.js's notifySysop() sends
            # real netmail) — confirmed with Jerry that this doesn't
            # need to work; nothing on the actual play path calls it.
            # Full audit of all ~15 view files (board/clue/answer/
            # wager/round/menu/scoreboard/etc) done, and confirmed via
            # repeated automated PTY smoke tests against Jerry's real
            # live game.a-net-online.lol JSON-RPC server: a brand new
            # player can reach the menu, start a round, select a real
            # clue, submit an answer, get scored, and return to the
            # board with zero errors. Three real door-side/compat-shim
            # bugs found and fixed this way (getUser()/
            # getUserGameState() null-vs-undefined handling for a
            # first-time player, missing skipsp()/js.flatten_string()
            # globals). Confirmed working on real Pi3 hardware by
            # Jerry (v1.0.30) — "it worked great".
            'name': 'Jeopardized',
            'slug': 'jeopardized',
            'description': 'Trivia game show over Synchronet JSON-RPC '
                           'interbbs, with live rankings and a real '
                           'answer-checking Web API.',
            'category': 'trivia',
            'icon': 'bi-question-diamond',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'jeopardized',
                'jeopardized.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'jeopardized'),
            'sort_order': 18,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'jeopardized',
                'jeopardized.js'),
            '_active_default': True,
        },
        {
            # Gooble Gooble — 5th door on the Synchronet JSON-RPC
            # interbbs client, a real-time Pac-Man clone (echicken's
            # own door, same author as Chicken Delivery/Bubble Boggle/
            # Synchronetris/Jeopardized). Simpler than the others in
            # one respect (no HTTP, just JSONClient for a global
            # scoreboard) but the FIRST real-time-action door in this
            # family — continuous ghost movement via Sprite.Aerial's
            # constantmotion, not turn-based like the others. Entry
            # point is gooble.js. Reuses Frame/Sprite/Tree/JSONClient/
            # Timer infrastructure already proven by earlier doors in
            # this family — no new compat-shim gaps found by static
            # audit before bundling (unlike Jeopardized, which needed
            # real HTTP support). server.ini points at Jerry's real
            # live game.a-net-online.lol:10088, matching the other
            # JSON-RPC doors. commands.js/service.js excluded (real
            # server-side json-service hooks — score-list aggregation
            # and write-authorization — never load()'d by gooble.js
            # itself).
            #
            # Confirmed working on real Pi3 hardware by Jerry
            # ("both games worked!!!") alongside Synkroban, v1.0.32.
            'name': 'Gooble Gooble',
            'slug': 'gooble',
            'description': 'A real-time Pac-Man-style maze chase over '
                           'Synchronet JSON-RPC interbbs, with a live '
                           'cross-BBS high score list.',
            'category': 'arcade',
            'icon': 'bi-joystick',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'gooble',
                'gooble.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'gooble'),
            'sort_order': 19,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'gooble',
                'gooble.js'),
            '_active_default': True,
        },
        {
            # Synkroban — 6th door on the Synchronet JSON-RPC interbbs
            # client, a real Sokoban warehouse-puzzle clone (real ART
            # @ FATCATS BBS door, source unmodified from Jerry's own
            # curated /home/jerry/Desktop/xtrn/ upload; source header
            # carries a copyright notice permitting unmodified use plus
            # configuration-only edits — this project's usual "never
            # edit the door's own file, patch at load time instead"
            # policy applies here even more strictly than usual, and
            # the bundled synkroban.js is byte-identical to source).
            # 11 real level packs bundled (Sven, Microban, Learning
            # Sokoban, etc).
            #
            # Real portability bug found reading the source before
            # bundling: level loading hardcodes the author's own
            # absolute install path ("/sbbs/xtrn/synkroban/") instead
            # of using js.exec_dir — fixed via a load-time string-
            # substitution door-patch in _applyKnownDoorFixes
            # (synchronet_compat.py), NOT a file edit. No other compat-
            # shim gaps found — reuses only plain Frame + JSONClient,
            # no Sprite/Tree needed. server.ini already came pre-
            # pointed at Jerry's real live game.a-net-online.lol:10088
            # in the source upload.
            #
            # Confirmed working on real Pi3 hardware by Jerry
            # ("both games worked!!!") alongside Gooble Gooble, v1.0.32.
            'name': 'Synkroban',
            'slug': 'synkroban',
            'description': 'A Sokoban warehouse puzzle over Synchronet '
                           'JSON-RPC interbbs, with a live cross-BBS '
                           'scoreboard and 11 real level packs.',
            'category': 'puzzle',
            'icon': 'bi-box-seam',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'synkroban',
                'synkroban.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'synkroban'),
            'sort_order': 21,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'synkroban',
                'synkroban.js'),
            '_active_default': True,
        },
        {
            # Star Trek — 7th door on the Synchronet JSON-RPC interbbs
            # client, a real-time space-combat arcade game (echicken's
            # own door, same author as Chicken Delivery/Bubble Boggle/
            # Synchronetris/Jeopardized/Gooble Gooble). Single-file
            # door (startrek.js) — reuses Frame/Sprite/Layout/Timer/
            # JSONClient, all already proven infrastructure; this is
            # the first door to use layout.js (ship-selection tabs),
            # previously vendored+E4X-fixed proactively but never
            # actually exercised by a bundled door until now.
            #
            # 2 real bugs found reading the source before bundling,
            # both fixed via load-time door-patches in
            # _applyKnownDoorFixes (synchronet_compat.py), NOT file
            # edits:
            #   1. scoreBoard() checks `scores === undefined` to
            #      detect a brand-new scoreboard scope, but the real
            #      server returns JSON null for a missing key -- same
            #      bug class as Jeopardized's getUser(). Would crash
            #      on `scores.length` the first time ANYONE finishes a
            #      game against a fresh "STARTREK" scope.
            #   2. Real gap in the compat shim itself (not door-
            #      specific, fixed directly in console.getstr() rather
            #      than via a door-patch): setup() calls the real
            #      Synchronet 3-arg overload
            #      `console.getstr("USS ", 30, K_LINE|K_EDIT)` to
            #      pre-fill a ship-name prompt with "USS " -- the old
            #      2-arg-only implementation treated any non-numeric
            #      first argument as invalid and silently dropped the
            #      prefix (and the door's real intended maxlen).
            #      Independently confirmed as real Synchronet behavior
            #      (not a door bug) via a second real caller,
            #      sbbs_stubs/form.js.
            #
            # server.ini points at Jerry's real live
            # game.a-net-online.lol:10088, matching the other JSON-RPC
            # doors.
            #
            # Confirmed working on real Pi3 hardware by Jerry
            # ("star trek worked great too").
            'name': 'Star Trek',
            'slug': 'startrek',
            'description': 'Real-time space combat over Synchronet '
                           'JSON-RPC interbbs, with a live cross-BBS '
                           'high score list.',
            'category': 'space',
            'icon': 'bi-rocket-takeoff',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'startrek',
                'startrek.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'startrek'),
            'sort_order': 22,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'startrek',
                'startrek.js'),
            '_active_default': True,
        },
        {
            # Fat Fish — 8th door on the Synchronet JSON-RPC interbbs
            # client, a fishing simulation with real fish AI (fish are
            # independent objects that swim, feed, and choose depths
            # on their own — not random luck) and a live cross-BBS
            # leaderboard. Real "Art @ Fatcats BBS" door (same author
            # as Synkroban), source unmodified from Jerry's own curated
            # /home/jerry/Desktop/xtrn/ upload; source header carries a
            # copyright notice restricting modification, so — same as
            # Synkroban — no direct file edits, only load-time
            # door-patches if any bugs are ever found.
            #
            # First door to reach mapgenerator.js (real vendored
            # terrain-generation library, previously present in
            # sbbs_stubs/ but never exercised by any bundled door —
            # confirmed clean syntax, no E4X issues, before bundling).
            # No Sprite usage at all (unlike Gooble/Star Trek) — just
            # Frame + JSONClient + the map generator, all already
            # proven infrastructure. Full API audit (every console.*/
            # system.*/js.*/user.*/Frame.prototype.* call across all 6
            # of the door's own JS files) found zero compat-shim gaps
            # before bundling.
            #
            # server.ini came pre-configured in Jerry's own upload
            # already pointed at game.a-net-online.lol:10088.
            #
            # Confirmed working on real Pi3 hardware by Jerry
            # ("fatfish worked great") — including the frame.js
            # z-order fix (v1.0.36, see that BUNDLED_DOORS comment
            # history/memory) for the gray shop-panel box he first
            # reported.
            'name': 'Fat Fish',
            'slug': 'fatfish',
            'description': 'A fishing simulation over Synchronet '
                           'JSON-RPC interbbs — real fish AI, random '
                           'lake terrain, and a live cross-BBS '
                           'leaderboard.',
            'category': 'simulation',
            'icon': 'bi-water',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'fatfish',
                'fatfish.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'fatfish'),
            'sort_order': 23,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'fatfish',
                'fatfish.js'),
            '_active_default': True,
        },
        {
            # Dice Warz ][ — 9th door on the Synchronet JSON-RPC
            # interbbs client, a real "Dice Wars"-style turn-based
            # territory-conquest strategy game (Risk-like: roll dice
            # against neighboring tiles to conquer the map), 4-7
            # players, human vs. AI or human vs. human. Real Matt
            # Johnson / mcmlxxix door (same author as Bubble Boggle,
            # already bundled), source unmodified. Entry point is
            # dice2.js (-> game.js -> dicefunc.js/diceobj.js). First
            # door to reach inputline.js and json-chat.js (real
            # InterBBS in-game chat) — both already vendored, neither
            # previously exercised by a bundled door. No Sprite usage.
            # Full API audit (console.*/system.*/js.*/user.*/bbs.*/
            # Frame.prototype.*, including the less-common
            # console.down()/left()/right() relative-cursor-move
            # methods) found zero compat-shim gaps before bundling.
            #
            # ai.js and service.js are real server-side-only files
            # (service.js runs the actual game/AI-turn authority;
            # ai.js is only load()'d BY service.js, never by the
            # client) — excluded, matching every prior door's own
            # commands.js/service.js exclusion pattern. server.ini
            # came pre-configured in Jerry's own upload already
            # pointed at game.a-net-online.lol:10088.
            #
            # Confirmed working on real Pi3 hardware by Jerry after 2
            # real bugs found from his playtest report and fixed —
            # see the json-chat.js null-history bug and dicefunc.js
            # getTile() JSON-array-hole bug, v1.0.38.
            'name': 'Dice Warz ][',
            'slug': 'dicewarz2',
            'description': 'A Risk-like territory-conquest dice game '
                           'over Synchronet JSON-RPC interbbs — 4-7 '
                           'players, human or AI, with real InterBBS '
                           'chat.',
            'category': 'strategy',
            'icon': 'bi-dice-6',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'dicewarz2',
                'dice2.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'dicewarz2'),
            'sort_order': 24,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'dicewarz2',
                'dice2.js'),
            '_active_default': True,
        },
        {
            # Maze Race — 10th door on the Synchronet JSON-RPC
            # interbbs client, a real-time multiplayer maze-racing
            # game (a JS remake of Atari's "Maze Craze"). Real Matt
            # Johnson / mcmlxxix door (same author as Bubble Boggle/
            # Dice Warz ][, both already bundled and confirmed
            # working). Entry point is maze.js -> game.js ->
            # mazeobj.js/mazegen.js/menu.js. Reuses only already-
            # proven infrastructure (Frame/JSONChat/InputLine/Layout/
            # funclib, graphic.js for ambient color constants) — no
            # new vendored files needed. No Sprite usage.
            #
            # Full API audit found zero compat-shim gaps. mazegen.js's
            # own maze generator always explicitly initializes every
            # grid cell up front (no JS array holes possible), so this
            # door does NOT hit the JSON.stringify()-turns-holes-into-
            # null bug class found in Dice Warz ][ (v1.0.38).
            # mazeobj.js's own GameData already defensively guards
            # every client.read(...) result with `if(!x) x = {};` --
            # exactly the null-check pattern several OTHER doors this
            # session were missing and had to be patched for.
            #
            # service.js (real server-side game authority) is never
            # load()'d by the client -- excluded, matching every prior
            # door's own exclusion pattern. server.ini came pre-
            # configured in Jerry's own upload already pointed at
            # game.a-net-online.lol:10088.
            #
            # Confirmed working on real Pi3 hardware by Jerry
            # ("worked great!").
            'name': 'Maze Race',
            'slug': 'maze',
            'description': 'Real-time multiplayer maze racing over '
                           'Synchronet JSON-RPC interbbs — a JS '
                           'remake of Atari\'s Maze Craze.',
            'category': 'arcade',
            'icon': 'bi-signpost-split',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'maze',
                'maze.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'maze'),
            'sort_order': 25,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'maze',
                'maze.js'),
            '_active_default': True,
        },
        {
            # Thirstyville — echicken's café-owner economic simulation
            # (11th JSON-RPC door). Entry point is thirsty.js -> loads
            # sbbsdefs.js/json-client.js/frame.js/layout.js/tree.js,
            # then its own demographics.js/products.js/stock-items.js/
            # weather.js/player.js. game.ini came pre-configured in
            # Jerry's own upload already pointed at
            # game.a-net-online.lol:10088.
            #
            # Full source audit found 3 real, previously-unnoticed
            # compat-shim/door gaps, all fixed before this ever ran on
            # real hardware:
            #  1. `jsonClient.send({scope:"ADMIN",func:"TIME"});
            #     jsonClient.wait();` -- the real client's own
            #     low-level packet primitives -- didn't exist anywhere
            #     in json-client.js. Added generically (JSONRPCClient.
            #     raw() + a new RAW op + JS shim send()/wait()), not
            #     Thirstyville-specific -- any future door that needs a
            #     packet shape the higher-level methods don't cover can
            #     use it too. Verified directly against the real live
            #     server.
            #  2. Same null-vs-undefined bug class as Jeopardized's
            #     original crash (a JSON-RPC read/keys of a
            #     never-written key returns real JSON null, confirmed
            #     live against the real server for both READ and KEYS):
            #     three real crash sites, all door-patched --
            #     thirsty.js's gameSettings/playerKeys on first-ever
            #     game creation, player.js's getPlayer() (worse: no
            #     `||update` short-circuit, so EVERY new player's first
            #     join would crash), and stock-items.js's
            #     makeStockItems() (worst: no guard at ALL, crashes
            #     unconditionally for the very first player on a fresh
            #     install, every time).
            #  3. `md5_calc(str, hex)` -- a real Synchronet global this
            #     door calls at load time (player.js's very first line)
            #     -- was completely absent from the compat shim. No
            #     prior bundled door ever actually exercised it
            #     client-side. Implemented via Node's crypto module and
            #     added to the _registerGlobals() allowlist (the same
            #     "declaring it isn't enough" gotcha base64_encode/ctrl
            #     hit before it).
            'name': 'Thirstyville',
            'slug': 'thirsty',
            'description': 'Café-owner economic simulation over '
                           'Synchronet JSON-RPC interbbs — buy '
                           'ingredients, brew drinks, and compete on a '
                           'shared cross-BBS market.',
            'category': 'strategy',
            'icon': 'bi-cup-hot',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'thirsty',
                'thirsty.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'thirsty'),
            'sort_order': 26,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'thirsty',
                'thirsty.js'),
            # Confirmed working on real Pi3 hardware by Jerry
            # ("works now!" — after the v1.0.45 getstr K_EDIT fix).
            '_active_default': True,
        },
        {
            # Good Time Trivia — 12th JSON-RPC door, Eric Oulashin's
            # (Nightfox) real trivia game. Entry point is gttrivia.js,
            # a flat text-console door (no Frame/Layout/Tree at all —
            # much smaller compat-shim surface than most doors this
            # batch). Ships with its own qa/ trivia question files
            # (~8MB, real question banks) and its default
            # gttrivia.ini already pointed at the AUTHOR's own real
            # public score-sharing hub (digitaldistortionbbs.com:10088,
            # "Digital Distortion" BBS) rather than StingRay's own
            # server — confirmed with Jerry to leave it as-is rather
            # than repoint it, per his explicit "whatever server each
            # door's ini already has stays as-is" rule for this whole
            # remaining batch. Live-queried that real remote directly
            # before shipping: it already has real historical player
            # data (including the author's own scores).
            #
            # 2 real, previously-unknown compat-shim gaps found by
            # source audit before ever running:
            #  1. `user.is_sysop` (real Synchronet property,
            #     js_user.cpp, `security.level >= 90`) was completely
            #     missing from the shim's `user` object -- with it
            #     undefined, `doSysopMenu()`'s own `if (!user.is_sysop)
            #     return;` silently locked the real sysop out of the
            #     admin menu (clear scores, remove a player/BBS from
            #     the shared scoreboard) no matter who was logged in.
            #     Fixed generically, not door-specific.
            #  2. gttrivia.js's own read-result sanity checks use
            #     `typeof(data) === "object"`, which doesn't actually
            #     exclude `null` (a well-known JS quirk) -- the
            #     established null-vs-undefined bug class from earlier
            #     doors, but every occurrence here is already wrapped
            #     in a try/catch (this door is unusually well-defended),
            #     so the real impact was a confusing on-screen JS error
            #     message rather than a hard crash. Widened defensively.
            # `MsgBase` (real Synchronet message-base API) is completely
            # absent from the shim -- gttrivia.js uses it for an
            # optional score-sharing-via-message-subboard feature, but
            # that's gated behind `scoresMsgSubBoardsForPosting`/
            # `scoresMsgSubBoardsForReading`, both empty by default (its
            # own shipped .ini) -- not reachable with default config,
            # left unimplemented rather than building it speculatively.
            'name': 'Good Time Trivia',
            'slug': 'gttrivia',
            'description': 'Trivia game with multiple categories and '
                           'inter-BBS high scores.',
            'category': 'trivia',
            'icon': 'bi-question-circle',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'gttrivia',
                'gttrivia.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'gttrivia'),
            'sort_order': 27,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'gttrivia',
                'gttrivia.js'),
            # Confirmed working on real Pi3 hardware by Jerry
            # ("they all works :)" — covering all 5 doors this round).
            '_active_default': True,
        },
        {
            # Lemons — 13th JSON-RPC door, echicken's real "Lemmings"-
            # style puzzle game (little lemon sprites, not @ signs, per
            # the door's own readme.txt origin story). Entry point is
            # lemons.js, loads sbbsdefs.js/frame.js/tree.js/
            # event-timer.js/json-client.js/sprite.js plus its own
            # defs.js/game.js/level.js/menu.js/help.js/dbhelper.js.
            # server.ini already came pre-configured in Jerry's own
            # upload pointed at game.a-net-online.lol:10088 — left
            # as-is per his rule for this whole batch. Live-queried
            # that real remote before shipping: it already has real
            # level packs (including the author's own original "Lemon
            # Party" level) and real score history from multiple real
            # BBSes.
            #
            # dbhelper.js (the JSON-RPC layer) is unusually well-
            # written — every read result is already correctly null-
            # AND-undefined-safe (`if(!player) return 0;` etc), unlike
            # several earlier doors this batch — so this door needed
            # no null-check door-patches at all.
            #
            # Zero compat-shim gaps needed fixing for this door. `Menu`/
            # `Game`/`Level`/`Help`/`PopUp` all turned out to be the
            # door's own local class definitions (matching their own
            # filenames, one per file, exactly like every other door
            # this batch) rather than missing library classes —
            # `PopUp` specifically also brings its own `Frame.prototype
            # .drawBorder` polyfill (lemons.js's own top-level code,
            # not a real Synchronet frame.js method either), so nothing
            # needed adding to the shared shim at all. (A `PopUp` +
            # `drawBorder` pair was briefly and mistakenly added to
            # this shim's frame.js during initial audit, based on a
            # too-shallow read of lemons.js that stopped at its load()
            # calls — reverted once the door's own definitions further
            # down the same file were found.)
            #
            # leveledit.js/leveleditor.js (a separate, standalone level-
            # editing tool -- never load()'d by lemons.js's own client
            # entry point, not mentioned anywhere in the door's own
            # installation docs as a separate program) are excluded,
            # matching the established "only bundle client-reachable
            # files" rule.
            'name': 'Lemons',
            'slug': 'lemons',
            'description': 'A "Lemmings"-style puzzle game over '
                           'Synchronet JSON-RPC interbbs — guide '
                           'lemon sprites to safety, with a shared '
                           'cross-BBS level library and scoreboard.',
            'category': 'puzzle',
            'icon': 'bi-signpost-2',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'lemons',
                'lemons.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'lemons'),
            'sort_order': 28,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'lemons',
                'lemons.js'),
            # Confirmed working on real Pi3 hardware by Jerry
            # ("they all works :)" — covering all 5 doors this round).
            '_active_default': True,
        },
        {
            # Star Stocks — 14th JSON-RPC door, Matt Johnson's real
            # galactic-investment strategy game (build outposts on
            # stars, merge/split companies, trade stock, live cross-BBS
            # scoreboard). Same author as Fat Fish/Dice Warz ][/Maze
            # Race/Uber Blox — consistently careful null/undefined
            # handling in his doors' own JSON-RPC code throughout this
            # batch, and this one is no exception (`if(!scores) scores
            # = {};` / `if(!currscore || ...)`, both correctly falsy-
            # safe already). Entry point is stars.js -> loads
            # sbbsdefs.js/funclib.js/graphic.js (the same rendering
            # library Bubble Boggle already proved) plus its own
            # game.js. server.ini already came pre-configured in
            # Jerry's own upload pointed at game.a-net-online.lol:10088
            # — left as-is per his rule for this whole batch (the
            # door's own install-xtrn.ini default is actually the
            # author's own bbs.thebrokenbubble.com).
            #
            # One real, previously-unknown compat-shim gap found by
            # source audit before ever running: `console.clearline()`
            # (a real Synchronet console method, distinct from the
            # already-supported `cleartoeol()` — clears the WHOLE
            # current line rather than just cursor-to-end) was
            # completely missing. Not an edge case: it's called in
            # `processSelection()`, the core "build an outpost on a
            # star" gameplay flow. Fixed generically (any door calling
            # it, not just this one), including the same bare-global
            # alias convention already established for cleartoeol().
            'name': 'Star Stocks',
            'slug': 'starstocks',
            'description': 'Galactic investment strategy over '
                           'Synchronet JSON-RPC interbbs — build '
                           'outposts, merge companies, trade stock, '
                           'with a shared cross-BBS scoreboard.',
            'category': 'strategy',
            'icon': 'bi-graph-up-arrow',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'starstocks',
                'stars.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'starstocks'),
            'sort_order': 29,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'starstocks',
                'stars.js'),
            # Confirmed working on real Pi3 hardware by Jerry
            # ("they all works :)" — covering all 5 doors this round).
            '_active_default': True,
        },
        {
            # DrugLord — 15th JSON-RPC door, a real "Dope Wars"-style
            # economic sim by art (Fatcats BBS). Entry point is
            # druglord.js, loads sbbsdefs.js plus its own ANSI.js/
            # atm.js/Drug.js/event.js/Location.js/pocket.js. Zero
            # compat-shim gaps found by source audit — every one of
            # this door's own null/undefined checks uses loose `==`/
            # `!=` (which correctly catches both null AND undefined in
            # one comparison), sidestepping the whole null-vs-undefined
            # bug class several earlier doors this batch hit via `===`.
            #
            # server.ini already came pre-configured pointed at
            # romulusbbs.com:10088 (matches the door's own hardcoded
            # druglord_config default exactly) — a real, thriving
            # third-party multi-BBS community hub, NOT StingRay's own
            # server. Left as-is per Jerry's explicit rule for this
            # whole batch. Live-queried that real remote before
            # shipping: real historical score data from a dozen-plus
            # real BBSes (FATCATS, RASPBERI, ROMULUS, BITSLAIR, TWIST,
            # and more).
            #
            # Research note for future audits: this door's own .js
            # files contain extended-ASCII bytes that make plain grep
            # silently treat them as binary (zero matches, no error) —
            # `grep -a` is required, or real content gets missed
            # entirely (hit this live auditing druglord.js itself: an
            # initial pass wrongly concluded the JSON-RPC scoreboard
            # feature wasn't actually implemented in this version,
            # before re-checking with -a).
            #
            # logos.ans (present in the source but never referenced by
            # any console.printfile() call) is excluded — unreachable.
            'name': 'DrugLord',
            'slug': 'druglord',
            'description': 'A "Dope Wars"-style economic sim over '
                           'Synchronet JSON-RPC interbbs — buy low, '
                           'sell high, dodge debt, with a shared '
                           'cross-BBS scoreboard.',
            'category': 'strategy',
            'icon': 'bi-capsule',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'druglord',
                'druglord.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'druglord'),
            # 30/31 are already used by the pre-existing DOOM/Duke3D
            # entries below (a different category) -- 32 avoids that
            # collision while continuing this batch's own sequence.
            'sort_order': 32,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'druglord',
                'druglord.js'),
            # Confirmed working on real Pi3 hardware by Jerry
            # ("they all works :)" — covering all 5 doors this round).
            '_active_default': True,
        },
        {
            # Uber Blox — 16th JSON-RPC door, Matt Johnson's real
            # block-clearing puzzle game (like GameHouse's "Super
            # Collapse", NOT a Tetris clone — an earlier, unverified
            # memory note calling it "redundant with Synchronetris"
            # was wrong, corrected during this batch's own triage
            # pass). Same author as Fat Fish/Dice Warz ][/Maze Race/
            # Star Stocks — same consistently careful null/undefined
            # handling throughout (`if(!this.players)
            # this.players={};` etc, already falsy-safe). Entry point
            # is blox.js -> loads json-client.js, then its own game.js
            # (which loads graphic.js/sbbsdefs.js/funclib.js, the same
            # rendering library Star Stocks and Bubble Boggle already
            # proved). server.ini already came pre-configured in
            # Jerry's own upload pointed at game.a-net-online.lol:10088
            # — left as-is per his rule for this whole batch.
            #
            # Zero real compat-shim gaps found -- `console.right()`
            # looked missing on first grep (used in the high-scores
            # column layout) but was a false alarm: it already exists
            # in synchronet_compat.py (`right: function(n){...}`,
            # alongside the already-present left/up/down) — the
            # earlier grep just searched for the wrong literal text
            # ("console.right" instead of the object-literal key
            # "right:"), not a real gap.
            'name': 'Uber Blox',
            'slug': 'uberblox',
            'description': 'Block-clearing puzzle strategy over '
                           'Synchronet JSON-RPC interbbs, with a '
                           'shared cross-BBS scoreboard.',
            'category': 'puzzle',
            'icon': 'bi-grid-3x3-gap',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'uberblox',
                'blox.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'uberblox'),
            'sort_order': 33,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'uberblox',
                'blox.js'),
            # Confirmed working on real Pi3 hardware by Jerry
            # ("they all works :)" — covering all 5 doors this round).
            '_active_default': True,
        },
        {
            # Synchronet Minesweeper — 17th door, but a DIFFERENT family
            # from the 16 above: this is Digital Man's (Rob Swindell's)
            # own real, official Synchronet door, not a JSON-RPC (port
            # 10088) game. Its only InterBBS feature (posting wins to a
            # shared "syncdata" DOVE-Net/FidoNet message area via a real
            # MsgBase) is auto-detected and gracefully self-disables on
            # a stock ANetBBS install (msg_area.sub is {}, so
            # syncdata.js's own find() correctly returns false) — the
            # core single-player game needs nothing beyond what's
            # already here. Excluded from the release tarball for the
            # same reason as the 16 JSON-RPC doors (see .gitignore) —
            # real, free, open-source software from its own original
            # author, not ANetBBS's to redistribute.
            #
            # Full audit found and fixed 4 real, general (not
            # Minesweeper-specific) compat-shim bugs in
            # synchronet_compat.py, all with their own regression tests
            # in tests/test_synchronet_compat_missing_globals.py:
            #  1. BG_HIGH (referenced on minesweeper.js's very first
            #     executable line) was undeclared anywhere reachable —
            #     immediate ReferenceError crash on launch. Root cause:
            #     two genuinely different real upstream cga_defs.js
            #     revisions are vendored at different paths (the
            #     dorkit/ copy calls the same bit BG_BRIGHT, not
            #     BG_HIGH), and load()'s path search prefers the dorkit
            #     copy. Fixed by registering BG_HIGH/BLINK directly.
            #  2. format()'s sprintf-style regex had no 'u' (unsigned
            #     decimal) conversion at all — every %u token in the
            #     game clock and every scoreboard column would have
            #     rendered as literal unexpanded text. Also fixed
            #     zero-pad width flags ("%02u") being silently treated
            #     as space-padding.
            #  3/4. file_getname()/file_exists()/directory() each had a
            #     SECOND, later, strictly-worse definition further down
            #     this file that silently shadowed the real one (same
            #     "duplicate keys shadow the originals" trap already
            #     known for object literals, just for top-level function
            #     redeclarations instead). Minesweeper's own top-level
            #     catch-all exception handler calls
            #     file_getname(e.fileName) — real under SpiderMonkey,
            #     undefined under V8/Node — which the shadowing
            #     duplicate crashed on instead of handling gracefully.
            #
            # console.creturn()/clear_hotspots()/getbyte()/status/
            # mouse_mode were all genuinely missing and added (see
            # synchronet_compat.py's console object) — status/mouse_mode
            # are inert bit-buckets (this shim never actually enables
            # real terminal-side xterm mouse tracking, so there's no
            # real mouse wire protocol for them to drive either way).
            # SyncTERM pixel-graphics mode (detect_graphics()'s APC
            # query/response dance) is gated behind a cterm_version this
            # shim intentionally reports as too old to reach — the game
            # gracefully falls back to its plain ANSI/PETSCII rendering,
            # matching how every other door here handles a capability
            # this shim doesn't implement.
            # 'minesweeper' (the obvious slug) is already taken by the
            # unrelated native browser-JS minigame seeded in
            # anetbbs/games/web_games.py -- confirmed live: using it
            # here collided with that existing row, and since the
            # self-correction loop below only touches game_type/
            # web_game_module/web_game_url on an EXISTING row (never
            # synchronet_script_path), the door's own row silently kept
            # synchronet_script_path=None forever. 'sbbs-minesweeper'
            # avoids the collision and doubles as a hint to sysops that
            # this is the real Synchronet door, not the built-in
            # minigame.
            'name': 'Minesweeper (Synchronet)',
            'slug': 'sbbs-minesweeper',
            'description': 'Synchronet\'s official Minesweeper, by '
                           'Digital Man — classic minefield-clearing '
                           'puzzle with personal-best tracking.',
            'category': 'puzzle',
            'icon': 'bi-flag',
            'game_type': 'door_synchronet',
            'synchronet_script_path': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'minesweeper',
                'minesweeper.js'),
            'synchronet_exec_dir': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'minesweeper'),
            'sort_order': 34,
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'sbbs_doors', 'minesweeper',
                'minesweeper.js'),
            # Not yet confirmed on real Pi3 hardware — off by default
            # pending that confirmation, matching every other door's own
            # rollout convention.
            '_active_default': False,
        },
        {
            # A-Net Game Server — StingRay's own rlogin door-game server
            # (game.a-net-online.lol), 450+ live games. The password
            # only needs to be hard for a random stranger to guess — the
            # remote server doesn't validate it against anything
            # specific — so a locally generated random string is all
            # that's needed, no coordination with the remote server
            # required. The BBS tag is a separate rlogin_bbs_tag field
            # (not folded into command_line_args -- see that field's
            # comment on the Game model for why) -- a random 4-letter
            # tag so two ANetBBS installs won't collide with each other
            # on the remote server by default; the sysop is free to
            # change it any time. Both are generated fresh only the
            # FIRST time this seed runs for an install: the update loop
            # below only re-syncs game_type/web_game_module/
            # web_game_url on existing rows, so these values are
            # computed on every boot but only actually used (and thus
            # only actually matter) the one time the row gets created —
            # they never overwrite an already-seeded password/tag on
            # subsequent boots.
            'name': 'A-Net Game Server',
            'slug': 'a-net-game-server',
            'description': 'A-Net Online Door Game Server — over 450 '
                           'live games, rlogin into a shared game hub.',
            'category': 'other',
            'icon': 'bi-controller',
            'game_type': 'door_rlogin',
            'executable_path': 'game.a-net-online.lol:513',
            'command_line_args': f'@USER@ {secrets.token_urlsafe(16)}',
            'rlogin_bbs_tag': ''.join(
                secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ') for _ in range(4)),
            'max_nodes': 20,
            'sort_order': 20,
            # No local game files to check for (it's a remote
            # connection) — door_runner.py always ships, so it's used
            # here as an always-true guard, matching the must_exist
            # convention every other bundled door already follows.
            'must_exist': os.path.join(
                _ca.root_path, 'games', 'door_runner.py'),
            '_active_default': True,
        },
        {
            'name': 'DOOM (Shareware)',
            'slug': 'doom',
            'description': 'The 1993 id Software classic — fight demons '
                           'on Mars. Shareware episode, plays in-browser '
                           'via EmulatorJS (no install required).',
            'category': 'action',
            'icon': 'bi-controller',
            'game_type': 'door_dos_browser',
            'web_game_url': '/games/dos-data/doom.zip',
            'sort_order': 30,
            'must_exist': os.path.join(base_dir, '..', 'data',
                                       'dos-games', 'doom.zip'),
            '_active_default': True,
        },
        {
            'name': 'Duke Nukem 3D (Shareware)',
            'slug': 'duke3d',
            'description': 'Duke Nukem 3D shareware episode by 3D Realms '
                           '(1996). Plays in-browser via EmulatorJS.',
            'category': 'action',
            'icon': 'bi-controller',
            'game_type': 'door_dos_browser',
            'web_game_url': '/games/dos-data/duke3d.zip',
            'sort_order': 31,
            'must_exist': os.path.join(base_dir, '..', 'data',
                                       'dos-games', 'duke3d.zip'),
            '_active_default': True,
        },
    ]
    for d in BUNDLED_DOORS:
        if not os.path.isfile(d['must_exist']):
            _ca.logger.info('Bundled door %s not found at %s — skipping',
                            d['slug'], d['must_exist'])
            continue
        kw = {k: v for k, v in d.items()
              if k not in ('must_exist', '_active_default')}
        kw['is_active'] = d.get('_active_default', True)
        # Every bundled door up to now has been single-player-per-node
        # locally (DOSBox/JS door instances), hence the flat default of
        # 1 -- but a remote multiplayer game server (rlogin) shouldn't be
        # capped at one simultaneous caller. Respect an explicit
        # max_nodes in the door's own dict if given.
        kw.setdefault('max_nodes', 1)
        existing = Game.query.filter_by(slug=d['slug']).first()
        if existing:
            # Keep key fields current so version upgrades self-correct
            for field in ('game_type', 'web_game_module', 'web_game_url'):
                if field in kw:
                    setattr(existing, field, kw[field])
        else:
            db.session.add(Game(**kw))
    db.session.commit()

    # Real live bug found (2026-09-01, reported by Jerry): the bundled
    # "A-Net Game Server" row (slug a-net-game-server, in BUNDLED_DOORS
    # above) is re-created here with a FRESH, never-coordinated random
    # password/tag whenever it's missing -- including right after a
    # sysop deletes it because they'd already configured their OWN
    # A-Net Game Server entry under a different slug before this
    # bundled row ever existed. The next service restart silently
    # resurrected it, active by default, which
    # anet_game_import.py's base_server_credentials() then either
    # flagged as a second, ambiguous candidate, or -- worse, if the
    # sysop's own entry were ever deactivated -- would have silently
    # used as THE candidate, applying throwaway credentials nobody
    # configured to every game the bulk-import tool creates. Self-
    # correct the same way the ANetDarkForces block below handles a
    # similar "shouldn't silently be active" case: if a DIFFERENT
    # active door_rlogin game is already pointed at the real A-Net
    # Online host, this bundled row must never be the active one.
    from .features.anet_game_import import _ANET_HOST_MARKER as _anet_host_marker
    _anet_bundled = Game.query.filter_by(slug='a-net-game-server').first()
    if _anet_bundled is not None and _anet_bundled.is_active:
        _other_active_anet = (
            Game.query
            .filter(Game.slug != 'a-net-game-server')
            .filter_by(game_type='door_rlogin', is_active=True)
            .filter(Game.executable_path.ilike(f'%{_anet_host_marker}%'))
            .first())
        if _other_active_anet is not None:
            _anet_bundled.is_active = False
            db.session.commit()

    # ANetDarkForces (Terminal) was removed from BUNDLED_DOORS above (see
    # its comment) -- so it's simply never seeded on a fresh install, but
    # an install that already has the row from a prior release (v1.0b2.
    # 168-173) would otherwise keep it active forever, since the self-
    # correction loop above only touches game_type/web_game_module/
    # web_game_url on an existing row, never is_active. Deactivate it
    # here instead of deleting it, so any GameScore/GameSession history
    # tied to it survives intact -- same reasoning delete_game() in
    # web/games_admin.py documents for why deletion (not deactivation)
    # is the destructive path. Remove this block once the terminal
    # edition is ready to ship again and BUNDLED_DOORS gets its entry
    # back (at which point a sysop's own admin-UI re-activation would
    # also stop getting reverted on every boot, which this block doesn't
    # try to distinguish from -- acceptable while this is a short-lived,
    # single-entry retirement rather than a general mechanism).
    _df_term = Game.query.filter_by(slug='darkforces-term').first()
    if _df_term is not None and _df_term.is_active:
        _df_term.is_active = False
        db.session.commit()

    # ─── Default RSS feeds ───────────────────────────────────────────
    # Seed at least one feed so a fresh install ships with something
    # to read. Sysop manages the rest at /admin/rss/. Idempotent —
    # we only add a feed if its URL isn't already registered.
    from .models import RssFeed
    DEFAULT_RSS_FEEDS = [
        {
            'name': 'X-News',
            'url': 'https://x-bit.org/rss/rss.xml',
            'site_url': 'https://x-bit.org',
            'description': 'X-Bit BBS scene news.',
            'category': 'scene',
            'sort_order': 10,
        },
    ]
    for feed_data in DEFAULT_RSS_FEEDS:
        if not RssFeed.query.filter_by(url=feed_data['url']).first():
            db.session.add(RssFeed(is_active=True, **feed_data))
    db.session.commit()

    # ─── Wiki seed pages ─────────────────────────────────────────────
    # Best-effort: fill the wiki with starter docs on a fresh install,
    # AND keep already-seeded installs' untouched pages current with
    # this version's content (sync_unedited=True) -- see
    # seed_initial_pages()'s own docstring for the real bug this fixes
    # (content fixes to SEED never reached any already-seeded install
    # before this). Never touches a page a sysop has actually edited.
    try:
        from .wiki.seed import seed_initial_pages
        seed_initial_pages(sync_unedited=True)
    except Exception as exc:  # pylint: disable=broad-except
        # Don't let a wiki seed failure stop the rest of startup —
        # log it and move on.
        try:
            from flask import current_app as _ca
            _ca.logger.warning('Wiki seed skipped: %s', exc)
        except Exception:
            pass

    # ─── Default game categories ─────────────────────────────────────
    # Idempotent: only inserts if the table is completely empty.
    if not GameCategory.query.first():
        defaults = [
            ('Action',      'action',   1),
            ('Classic DOS', 'classic',  2),
            ('Puzzle',      'puzzle',   3),
            ('RPG',         'rpg',      4),
            ('Space',       'space',    5),
            ('Strategy',    'strategy', 6),
            ('Other',       'other',    99),
        ]
        for name, slug, order in defaults:
            db.session.add(GameCategory(name=name, slug=slug, sort_order=order))
        db.session.commit()

    # ─── Default tagline pool ────────────────────────────────────────
    # Idempotent: only seeds if the table is completely empty, so a
    # sysop who's already added/removed their own entries never gets
    # them silently re-added on a later update.
    from .models import Tagline
    if not Tagline.query.first():
        try:
            taglines_path = os.path.join(
                os.path.dirname(__file__), 'data', 'default_taglines.txt')
            with open(taglines_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    db.session.add(Tagline(text=line[:200], is_active=True))
            db.session.commit()
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            try:
                from flask import current_app as _ca
                _ca.logger.warning('Tagline seed skipped: %s', exc)
            except Exception:
                pass


def main():
    """Main entry point for web application"""
    import sys as _sys
    if any(a in ('--version', '-V') for a in _sys.argv[1:]):
        from anetbbs.version import VERSION
        print(f'ANetBBS web {VERSION}')
        return

    app = create_app()
    
    # Get configuration
    config_name = os.environ.get('FLASK_ENV', 'development')

    host = app.config['WEB_HOST']
    port = app.config['WEB_PORT']
    debug = app.config['DEBUG']
    
    app.logger.info(f"Starting ANetBBS Web Server on {host}:{port}")
    app.logger.info(f"Environment: {config_name}")
    app.logger.info(f"Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
    
    # Run with SocketIO support
    socketio.run(app, host=host, port=port, debug=debug)


if __name__ == '__main__':
    main()
