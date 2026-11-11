"""Bind browser MRC sockets to their existing signed BBS login.

The bridge also serves cookie-less terminal/standalone clients. Their
existing transport trust model is unchanged; terminal sessions are revoked
by the BBS watchdog. No full Flask application or pollers are started here.
"""
import os
import time
from dataclasses import dataclass

from flask import Flask
from flask_login.utils import decode_cookie
from itsdangerous import BadData


class InvalidBBSCookie(ValueError):
    pass


def _cookie_app():
    from anetbbs.config import get_config
    app = Flask(__name__)
    app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
    return app


def _identifier_is_current(identifier):
    from anetbbs.core.user_manager import _Session
    from anetbbs.models import User
    try:
        user_id = int(identifier.partition(':')[0])
    except (TypeError, ValueError, AttributeError):
        return False
    with _Session() as session:
        user = session.get(User, user_id)
        return bool(
            user is not None and user.get_id() == identifier
            and user.is_active and not user.is_locked
            and (user.is_verified or user.is_admin)
        )


@dataclass(frozen=True)
class BBSCookieAuth:
    identifier: str
    expires_at: float | None = None

    def is_current(self):
        try:
            if self.expires_at is not None and time.time() > self.expires_at:
                return False
            return _identifier_is_current(self.identifier)
        except Exception:
            # An unavailable DB cannot grant continuing browser access.
            return False


def bind_bbs_cookie_auth(cookies):
    """Return a fixed browser login, None for unrelated/no cookies, or fail.

    Session and remember-cookie precedence mirrors Flask-Login: a current
    session is preferred, then a valid remember login can restore it.
    """
    if not cookies:
        return None
    app = _cookie_app()
    session_name = app.config['SESSION_COOKIE_NAME']
    remember_name = app.config.get('REMEMBER_COOKIE_NAME', 'remember_token')
    if session_name not in cookies and remember_name not in cookies:
        return None

    candidates = []
    session_data = {}
    if session_name in cookies:
        serializer = app.session_interface.get_signing_serializer(app)
        if serializer is not None:
            lifetime = int(app.permanent_session_lifetime.total_seconds())
            try:
                session_data, timestamp = serializer.loads(
                    cookies[session_name], max_age=lifetime, return_timestamp=True)
                identifier = session_data.get('_user_id')
                if identifier is not None:
                    candidates.append(BBSCookieAuth(
                        str(identifier), timestamp.timestamp() + lifetime))
            except (BadData, TypeError, ValueError, AttributeError):
                session_data = {}

    if remember_name in cookies and session_data.get('_remember') != 'clear':
        try:
            identifier = decode_cookie(cookies[remember_name], key=app.secret_key)
            if identifier is not None:
                candidates.append(BBSCookieAuth(identifier))
        except (TypeError, ValueError):
            pass

    for candidate in candidates:
        if candidate.is_current():
            return candidate
    raise InvalidBBSCookie('BBS login is invalid or expired')
