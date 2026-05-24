#!/usr/bin/env python3
"""
ANetBBS web server — eventlet native WSGI.

gunicorn --worker-class=eventlet is broken on Python 3.12 (fork()+greenlet
crash).  Flask-SocketIO docs recommend eventlet's built-in WSGI server.
"""
import eventlet
eventlet.monkey_patch()

# ── Python 3.12 greenlet-finalization safety patch ──────────────────────────
# eventlet.monkey_patch() replaces threading.get_ident with a greenlet-based
# version. During import-time GC and Python 3.12 logging cleanup, getcurrent()
# raises RuntimeError("greenlet is being finalized"). Wrap it to fall back to
# the real C thread ID during finalization — safe for cleanup paths.
try:
    import _thread as _real_thread
    import eventlet.green.thread as _evt_thread

    _orig_get_ident = _evt_thread.get_ident

    def _safe_get_ident(gr=None):
        try:
            return _orig_get_ident(gr) if gr is not None else _orig_get_ident()
        except RuntimeError:
            return _real_thread.get_ident()

    _evt_thread.get_ident = _safe_get_ident
    import threading as _threading
    _threading.get_ident = _safe_get_ident
except Exception as _patch_err:
    import sys as _sys
    print(f"[warn] greenlet safety patch failed: {_patch_err}", file=_sys.stderr)
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys

# ── Path / DB setup (mirrors the admin-setup heredoc in install.sh) ──────────
# deploy/serve.py lives at <INSTALL_DIR>/deploy/serve.py, so two levels up
# is the install root.  Insert it at the front of sys.path so that
# anetbbs.config's __file__-based BASE_DIR resolves to the source tree
# rather than to site-packages (which happens when pip used a non-editable
# install).  Also pin DATABASE_URL explicitly so the correct path is used
# regardless of install mode.
_INSTALL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _INSTALL_DIR not in sys.path:
    sys.path.insert(0, _INSTALL_DIR)

# Always override DATABASE_URL with an absolute path derived from serve.py's
# own location.  The .env file may contain a relative path or one with
# systemd EnvironmentFile quoting artefacts; computing it here is simpler and
# always correct.  Operators who deliberately want Postgres or a non-default
# SQLite path should set ANETBBS_DB_URL instead (checked below).
_db_url = (
    os.environ.get('ANETBBS_DB_URL')  # explicit override for Postgres / custom path
    or f'sqlite:///{_INSTALL_DIR}/data/anetbbs.db'
)
os.environ['DATABASE_URL'] = _db_url
# ─────────────────────────────────────────────────────────────────────────────

import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('anetbbs.serve')
logger.info("INSTALL_DIR: %s", _INSTALL_DIR)
logger.info("DATABASE_URL: %s", _db_url)

import pathlib as _pl
_db_path = _pl.Path(_INSTALL_DIR) / 'data' / 'anetbbs.db'
logger.info("DB file: exists=%s  parent_exists=%s  parent_writable=%s",
            _db_path.exists(), _db_path.parent.exists(),
            os.access(_db_path.parent, os.W_OK))
del _pl, _db_path

try:
    from anetbbs.web_app import create_app, socketio
    app = create_app('production')
except Exception:
    logger.exception("FATAL: create_app() failed — cannot start web server")
    sys.exit(1)

host = os.environ.get('WEB_BIND', '0.0.0.0')
port = int(os.environ.get('WEB_PORT', 5000))

logger.info("Starting ANetBBS web server on %s:%s", host, port)

try:
    socketio.run(app, host=host, port=port, use_reloader=False, log_output=True)
except Exception:
    logger.exception("FATAL: web server crashed")
    sys.exit(1)
