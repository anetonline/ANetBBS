"""
Cross-protocol presence tracker.

Telnet/SSH/rlogin sessions write to the same UserSession table the web app
uses, so the web's "online users" count + admin user-list reflects everyone
across all protocols.

Usage from BBSSession after a successful login:
    track = SessionPresence(user_id, protocol='telnet', peer='1.2.3.4:5678')
    track.heartbeat()       # call periodically while user is active
    track.disconnect()      # call on session close

This module talks to the SAME SQLite DB the Flask app uses (no Flask app
context required), via a module-level SQLAlchemy engine.
"""
import os
import logging
import threading
import uuid
from datetime import datetime
from sqlalchemy import create_engine, delete, update
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

_relay_thread = None
_relay_stop = threading.Event()
_RELAY_POLL_SEC = 2


def _resolve_db_uri() -> str:
    uri = os.environ.get('DATABASE_URL')
    if uri:
        return uri
    try:
        from anetbbs.config import get_config
        cfg = get_config(os.environ.get('FLASK_ENV', 'production'))
        return cfg.SQLALCHEMY_DATABASE_URI
    except Exception:
        return 'sqlite:///data/anetbbs.db'


_engine = create_engine(_resolve_db_uri(), future=True)
_Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)


class SessionPresence:
    """Tracks one telnet/SSH/rlogin user's presence in the shared UserSession table."""

    def __init__(self, user_id: int, protocol: str = 'telnet', peer: str = '',
                username: str = ''):
        self.user_id = user_id
        # Denormalized onto every PresenceEvent row this connection
        # writes (login now, logout later) so a poller never needs a
        # join just to render "X just logged in". Caller already has
        # this from the login flow that just authenticated the user.
        self.username = username
        # Use the page field to encode the protocol since UserSession has no
        # dedicated protocol column. Web sessions set this to the request path
        # (e.g. /boards/), so a value like "[telnet]" is unambiguous.
        self.page = f'[{protocol}]'
        # Truncate peer to fit the ip_address column (45 chars for IPv6)
        self.peer = (peer or '')[:45]
        self.protocol = protocol
        self._row_id = None
        # Per-CONNECTION identity (see UserSession.session_key's own
        # docstring) -- every SessionPresence instance is exactly one
        # connection, so this is fresh every time, never looked up.
        self._session_key = uuid.uuid4().hex
        # Mark as online immediately
        self.heartbeat()

    def _record_presence_event(self, s, kind):
        """Insert a PresenceEvent row on the SAME session/transaction
        the caller is already committing, so a login/logout row can
        never exist without the UserSession change it corresponds to
        (or vice versa). Best-effort: never let a presence-alert write
        fail the actual login/logout/heartbeat it's piggybacking on."""
        try:
            from anetbbs.models import PresenceEvent
            s.add(PresenceEvent(
                user_id=self.user_id, username=self.username or '?',
                kind=kind, protocol=self.protocol,
                created_at=datetime.utcnow()))
        except Exception as exc:
            logger.debug('presence event record (%s) failed: %s', kind, exc)

    def heartbeat(self):
        """Refresh last_seen — call periodically (e.g. on each menu loop)."""
        try:
            from anetbbs.models import UserSession
        except Exception as exc:
            logger.debug('UserSession import failed: %s', exc)
            return

        try:
            with _Session() as s:
                if self._row_id is None:
                    # Every SessionPresence instance represents exactly ONE
                    # connection -- always insert a fresh row rather than
                    # adopting whatever row already exists for this
                    # user_id. Real bug found live: user_id used to be
                    # UNIQUE, so a second simultaneous connection (e.g.
                    # SSH while already logged in on the web) found and
                    # overwrote the FIRST connection's row instead of
                    # getting its own -- "who's online" only ever showed
                    # one of the two.
                    row = UserSession(
                        user_id=self.user_id,
                        session_key=self._session_key,
                        ip_address=self.peer,
                        user_agent=f'BBS/{self.protocol}',
                        page=self.page,
                        last_seen=datetime.utcnow(),
                    )
                    s.add(row)
                    s.flush()
                    self._row_id = row.id
                    self._record_presence_event(s, 'login')
                else:
                    s.execute(
                        update(UserSession)
                        .where(UserSession.id == self._row_id)
                        .values(last_seen=datetime.utcnow(), page=self.page)
                    )
                s.commit()
        except Exception as exc:
            logger.debug('presence heartbeat failed: %s', exc)

    def set_page(self, page: str):
        """Update what the user is currently doing (e.g. 'chat', 'games:lord')."""
        self.page = f'[{self.protocol}] {page}'
        self.heartbeat()

    def disconnect(self):
        """Remove this connection's row entirely.

        Used to just mark the row stale (last_seen far in the past) so
        the web's 5-minute online window would drop it -- fine when
        user_id was unique and the row got reused/overwritten by this
        user's next connection anyway. Now that UserSession supports
        multiple rows per user (session_key), leaving a stale row
        around forever would make the table grow unbounded over a
        long-running install's lifetime instead of self-capping at one
        row per user. The row's whole purpose ends when this connection
        ends, so delete it outright. (Connections that never get a
        clean disconnect -- dropped carrier, killed process -- are
        caught by the events.handlers.cleanup_stale_sessions backstop
        instead.)"""
        try:
            from anetbbs.models import UserSession
            with _Session() as s:
                if self._row_id is not None:
                    s.execute(delete(UserSession).where(UserSession.id == self._row_id))
                    self._record_presence_event(s, 'logout')
                    s.commit()
        except Exception as exc:
            logger.debug('presence disconnect failed: %s', exc)


