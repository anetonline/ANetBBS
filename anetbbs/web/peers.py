# anetbbs/web/peers.py
"""
BBS directory + cross-BBS presence aggregator.

Sysop registers known peer BBSes. The directory page fingers each one on
demand (or via a cron job) and caches the result so users can see who's
active across the network. This is the closest analog to Synchronet's
"active sysops" view across DOVE-Net etc.
"""
import socket
import threading
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from ..models import db, PeerBbs


peers_bp = Blueprint('peers', __name__, url_prefix='/bbses')


def _do_finger(host, port=79, timeout=8):
    """Synchronous finger query. Returns (text, error_or_None)."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except (OSError, socket.gaierror) as exc:
        return ('', f'connect failed: {exc}')
    try:
        sock.sendall(b'\r\n')   # empty query → list online users
        chunks = []
        while True:
            try:
                data = sock.recv(4096)
            except OSError as exc:
                return ('', f'recv failed: {exc}')
            if not data:
                break
            chunks.append(data)
            if sum(len(c) for c in chunks) > 64 * 1024:
                break
        return (b''.join(chunks).decode('utf-8', errors='replace'), None)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _count_users_in_response(text):
    """Best-effort online-user count from a finger response."""
    if not text:
        return 0
    # Heuristic: count lines that look like "  <username>   <where>   ..."
    n = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('=') or s.startswith('-'):
            continue
        if s.lower().startswith(('currently online',
                                 'no users',
                                 'login:',
                                 'name:',
                                 'last on:',
                                 'plan:',
                                 'tagline:',
                                 'location:',
                                 'calls:',
                                 'status:')):
            continue
        # "username   /[telnet] main   (last seen 12:34)" pattern
        if line.startswith('  '):
            n += 1
    return n


def _refresh_peer(peer):
    """Finger one peer, update its cached state."""
    text, err = _do_finger(peer.hostname, peer.finger_port or 79)
    peer.last_polled_at = datetime.utcnow()
    peer.last_response = text or ''
    peer.last_error = err or None
    peer.online_count = _count_users_in_response(text) if not err else 0
    db.session.commit()


def _refresh_all_async():
    """Spawn a background thread to refresh all active peers."""
    def _runner():
        # Background thread needs its own app context.
        try:
            from ..models import PeerBbs
            for peer in PeerBbs.query.filter_by(is_active=True).all():
                try:
                    _refresh_peer(peer)
                except Exception:
                    db.session.rollback()
        except Exception:
            pass
    # Use eventlet's spawn so we don't block the request.
    try:
        from ..web_app import socketio
        socketio.start_background_task(_runner)
    except Exception:
        threading.Thread(target=_runner, daemon=True).start()


@peers_bp.route('/')
@login_required
def index():
    """User-facing BBS directory."""
    peers = (PeerBbs.query.filter_by(is_active=True)
             .order_by(PeerBbs.online_count.desc(), PeerBbs.name).all())
    # Auto-refresh anything older than 5 minutes
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    stale = [p for p in peers if not p.last_polled_at or p.last_polled_at < cutoff]
    if stale:
        _refresh_all_async()
    return render_template('peers/index.html', peers=peers)


@peers_bp.route('/<int:peer_id>')
@login_required
def view(peer_id):
    peer = PeerBbs.query.get_or_404(peer_id)
    if not peer.is_active and not getattr(current_user, 'is_admin', False):
        abort(404)
    # On-demand refresh if stale
    cutoff = datetime.utcnow() - timedelta(minutes=2)
    if not peer.last_polled_at or peer.last_polled_at < cutoff:
        try:
            _refresh_peer(peer)
        except Exception:
            db.session.rollback()
    return render_template('peers/view.html', peer=peer)


@peers_bp.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    """Sysop CRUD for the BBS directory."""
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = (request.form.get('name') or '').strip()
            host = (request.form.get('hostname') or '').strip()
            port = request.form.get('finger_port', type=int) or 79
            ftn = (request.form.get('ftn_address') or '').strip() or None
            desc = (request.form.get('description') or '').strip() or None
            if not name or not host:
                flash('Name and hostname required.', 'danger')
            elif PeerBbs.query.filter_by(hostname=host).first():
                flash(f'Host {host} already registered.', 'warning')
            else:
                p = PeerBbs(name=name, hostname=host, finger_port=port,
                            ftn_address=ftn, description=desc, is_active=True)
                db.session.add(p)
                db.session.commit()
                flash(f'Added {name} ({host}).', 'success')
        elif action == 'delete':
            p = PeerBbs.query.get_or_404(request.form.get('peer_id', type=int))
            db.session.delete(p)
            db.session.commit()
            flash('Deleted.', 'success')
        elif action == 'toggle':
            p = PeerBbs.query.get_or_404(request.form.get('peer_id', type=int))
            p.is_active = not bool(p.is_active)
            db.session.commit()
            flash('Toggled.', 'success')
        elif action == 'refresh':
            _refresh_all_async()
            flash('Refresh queued — reload in a few seconds.', 'info')
        return redirect(url_for('peers.admin'))

    peers = PeerBbs.query.order_by(PeerBbs.name).all()
    return render_template('peers/admin.html', peers=peers)
