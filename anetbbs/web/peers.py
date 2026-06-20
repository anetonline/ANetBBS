# anetbbs/web/peers.py
"""
BBS directory — three sections:
  Local      : sysop-managed entries + user self-submissions (pending approval)
  TelnetBBSGuide : fetched from their CSV, cached in DB every 6 hours
  IPTIA      : same pattern (shown only if data is available)
"""
import csv
import io
import socket
import threading
from datetime import datetime, timedelta

import requests
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from ..models import ExternalBbsCache, PeerBbs, db

peers_bp = Blueprint('peers', __name__, url_prefix='/bbses')

# ---------------------------------------------------------------------------
# External source config
# ---------------------------------------------------------------------------

EXTERNAL_SOURCES = {
    'telnetbbsguide': {
        'label': 'TelnetBBSGuide',
        'url': 'https://www.telnetbbsguide.com/bbs/list/brief/csv/',
        'icon': 'bi-globe2',
        'badge_color': 'bg-info text-dark',
    },
    'iptia': {
        'label': 'IPTIA',
        'url': 'https://iptia.bbsindex.com/bbs.csv',
        'icon': 'bi-broadcast',
        'badge_color': 'bg-warning text-dark',
    },
}

_CACHE_TTL_HOURS = 6
_refresh_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Local peer helpers (finger)
# ---------------------------------------------------------------------------

def _do_finger(host, port=79, timeout=8):
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except (OSError, socket.gaierror) as exc:
        return ('', f'connect failed: {exc}')
    try:
        sock.sendall(b'\r\n')
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
    if not text:
        return 0
    n = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(('=', '-')):
            continue
        if s.lower().startswith(('currently online', 'no users', 'login:',
                                  'name:', 'last on:', 'plan:', 'tagline:',
                                  'location:', 'calls:', 'status:')):
            continue
        if line.startswith('  '):
            n += 1
    return n


def _refresh_peer(peer):
    text, err = _do_finger(peer.hostname, peer.finger_port or 79)
    peer.last_polled_at = datetime.utcnow()
    peer.last_response = text or ''
    peer.last_error = err or None
    peer.online_count = _count_users_in_response(text) if not err else 0
    db.session.commit()


def _refresh_all_async():
    def _runner():
        try:
            for peer in PeerBbs.query.filter_by(is_active=True, is_approved=True).all():
                try:
                    _refresh_peer(peer)
                except Exception:
                    db.session.rollback()
        except Exception:
            pass
    try:
        from ..web_app import socketio
        socketio.start_background_task(_runner)
    except Exception:
        threading.Thread(target=_runner, daemon=True).start()


# ---------------------------------------------------------------------------
# External CSV fetch + parse
# ---------------------------------------------------------------------------

def _parse_telnetbbsguide(text):
    """Parse TelnetBBSGuide CSV. Returns list of dicts."""
    entries = []
    reader = csv.DictReader(io.StringIO(text))
    # Normalise header keys: strip whitespace, lower-case
    for row in reader:
        r = {k.strip().lower().replace(' ', '_'): (v or '').strip()
             for k, v in row.items()}
        name = r.get('bbs_name') or r.get('name') or r.get('bbsname') or ''
        host = (r.get('telnet_address') or r.get('address') or
                r.get('telnet') or r.get('hostname') or '')
        port_raw = (r.get('telnet_port') or r.get('port') or '23').strip()
        try:
            port = int(port_raw) if port_raw else 23
        except ValueError:
            port = 23
        web = (r.get('http_address') or r.get('web') or r.get('url') or
               r.get('http') or r.get('website') or '')
        loc_parts = [r.get('city', ''), r.get('state', ''),
                     r.get('country', '')]
        location = ', '.join(p for p in loc_parts if p)
        software = r.get('software') or r.get('bbs_software') or ''
        sysop = r.get('sysop') or r.get('sysop_name') or ''
        desc = r.get('notes') or r.get('description') or r.get('comment') or ''
        if not name and not host:
            continue
        if web and not web.startswith('http'):
            web = 'http://' + web
        entries.append({
            'name': name, 'telnet_host': host, 'telnet_port': port,
            'web_url': web, 'location': location, 'software': software,
            'sysop': sysop, 'description': desc,
        })
    return entries


