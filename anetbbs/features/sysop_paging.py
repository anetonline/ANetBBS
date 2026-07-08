# anetbbs/features/sysop_paging.py
"""
Sysop paging — telnet/SSH/rlogin user requests sysop attention.

Records a SysopPage row and emits a socketio toast to all sysop browser
tabs. Used from the BBSSession menu (e.g. as a 'page' menu action)."""
import logging
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .. import models

logger = logging.getLogger(__name__)


def _engine():
    """Lazy engine — uses the same DB the Flask app uses."""
    import os
    uri = os.environ.get('DATABASE_URL')
    if not uri:
        try:
            from anetbbs.config import get_config
            cfg = get_config(os.environ.get('FLASK_ENV', 'production'))
            uri = cfg.SQLALCHEMY_DATABASE_URI
        except Exception:
            uri = 'sqlite:///data/anetbbs.db'
    return create_engine(uri, future=True)


def page_sysop(user_id: int, message: str, service: str = 'telnet') -> int:
    """Record a sysop page and push a toast to web sysop tabs.

    Returns the new SysopPage row id, or 0 on failure."""
    eng = _engine()
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    page_id = 0
    try:
        with Session() as s:
            row = models.SysopPage(
                user_id=user_id, service=service,
                message=(message or '')[:2000],
                created_at=datetime.utcnow(),
            )
            s.add(row)
            s.commit()
            page_id = row.id
    except Exception:
        logger.exception('Failed to record sysop page')
        return 0

    # Best-effort webhook fire. _app() is a lightweight transient
    # Flask+SQLAlchemy context (anetbbs/features/bbs_ui.py) -- not the
    # full anetbbs.web_app module, so it doesn't carry the
    # eventlet.monkey_patch() risk the socketio block below is guarding
    # against; safe to call unconditionally from a terminal process
    # (core/session.py already does this same thing for CallerLog).
    try:
        from .bbs_ui import _app
        from .webhooks import fire
        with _app().app_context():
            user = models.User.query.get(user_id)
            fire('sysop_page', {'user': user.username if user else str(user_id),
                                'service': service, 'text': message or ''})
    except Exception:
        pass

    # Best-effort push via socketio so any sysop with a web tab open gets
    # a toast. We *only* try this when Flask-SocketIO is already loaded
    # in the current process — never import web_app from a BBS terminal
    # process, because eventlet.monkey_patch() at its top will corrupt
    # threading.Condition and break SQLAlchemy's connection pool.
    try:
        import sys as _sys
        if 'anetbbs.web_app' in _sys.modules:
            socketio = _sys.modules['anetbbs.web_app'].socketio
            socketio.emit('sysop_page', {
                'id': page_id,
                'user_id': user_id,
                'service': service,
                'message': message or '(no message)',
            }, namespace='/')
    except Exception:
        pass
    return page_id


# -----------------------------------------------------------------
# In-process notification queue: web-side admin can drop a one-line
# message in here and the next telnet/SSH/rlogin menu loop fetches +
# renders it. Process-local because telnet sessions and the Flask web
# process run in the SAME process (eventlet).
# -----------------------------------------------------------------

import collections
import threading

_inbox_lock = threading.Lock()
_inbox = collections.defaultdict(collections.deque)


def push_message(user_id: int, sender: str, text: str) -> None:
    """Drop a message for a logged-in user — picked up on their next menu loop."""
    if user_id is None:
        return
    with _inbox_lock:
        _inbox[user_id].append({'sender': sender, 'text': text})


def pop_messages(user_id: int):
    """Return + clear pending messages for `user_id`."""
    with _inbox_lock:
        d = _inbox.get(user_id)
        if not d:
            return []
        out = list(d)
        d.clear()
        return out
