# anetbbs/core/activity_log.py
"""Shared, non-Flask-request-bound writer for models.UserActivity.

anetbbs/web/auth.py had its own `_log_activity()` for years, but it
reads `flask.request` directly -- fine for web routes, but unusable
from the asyncio terminal (telnet/SSH/rlogin/PETSCII) session code,
which has no request object at all. That's why activity logging was
never wired up outside of web auth flows: there was nowhere for
non-web callers to plug in. This module is the same insert/commit body
with the request-specific bits (ip_address, user_agent) taken as plain
arguments instead, so both sides can share one implementation. See
anetbbs/core/session.py's Session._log_activity() for the terminal-side
caller, and anetbbs/web/auth.py's _log_activity() (now a thin wrapper
around this) for the web side.
"""
import logging

logger = logging.getLogger(__name__)


def log_activity(user_id, activity_type, details=None, *, ip_address=None,
                 user_agent=None, service=None, caller_log_id=None):
    """Record a UserActivity row. Best-effort -- never raises. Must be
    called with a Flask app context already pushed (both callers
    already need one for their own DB access anyway)."""
    try:
        from ..models import db, UserActivity
        db.session.add(UserActivity(
            user_id=user_id,
            activity_type=activity_type,
            details=details,
            ip_address=ip_address,
            user_agent=(user_agent or '')[:255] or None,
            service=service,
            caller_log_id=caller_log_id,
        ))
        db.session.commit()
    except Exception:
        logger.debug('log_activity(%s, %s) failed', user_id, activity_type,
                     exc_info=True)
        try:
            from ..models import db
            db.session.rollback()
        except Exception:
            pass
