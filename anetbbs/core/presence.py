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
from datetime import datetime
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


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

    def __init__(self, user_id: int, protocol: str = 'telnet', peer: str = ''):
        self.user_id = user_id
        # Use the page field to encode the protocol since UserSession has no
        # dedicated protocol column. Web sessions set this to the request path
        # (e.g. /boards/), so a value like "[telnet]" is unambiguous.
        self.page = f'[{protocol}]'
        # Truncate peer to fit the ip_address column (45 chars for IPv6)
        self.peer = (peer or '')[:45]
        self.protocol = protocol
        self._row_id = None
        # Mark as online immediately
        self.heartbeat()

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
                    # Find or create one row per user
                    row = s.execute(
                        select(UserSession).where(UserSession.user_id == self.user_id)
                    ).scalar_one_or_none()
                    if row is None:
                        row = UserSession(
                            user_id=self.user_id,
                            ip_address=self.peer,
                            user_agent=f'BBS/{self.protocol}',
                            page=self.page,
                            last_seen=datetime.utcnow(),
                        )
                        s.add(row)
                        s.flush()
                        self._row_id = row.id
                    else:
                        row.last_seen = datetime.utcnow()
                        row.ip_address = self.peer
                        row.user_agent = f'BBS/{self.protocol}'
                        row.page = self.page
                        self._row_id = row.id
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
        """Mark the row as stale immediately (set last_seen to a long-ago time)
        so the web's 5-minute online window drops them right away."""
        try:
            from anetbbs.models import UserSession
            with _Session() as s:
                if self._row_id is not None:
                    s.execute(
                        update(UserSession)
                        .where(UserSession.id == self._row_id)
                        .values(last_seen=datetime(1970, 1, 1))
                    )
                    s.commit()
        except Exception as exc:
            logger.debug('presence disconnect failed: %s', exc)