def _parse_iptia(text):
    """Parse IPTIA CSV. Field layout TBD — best-effort fallback."""
    entries = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        r = {k.strip().lower().replace(' ', '_'): (v or '').strip()
             for k, v in row.items()}
        name = (r.get('bbs_name') or r.get('name') or r.get('bbsname') or '')
        host = (r.get('telnet_address') or r.get('address') or
                r.get('telnet') or r.get('hostname') or '')
        port_raw = (r.get('telnet_port') or r.get('port') or '23').strip()
        try:
            port = int(port_raw) if port_raw else 23
        except ValueError:
            port = 23
        web = (r.get('http_address') or r.get('web') or r.get('url') or
               r.get('website') or '')
        loc_parts = [r.get('city', ''), r.get('state', ''),
                     r.get('country', '')]
        location = ', '.join(p for p in loc_parts if p)
        software = r.get('software') or r.get('bbs_software') or ''
        sysop = r.get('sysop') or r.get('sysop_name') or ''
        desc = r.get('notes') or r.get('description') or ''
        if not name and not host:
            continue
        if web and not web.startswith('http'):
            web = 'http://' + web
        entries.append({
            'name': name, 'telnet_host': host, 'telnet_port': port,
            'web_url': web, 'location': location, 'software': software,
            'sysop': sysop, 'description': desc,
        })
    return entries


_PARSERS = {
    'telnetbbsguide': _parse_telnetbbsguide,
    'iptia': _parse_iptia,
}


def _cache_is_fresh(source):
    cutoff = datetime.utcnow() - timedelta(hours=_CACHE_TTL_HOURS)
    return (ExternalBbsCache.query
            .filter_by(source=source)
            .filter(ExternalBbsCache.cached_at >= cutoff)
            .first()) is not None


def _refresh_source(app, source):
    """Fetch CSV for source, wipe old rows, insert fresh ones."""
    cfg = EXTERNAL_SOURCES.get(source)
    if not cfg:
        return 0
    try:
        resp = requests.get(cfg['url'], timeout=20,
                            headers={'User-Agent': 'ANetBBS/1.0'})
        resp.raise_for_status()
        text = resp.text
    except Exception:
        return 0
    parser = _PARSERS.get(source)
    if not parser:
        return 0
    entries = parser(text)
    if not entries:
        return 0
    with app.app_context():
        ExternalBbsCache.query.filter_by(source=source).delete()
        now = datetime.utcnow()
        for e in entries:
            db.session.add(ExternalBbsCache(
                source=source,
                cached_at=now,
                **e,
            ))
        db.session.commit()
    return len(entries)


