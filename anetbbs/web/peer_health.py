"""Peer health page for federation hubs.

`/admin/registry/` is the approval-workflow lens (pending, approved,
rejected). This page is the *liveness* lens — for each listed peer:

* last-heartbeat (HTTP register/heartbeat POST received from the peer)
* last-probe (SYSTAT probe initiated by the hub)
* current consecutive failure count
* a "Probe now" button that fires a synchronous SYSTAT round-trip

Useful only on REGISTRY_MODE_ENABLED installs. On peers, the page
renders an explanatory message.
"""
from __future__ import annotations

import logging
import socket
import time
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, jsonify, render_template)
from flask_login import current_user, login_required

from ..models import db, RegistryEntry


logger = logging.getLogger(__name__)
peers_health_bp = Blueprint('peers_health', __name__,
                            url_prefix='/admin/peers')


def _require_admin():
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        from flask import abort
        abort(403)


def _relative_age(dt):
    if not dt:
        return 'never'
    delta = datetime.utcnow() - dt
    if delta < timedelta(minutes=2):
        return 'just now'
    if delta < timedelta(hours=1):
        return f'{int(delta.total_seconds() // 60)}m ago'
    if delta < timedelta(days=1):
        return f'{int(delta.total_seconds() // 3600)}h ago'
    return f'{delta.days}d ago'


def _systat_probe(host, port, timeout=3.0):
    """One-shot probe. TCP first (most reliable), UDP fallback."""
    started = time.monotonic()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, int(port or 11)))
            try:
                s.send(b'\n')
            except OSError:
                pass
            try:
                data = s.recv(2048)
            except (socket.timeout, OSError):
                data = b''
        return True, (time.monotonic() - started) * 1000, data[:200].decode('utf-8', 'replace')
    except (socket.timeout, ConnectionRefusedError, OSError):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(b'\n', (host, int(port or 11)))
            data, _ = s.recvfrom(2048)
        return True, (time.monotonic() - started) * 1000, data[:200].decode('utf-8', 'replace')
    except (socket.timeout, OSError) as exc:
        return False, (time.monotonic() - started) * 1000, f'{exc.__class__.__name__}: {exc}'


@peers_health_bp.route('/', methods=['GET'])
@login_required
def index():
    _require_admin()
    cfg = current_app.config
    is_hub = bool(cfg.get('REGISTRY_MODE_ENABLED'))
    rows = []
    if is_hub:
        peers = (RegistryEntry.query
                 .filter(RegistryEntry.is_active.is_(True))
                 .order_by(RegistryEntry.is_listed.desc(),
                           RegistryEntry.consecutive_probe_failures.desc(),
                           RegistryEntry.last_heartbeat_at.desc())
                 .all())
        for p in peers:
            rows.append({
                'id': p.id, 'host': p.host, 'name': p.name,
                'sysop': p.sysop, 'location': p.location,
                'is_listed': p.is_listed, 'is_approved': p.is_approved,
                'is_verified': p.is_verified,
                'last_heartbeat_str': _relative_age(p.last_heartbeat_at),
                'last_probe_str': _relative_age(p.last_probe_at),
                'last_probe_ok': p.last_probe_ok,
                'failures': p.consecutive_probe_failures,
                'msp_port': p.msp_port, 'systat_port': p.systat_port,
            })
    return render_template('admin/peers.html',
                           is_hub=is_hub, rows=rows)


@peers_health_bp.route('/<int:peer_id>/probe', methods=['POST'])
@login_required
def probe_now(peer_id):
    _require_admin()
    p = RegistryEntry.query.get_or_404(peer_id)
    ok, ms, detail = _systat_probe(p.host, p.systat_port or 11)
    p.last_probe_at = datetime.utcnow()
    p.last_probe_ok = ok
    if ok:
        p.consecutive_probe_failures = 0
    else:
        p.consecutive_probe_failures = (p.consecutive_probe_failures or 0) + 1
    db.session.commit()
    return jsonify({'ok': ok, 'ms': round(ms, 1), 'detail': detail,
                    'failures': p.consecutive_probe_failures})
