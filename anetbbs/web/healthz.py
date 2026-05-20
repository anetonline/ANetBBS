"""Lightweight health endpoint.

Public, unauthenticated, JSON. Used by:

* ``update.sh`` post-restart probe — confirms the web app didn't just
  start gunicorn but is actually serving requests with a working DB
  binding.
* External uptime monitoring (UptimeRobot, statuspage, etc.) — small
  body, fast response, no template render path.
* The federation hub's daily peer self-test (Task 40).

The endpoint NEVER exposes secrets — version, listener count, and DB
reachability only. Anyone scanning the internet can already see this
much via TCP probes; surfacing it as one JSON blob just makes it
easier for legitimate monitors.
"""
from __future__ import annotations

import logging
import os
import socket
import time

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from ..models import db
from ..version import VERSION


logger = logging.getLogger(__name__)
healthz_bp = Blueprint('healthz', __name__)


# Captured at import time. Survives across requests in the same worker;
# changes only when the worker recycles.
_BOOT_MONO = time.monotonic()
_BOOT_WALL = time.time()


def _db_ok() -> bool:
    """Quick `SELECT 1` against the configured DB. Catches a mis-
    configured connection string, a corrupted sqlite file, or a
    dropped postgres connection without forcing a full ORM round-trip."""
    try:
        db.session.execute(text('SELECT 1'))
        return True
    except Exception as exc:  # noqa: BLE001 — log + degrade, never crash /healthz
        logger.warning('healthz: db probe failed: %s', exc)
        return False


def _listener_states(cfg) -> dict:
    """Best-effort check that the headline ports are actually bound.

    We don't try to enumerate every listener — those are introspected by
    the Service Control Center. Here we just want a one-glance signal
    for the post-update probe + uptime monitors: is the web port open?
    is telnet open if it should be? etc.
    """
    candidates = [
        ('web',     cfg.get('WEB_PORT', 5000)),
        ('telnet',  cfg.get('TELNET_PORT', 23)),
        ('ssh',     cfg.get('SSH_PORT', 2222)),
        ('rlogin',  cfg.get('RLOGIN_PORT', 513)),
        ('ftp',     cfg.get('FTP_PORT', 2121)),
        ('finger',  cfg.get('FINGER_PORT', 79)),
    ]
    out = {}
    for name, port in candidates:
        port = int(port or 0)
        if not port:
            out[name] = 'disabled'
            continue
        # Trying to bind tells us a listener owns the port. We connect
        # instead — binding would race with the actual service.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                s.connect(('127.0.0.1', port))
            out[name] = 'open'
        except (socket.timeout, ConnectionRefusedError, OSError):
            out[name] = 'closed'
    return out


@healthz_bp.route('/healthz')
def healthz():
    """Return JSON describing the running BBS.

    Body:
        {
          "status": "ok"|"degraded",
          "version": "v1.0a2.43",
          "db_ok": true|false,
          "uptime_sec": <int seconds since this worker started>,
          "started_at": "<ISO-8601 UTC>",
          "listeners": {"web": "open", "telnet": "closed", ...},
          "host": "<server hostname>",
        }

    Status is "ok" when db_ok is true. Otherwise "degraded" with an
    explicit 503 so monitors flag the box.
    """
    cfg = current_app.config
    db_ok = _db_ok()
    listeners = _listener_states(cfg)
    body = {
        'status': 'ok' if db_ok else 'degraded',
        'version': VERSION,
        'db_ok': db_ok,
        'uptime_sec': int(time.monotonic() - _BOOT_MONO),
        'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                    time.gmtime(_BOOT_WALL)),
        'listeners': listeners,
        'host': socket.gethostname(),
    }
    return jsonify(body), 200 if db_ok else 503