def _refresh_source_async(app, source):
    """Fire-and-forget background refresh for one source."""
    if not _refresh_lock.acquire(blocking=False):
        return  # another refresh already in flight
    def _runner():
        try:
            _refresh_source(app, source)
        finally:
            _refresh_lock.release()
    try:
        from ..web_app import socketio
        socketio.start_background_task(_runner)
    except Exception:
        threading.Thread(target=_runner, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@peers_bp.route('/')
@login_required
def index():
    q = (request.args.get('q') or '').strip()
    tab = request.args.get('tab', 'local')

    # ── Local peers ──────────────────────────────────────────────────────────
    local_q = (PeerBbs.query
               .filter_by(is_active=True, is_approved=True))
    if q:
        ql = f'%{q.lower()}%'
        local_q = local_q.filter(db.or_(
            db.func.lower(PeerBbs.name).like(ql),
            db.func.lower(PeerBbs.hostname).like(ql),
            db.func.lower(PeerBbs.location).like(ql),
        ))
    peers = local_q.order_by(PeerBbs.online_count.desc(), PeerBbs.name).all()

    cutoff = datetime.utcnow() - timedelta(minutes=5)
    stale = [p for p in peers
             if not p.last_polled_at or p.last_polled_at < cutoff]
    if stale:
        _refresh_all_async()

    # ── External sources ─────────────────────────────────────────────────────
    ext_data = {}
    for source in EXTERNAL_SOURCES:
        if not _cache_is_fresh(source):
            _refresh_source_async(current_app._get_current_object(), source)
        eq = ExternalBbsCache.query.filter_by(source=source)
        if q:
            ql = f'%{q.lower()}%'
            eq = eq.filter(db.or_(
                db.func.lower(ExternalBbsCache.name).like(ql),
                db.func.lower(ExternalBbsCache.telnet_host).like(ql),
                db.func.lower(ExternalBbsCache.location).like(ql),
                db.func.lower(ExternalBbsCache.software).like(ql),
                db.func.lower(ExternalBbsCache.sysop).like(ql),
            ))
        rows = eq.order_by(ExternalBbsCache.name).limit(2000).all()
        last = (ExternalBbsCache.query
                .filter_by(source=source)
                .order_by(ExternalBbsCache.cached_at.desc())
                .first())
        ext_data[source] = {
            'rows': rows,
            'count': len(rows),
            'last_updated': last.cached_at if last else None,
            **EXTERNAL_SOURCES[source],
        }

    pending_count = 0
    if getattr(current_user, 'is_admin', False):
        pending_count = PeerBbs.query.filter_by(is_approved=False).count()

    return render_template('peers/index.html',
                           peers=peers, q=q, tab=tab,
                           ext_data=ext_data,
                           pending_count=pending_count,
                           sources=EXTERNAL_SOURCES)


@peers_bp.route('/submit', methods=['GET', 'POST'])
@login_required
def submit():
    """User self-submission form — adds a pending entry for sysop approval."""
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        host = (request.form.get('hostname') or '').strip()
        port = request.form.get('telnet_port', type=int) or 23
        web = (request.form.get('web_url') or '').strip() or None
        loc = (request.form.get('location') or '').strip() or None
        sw = (request.form.get('software') or '').strip() or None
        desc = (request.form.get('description') or '').strip() or None
        if not name or not host:
            flash('BBS name and telnet address are required.', 'danger')
        elif PeerBbs.query.filter(
                db.func.lower(PeerBbs.hostname) == host.lower()).first():
            flash('That hostname is already in the directory.', 'warning')
        else:
            is_admin = getattr(current_user, 'is_admin', False)
            p = PeerBbs(
                name=name, hostname=host, telnet_port=port,
                web_url=web, location=loc, software=sw, description=desc,
                is_active=True, is_approved=is_admin,
                submitted_by_user_id=current_user.id,
            )
            db.session.add(p)
            db.session.commit()
            if is_admin:
                flash(f'Added {name}.', 'success')
            else:
                flash(f'Submitted! Your BBS will appear after sysop review.', 'success')
            return redirect(url_for('peers.index', tab='local'))
    return render_template('peers/submit.html')


@peers_bp.route('/<int:peer_id>')
@login_required
def view(peer_id):
    peer = PeerBbs.query.get_or_404(peer_id)
    if (not peer.is_active or not peer.is_approved) and \
            not getattr(current_user, 'is_admin', False):
        from flask import abort
        abort(404)
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
    if not getattr(current_user, 'is_admin', False):
        from flask import abort
        abort(403)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'delete':
            p = PeerBbs.query.get_or_404(request.form.get('peer_id', type=int))
            db.session.delete(p)
            db.session.commit()
            flash('Deleted.', 'success')
        elif action == 'approve':
            p = PeerBbs.query.get_or_404(request.form.get('peer_id', type=int))
            p.is_approved = True
            db.session.commit()
            flash(f'Approved {p.name}.', 'success')
        elif action == 'toggle':
            p = PeerBbs.query.get_or_404(request.form.get('peer_id', type=int))
            p.is_active = not bool(p.is_active)
            db.session.commit()
            flash('Toggled.', 'success')
        elif action == 'refresh':
            _refresh_all_async()
            flash('Finger refresh queued — reload in a few seconds.', 'info')
        elif action == 'refresh_ext':
            source = request.form.get('source', '')
            if source in EXTERNAL_SOURCES:
                app = current_app._get_current_object()
                threading.Thread(
                    target=_refresh_source, args=(app, source), daemon=True
                ).start()
                flash(f'Refresh of {EXTERNAL_SOURCES[source]["label"]} queued.', 'info')
        return redirect(url_for('peers.admin'))

    peers = PeerBbs.query.order_by(PeerBbs.is_approved, PeerBbs.name).all()
    ext_stats = {}
    for source, cfg in EXTERNAL_SOURCES.items():
        cnt = ExternalBbsCache.query.filter_by(source=source).count()
        last = (ExternalBbsCache.query.filter_by(source=source)
                .order_by(ExternalBbsCache.cached_at.desc()).first())
        ext_stats[source] = {
            'count': cnt,
            'last_updated': last.cached_at if last else None,
            'label': cfg['label'],
        }
    return render_template('peers/admin.html', peers=peers, ext_stats=ext_stats)