# ---------------------------------------------------------------------------
# Web-side relay: re-emit PresenceEvent rows over SocketIO.
#
# Terminal sessions (telnet/SSH/rlogin) poll PresenceEvent directly via
# their own per-session watchdog (core/session.py's
# _start_presence_alert_watchdog) since they aren't Flask/SocketIO
# clients. Browser tabs need this relay instead: telnet/SSH/rlogin run
# in a SEPARATE process (anetbbs.service) from the web app
# (anetbbs-web.service) in a real deployment, so a terminal-originated
# login has no other way to reach a connected browser socket than a
# poller running INSIDE the web process. Mirrors web/metrics.py's
# sampler thread shape.
# ---------------------------------------------------------------------------

def start_presence_alert_relay(app):
    """Spawn the relay thread. Idempotent."""
    global _relay_thread
    if _relay_thread is not None and _relay_thread.is_alive():
        return
    _relay_stop.clear()
    _relay_thread = threading.Thread(
        target=_relay_loop, args=(app,),
        name='presence-alert-relay', daemon=True)
    _relay_thread.start()
    logger.info('presence alert relay: started (poll=%ss)', _RELAY_POLL_SEC)


def stop_presence_alert_relay():
    _relay_stop.set()


def _relay_loop(app):
    last_id = 0
    try:
        with app.app_context():
            from anetbbs.models import PresenceEvent
            row = PresenceEvent.query.order_by(PresenceEvent.id.desc()).first()
            last_id = row.id if row else 0
    except Exception:
        logger.exception('presence alert relay: initial id lookup failed')

    while not _relay_stop.is_set():
        try:
            with app.app_context():
                from anetbbs.models import PresenceEvent
                events = (PresenceEvent.query
                         .filter(PresenceEvent.id > last_id)
                         .order_by(PresenceEvent.id.asc())
                         .limit(50).all())
                pending = [(e.id, e.user_id, e.username, e.kind) for e in events]
                if pending:
                    sio = app.extensions.get('socketio')
                    for eid, user_id, username, kind in pending:
                        last_id = max(last_id, eid)
                        if sio is not None:
                            try:
                                sio.emit('presence_alert', {
                                    'user_id': user_id, 'username': username,
                                    'kind': kind,
                                }, namespace='/')
                            except Exception:
                                logger.debug(
                                    'presence alert relay: emit failed',
                                    exc_info=True)
        except Exception:
            logger.exception('presence alert relay tick crashed; will retry')
        if _relay_stop.wait(_RELAY_POLL_SEC):
            break
