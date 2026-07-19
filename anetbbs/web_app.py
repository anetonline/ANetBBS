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
import secrets
import logging
import json
from datetime import datetime
from flask import Flask, request, render_template
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_socketio import SocketIO
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


def create_app(config_name=None):
    """Application factory pattern"""
    app = Flask(__name__)
    
    # Load configuration
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')
    
    config_class = get_config(config_name)
    app.config.from_object(config_class)

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
    socketio.init_app(app, cors_allowed_origins="*", async_mode='eventlet',
                      ping_timeout=60, ping_interval=25,
                      logger=False, engineio_logger=False)
    csrf.init_app(app)
    
    # Configure login manager
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @app.before_request
    def track_user_session():
        """Track authenticated user sessions for online presence"""
        from flask_login import current_user
        from flask import request
        if current_user.is_authenticated:
            session = UserSession.query.filter_by(user_id=current_user.id).first()
            if session is None:
                session = UserSession(user_id=current_user.id)
                db.session.add(session)
            session.last_seen = datetime.utcnow()
            session.ip_address = request.remote_addr
            session.user_agent = request.user_agent.string[:255] if request.user_agent.string else None
            session.page = request.path
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
    def inject_online_count():
        from datetime import timedelta
        five_min_ago = datetime.utcnow() - timedelta(minutes=5)
        try:
            count = UserSession.query.filter(UserSession.last_seen >= five_min_ago).count()
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
        return Markup(''.join(out).replace('\n', '<br>'))

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
        return Markup(_ansi_to_html(_pipe_to_ansi(str(value))))

    @app.template_filter('strip_ansi')
    def strip_ansi_filter(value):
        """Strip ANSI escape sequences, returning plain text."""
        import re as _re
        from markupsafe import Markup, escape
        if not value:
            return Markup('')
        clean = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(value))
        return Markup(str(escape(clean)))

    @app.template_filter('markdown')
    def markdown_filter(value):
        """Render Markdown to safe HTML.

        Uses the `markdown` package if available, with sane defaults: hard line
        breaks (so users don't have to type two spaces at end of line), fenced
        code, tables. The output is run through an HTML cleaner via Markup
        only if `bleach` is also available — otherwise we fall back to a
        minimal escape-then-render which is still safe because we never
        allow embedded raw HTML in the source.
        """
        from markupsafe import Markup, escape
        if not value:
            return Markup('')
        try:
            import markdown as _md
            html = _md.markdown(
                str(value),
                extensions=['fenced_code', 'nl2br', 'tables'],
                output_format='html5',
            )
        except ImportError:
            # No markdown package — escape and just preserve newlines.
            html = '<p>' + str(escape(value)).replace('\n', '<br>') + '</p>'
        # Optional bleach pass to whitelist tags
        try:
            import bleach as _bleach
            allowed = _bleach.sanitizer.ALLOWED_TAGS | {
                'p', 'pre', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'}
            html = _bleach.clean(html, tags=allowed,
                                 attributes={'a': ['href', 'title'],
                                             'img': ['src', 'alt', 'title']},
                                 strip=True)
        except ImportError:
            pass
        return Markup(html)
    
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
    from .web.gallery import gallery_bp
    from .web.gallery_admin import gallery_admin_bp
    from .web.rss import rss_bp, redirect_bp
    from .web.rss_admin import rss_admin_bp
    from .web.menu_admin import menu_admin_bp
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
    from .web.upgrades import upgrades_api_bp, upgrades_admin_bp
    from .web.healthz import healthz_bp
    from .web.preflight import preflight_bp
    from .web.peer_health import peers_health_bp
    from .web.events_admin import events_admin_bp
    from .web.security_admin import security_bp
    from .web.door_errors import door_errors_bp
    from .web.backups_admin import backups_bp
    from .web.login_modules_admin import login_modules_admin_bp
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
    app.register_blueprint(gallery_bp)
    app.register_blueprint(gallery_admin_bp)
    app.register_blueprint(rss_bp)
    app.register_blueprint(rss_admin_bp)
    app.register_blueprint(redirect_bp)
    app.register_blueprint(menu_admin_bp)
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
    # Logon/logoff modules and graffiti wall admin.
    app.register_blueprint(login_modules_admin_bp)
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
    if app.config.get('ECHOMAIL_ENABLED', True) and not app.config.get('TESTING', False):
        from .echomail.poller import start_poller
        start_poller(app)

    # Start background RSS poller (refreshes feeds on RSS_POLL_INTERVAL,
    # default 30 min). No-op in TESTING mode.
    if not app.config.get('TESTING', False):
        try:
            from .rss.poller import start_poller as start_rss_poller
            start_rss_poller(app)
        except Exception:
            app.logger.exception('RSS poller failed to start')

    # Start the inter-BBS instant message (MSP / RFC 1312) listener
    if app.config.get('MSP_ENABLED', True) and not app.config.get('TESTING', False):
        from .msp.server import start_msp_server
        start_msp_server(app)

    # SYSTAT / ActiveUser UDP service (Synchronet IMSG companion to MSP)
    if app.config.get('SYSTAT_ENABLED', True) and not app.config.get('TESTING', False):
        from .msp.systat import start_systat_server
        start_systat_server(app)

    # Daily refresh of the inter-BBS directory (sbbsimsg.lst)
    if app.config.get('SBBSIMSG_AUTO_REFRESH', True) and not app.config.get('TESTING', False):
        from .msp.directory import start_refresher
        start_refresher(app)

    # Daily refresh of the ANetBBS federation directory (anetbbs.lst).
    # Independent of the registry-hub role: any install with a
    # REGISTRY_URL set will pull the upstream list so its users can see
    # peer ANetBBS systems in /imsg/directory/.
    if not app.config.get('TESTING', False):
        from .msp.anetbbs_directory import start_anetbbs_directory_refresher
        start_anetbbs_directory_refresher(app)

    # Federation hub — SYSTAT prober that keeps anetbbs.lst pruned of
    # dead peers. Only runs when this install is the central hub
    # (REGISTRY_MODE_ENABLED). No-op on peer installs.
    if app.config.get('REGISTRY_MODE_ENABLED') and not app.config.get('TESTING', False):
        from .msp.probe import start_probe_thread
        start_probe_thread(app)

    # Federation hub — seed + heartbeat the hub's OWN RegistryEntry so
    # /anetbbs.lst includes the hub itself. Without this, the hub's
    # own /imsg/directory shows no self-entry until a sysop creates
    # the row by hand. Pre-verified + pre-approved (the hub trusts
    # itself). No-op on peer installs.
    if app.config.get('REGISTRY_MODE_ENABLED') and not app.config.get('TESTING', False):
        from .msp.hub_self_register import start_hub_self_register_thread
        start_hub_self_register_thread(app)

    # Federation hub — daily self-test that fetches our own public
    # surfaces (anetbbs.lst, /api/releases/latest, /healthz) over the
    # configured REGISTRY_URL. Catches reverse-proxy regressions
    # before peers start complaining.
    if app.config.get('REGISTRY_MODE_ENABLED') and not app.config.get('TESTING', False):
        try:
            from .msp.hub_selftest import start_hub_selftest_thread
            start_hub_selftest_thread(app)
        except Exception:
            app.logger.exception('Hub selftest thread failed to start')

    # Service Control Center — per-PID CPU% / RSS / thread sampler that
    # feeds the live graphs at /admin/control/. Reads MainPID via
    # `systemctl show` and /proc via psutil; no privileges required.
    # Soft-no-ops if psutil isn't installed.
    if not app.config.get('TESTING', False):
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
    if not app.config.get('TESTING', False):
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
    if app.config.get('REGISTRY_SELF_REGISTER') and not app.config.get('TESTING', False):
        from .msp.registry_client import start_self_register_thread
        start_self_register_thread(app)

    return app


def _configure_logging(app):
    """Configure application logging"""
    log_level = getattr(logging, app.config['LOG_LEVEL'])
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    
    # File handler
    file_handler = logging.FileHandler(app.config['LOG_FILE'])
    file_handler.setLevel(log_level)
    file_handler.setFormatter(console_formatter)
    
    # Configure app logger
    app.logger.setLevel(log_level)
    app.logger.addHandler(console_handler)
    app.logger.addHandler(file_handler)
    
    # Also configure root logger for SQLAlchemy, etc.
    logging.basicConfig(level=log_level, handlers=[console_handler, file_handler])


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
                    conn.execute(_sa.text(
                        f'UPDATE {_table} SET hub_identity_id = :hid '
                        f'WHERE hub_identity_id IS NULL'), {'hid': default_id})
            except Exception as exc:
                app.logger.warning(
                    'Could not backfill %s.hub_identity_id: %s', _table, exc)
    except Exception as exc:
        app.logger.warning('Could not seed default HubIdentity: %s', exc)


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
    
    # Create default admin user if it doesn't exist
    if not User.query.filter_by(username='admin').first():
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

    # Create default built-in web games
    from .games.web_games import WEB_GAMES
    for game_data in WEB_GAMES:
        if not Game.query.filter_by(slug=game_data['slug']).first():
            game = Game(
                name=game_data['name'],
                slug=game_data['slug'],
                description=game_data['description'],
                category=game_data['category'],
                icon=game_data.get('icon', 'bi-controller'),
                game_type='builtin_web',
                web_game_module=game_data['web_game_module'],
                sort_order=game_data.get('sort_order', 0),
                is_active=True,
            )
            db.session.add(game)
    db.session.commit()

    # Pre-seed bundled door games. The binaries / scripts ship inside the
    # vendor/games/<slug>/ tree so a fresh install has working doors out
    # of the box without the sysop having to track down + configure them.
    # We only insert the DB row if the binary actually exists on disk —
    # otherwise the door would 500 when launched.
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
    # Best-effort: fill the wiki with starter docs on a fresh install.
    # Idempotent — only inserts pages whose slug doesn't already exist.
    try:
        from .wiki.seed import seed_initial_pages
        seed_initial_pages()
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
    config_class = get_config(config_name)
    
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
