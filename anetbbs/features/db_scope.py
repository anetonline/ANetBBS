# anetbbs/features/db_scope.py
"""Shared helper for code that needs a Flask app context outside of a
real Flask request (terminal/SSH/rlogin sessions, door launches, etc.)
but can't just reuse one long-lived cached app the way
anetbbs/features/bbs_ui.py now does -- some callers (anetbbs/features/
games.py, anetbbs/games/door_runner.py) have tests that rely on getting
a genuinely fresh app+engine per call so they can freely point
SQLALCHEMY_DATABASE_URI at a different temp DB per test case; caching
would pin every test in a file to whichever one ran first.

Real incident, 2026-08-07: anetbbs/features/bbs_ui.py's _app() built a
fresh Flask app AND called db.init_app(app) -- registering a fresh
SQLAlchemy engine/connection pool -- on every call, never disposed.
session.py's sysop-kick watchdog called it every 5 seconds for the
entire lifetime of every logged-in session; over hours, with multiple
concurrent sessions, that leaked to 19.8GB RAM and 99.7% CPU on the
live server. Fixed there by caching (bbs_ui.py doesn't have the
per-test-temp-DB constraint the callers above do). For callers that DO
need a fresh app per call, use transient_app_context() below instead
of a bare `with app.app_context():` -- it disposes the engine on exit
so a fresh-app-per-call design doesn't also mean a leaked-engine-per-call.
"""
import contextlib


@contextlib.contextmanager
def transient_app_context(app):
    """Push *app*'s app context, and dispose its SQLAlchemy engine on
    exit (success or exception) -- use this instead of a bare
    `with app.app_context():` around any Flask app that's built fresh
    per call rather than cached. Disposal is best-effort: tests that
    mock db.init_app() to a no-op leave no engine registered at all,
    which would otherwise make db.engine raise instead of just being a
    no-op, so that's swallowed rather than propagated.
    """
    with app.app_context():
        try:
            yield
        finally:
            from anetbbs.models import db
            try:
                db.engine.dispose()
            except Exception:
                pass
