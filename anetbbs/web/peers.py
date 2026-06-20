# anetbbs/web/peers.py
"""
BBS directory — three sections:
  Local         : sysop-managed entries + user self-submissions (pending approval)
  TelnetBBSGuide: monthly ZIP (ibbs{MM}{YYYY}.zip) containing a pipe-delimited list
  IPTIA         : XML from ipingthereforeiam.com
"""
import io
import socket
import threading
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta

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
        'icon': 'bi-globe2',
        'badge_color': 'bg-info text-dark',
    },
    'iptia': {
        'label': 'IPTIA',
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
# TelnetBBSGuide — monthly ZIP, pipe-delimited text inside
# ---------------------------------------------------------------------------

def _telnetbbsguide_urls():
    """Return candidate URLs for this month then last month as fallback."""
    today = date.today()
    urls = [f'https://www.telnetbbsguide.com/bbslist/ibbs{today.month:02d}{today.year}.zip']
    if today.month == 1:
        prev = date(today.year - 1, 12, 1)
    else:
        prev = date(today.year, today.month - 1, 1)
    urls.append(f'https://www.telnetbbsguide.com/bbslist/ibbs{prev.month:02d}{prev.year}.zip')
    return urls


def _parse_telnetbbsguide(data):
    """Parse TelnetBBSGuide monthly ZIP. data is bytes. Returns list of dicts."""
    # Extract text from ZIP
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        # Pick first .txt/.lst/.dat or just whatever is in there
        txt_names = [n for n in names
                     if n.lower().endswith(('.txt', '.lst', '.dat', '.bbs'))]
        target = txt_names[0] if txt_names else (names[0] if names else None)
        if target is None:
            return []
        raw = zf.read(target)
    except Exception:
        raw = data  # not a ZIP — try treating as raw text

    text = raw.decode('latin-1', errors='replace')
    entries = []

    # The IBBS list is pipe-delimited. Common column orders seen:
    # Name|Network|Telnet:Port|Location|Sysop|Software|Web|Notes
    # or Name|Telnet|Port|Sysop|Location|Software|Web|Notes
    # We sniff the header row if present, otherwise use positional guesses.
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []

    # Check if first line looks like a header
    header_line = lines[0]
    has_header = any(kw in header_line.lower()
                     for kw in ('name', 'address', 'telnet', 'sysop', 'software'))

    if '|' in (lines[1] if len(lines) > 1 else lines[0]):
        # Pipe-delimited
        if has_header:
            # Normalise header keys
            headers = [h.strip().lower().replace(' ', '_').replace('/', '_')
                       for h in header_line.split('|')]
            data_lines = lines[1:]
        else:
            headers = None
            data_lines = lines

        for line in data_lines:
            if not line.strip() or line.startswith(';'):
                continue
            fields = [f.strip() for f in line.split('|')]
            if len(fields) < 2:
                continue

            if headers:
                r = dict(zip(headers, fields))
            else:
                # Positional fallback: Name|Telnet|Port|Sysop|Location|Software|Web|Notes
                r = {}
                _pos = ['name', 'telnet_host', 'telnet_port', 'sysop',
                        'location', 'software', 'web_url', 'description']
                for i, val in enumerate(fields):
                    if i < len(_pos):
                        r[_pos[i]] = val

            name = (r.get('name') or r.get('bbs_name') or r.get('bbs') or '').strip()
            # Telnet address may be host:port in one field
            raw_addr = (r.get('telnet_host') or r.get('telnet') or
                        r.get('address') or r.get('telnet_address') or '').strip()
            port_raw = (r.get('telnet_port') or r.get('port') or '').strip()
            if ':' in raw_addr and not port_raw:
                host, _, port_raw = raw_addr.partition(':')
            else:
                host = raw_addr
            try:
                port = int(port_raw) if port_raw else 23
            except ValueError:
                port = 23
            web = (r.get('web_url') or r.get('web') or r.get('url') or
                   r.get('website') or r.get('http') or '').strip()
            if web and not web.startswith('http'):
                web = 'http://' + web
            location = (r.get('location') or r.get('city') or '').strip()
            software = (r.get('software') or r.get('bbs_software') or '').strip()
            sysop = (r.get('sysop') or r.get('sysop_name') or '').strip()
            desc = (r.get('description') or r.get('notes') or
                    r.get('comment') or '').strip()

            if not name and not host:
                continue
            entries.append({
                'name': name, 'telnet_host': host, 'telnet_port': port,
                'web_url': web or None, 'location': location or None,
                'software': software or None, 'sysop': sysop or None,
                'description': desc or None,
            })
    else:
        # Labeled-field block format:
        # BBS Name: ...
        # Address: ...
        # Port: ...
        # Sysop: ...  etc.
        current = {}
        for line in lines:
            if line.startswith(('-' * 5, '=' * 5, '*' * 5)):
                if current:
                    entries.append(_ibbs_block_to_entry(current))
                    current = {}
                continue
            if ':' in line:
                key, _, val = line.partition(':')
                current[key.strip().lower()] = val.strip()
        if current:
            entries.append(_ibbs_block_to_entry(current))

    return [e for e in entries if e]


def _ibbs_block_to_entry(r):
    name = (r.get('bbs name') or r.get('name') or '').strip()
    host = (r.get('address') or r.get('telnet') or r.get('hostname') or '').strip()
    port_raw = r.get('port', '23').strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 23
    web = (r.get('web') or r.get('website') or r.get('url') or '').strip()
    if web and not web.startswith('http'):
        web = 'http://' + web
    if not name and not host:
        return None
    return {
        'name': name, 'telnet_host': host, 'telnet_port': port,
        'web_url': web or None,
        'location': (r.get('location') or r.get('city') or None),
        'software': r.get('software') or None,
        'sysop': r.get('sysop') or None,
        'description': r.get('notes') or r.get('description') or None,
    }


# ---------------------------------------------------------------------------
# IPTIA — XML
# ---------------------------------------------------------------------------

def _parse_iptia(data):
    """Parse IPTIA dialdirectory.xml. data is bytes. Returns list of dicts."""
    entries = []
    try:
        root = ET.fromstring(data if isinstance(data, bytes)
                             else data.encode('utf-8'))
    except ET.ParseError:
        return []

    # Walk every element that might be a BBS record.
    # Common tag names: bbs, system, entry, record, listing
    _bbs_tags = {'bbs', 'system', 'entry', 'record', 'listing', 'bbsentry',
                 'bbslisting'}

    def _find_records(node):
        # If this node itself looks like a record, yield it
        if node.tag.lower().split('}')[-1] in _bbs_tags:
            yield node
        else:
            for child in node:
                yield from _find_records(child)

    def _text(node, *tags):
        """First non-empty text among tried tag names (case-insensitive search)."""
        tag_lower = {t.lower() for t in tags}
        for child in node:
            if child.tag.lower().split('}')[-1] in tag_lower:
                return (child.text or '').strip()
        # Also check attributes
        for t in tags:
            val = node.get(t, '') or node.get(t.upper(), '') or node.get(t.lower(), '')
            if val:
                return val.strip()
        return ''

    for record in _find_records(root):
        name = _text(record, 'name', 'bbsname', 'bbs_name', 'title', 'systemname')
        host = _text(record, 'address', 'telnet', 'hostname', 'host',
                     'telnetaddress', 'telnet_address', 'ip', 'ipaddress')
        port_raw = _text(record, 'port', 'telnetport', 'telnet_port')
        # address may be host:port
        if ':' in host and not port_raw:
            host, _, port_raw = host.partition(':')
        try:
            port = int(port_raw) if port_raw else 23
        except ValueError:
            port = 23
        web = _text(record, 'web', 'website', 'url', 'http', 'www',
                    'webaddress', 'web_address')
        if web and not web.startswith('http'):
            web = 'http://' + web
        loc_parts = [
            _text(record, 'city'), _text(record, 'state', 'province'),
            _text(record, 'country'),
        ]
        location = ', '.join(p for p in loc_parts if p) or \
                   _text(record, 'location', 'region')
        software = _text(record, 'software', 'bbssoftware', 'bbs_software', 'type')
        sysop = _text(record, 'sysop', 'sysopname', 'sysop_name', 'operator')
        desc = _text(record, 'description', 'notes', 'comment', 'info', 'about')

        if not name and not host:
            continue
        entries.append({
            'name': name, 'telnet_host': host, 'telnet_port': port,
            'web_url': web or None, 'location': location or None,
            'software': software or None, 'sysop': sysop or None,
            'description': desc or None,
        })

    return entries


# ---------------------------------------------------------------------------
# Generic fetch + cache
# ---------------------------------------------------------------------------

def _cache_is_fresh(source):
    cutoff = datetime.utcnow() - timedelta(hours=_CACHE_TTL_HOURS)
    return (ExternalBbsCache.query
            .filter_by(source=source)
            .filter(ExternalBbsCache.cached_at >= cutoff)
            .first()) is not None


def _fetch_telnetbbsguide():
    """Try monthly URLs in order, return raw bytes or None."""
    for url in _telnetbbsguide_urls():
        try:
            resp = requests.get(url, timeout=30,
                                headers={'User-Agent': 'ANetBBS/1.0'})
            if resp.status_code == 200:
                return resp.content
        except Exception:
            pass
    return None


def _fetch_iptia():
    """Fetch IPTIA XML, return bytes or None."""
    url = 'https://www.ipingthereforeiam.com/bbs/dir/dialdirectory.xml'
    try:
        resp = requests.get(url, timeout=30,
                            headers={'User-Agent': 'ANetBBS/1.0'})
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


_FETCHERS = {
    'telnetbbsguide': _fetch_telnetbbsguide,
    'iptia': _fetch_iptia,
}
_PARSERS = {
    'telnetbbsguide': _parse_telnetbbsguide,
    'iptia': _parse_iptia,
}


def _refresh_source(app, source):
    """Fetch + parse source, wipe old rows, insert fresh ones. Returns count."""
    fetcher = _FETCHERS.get(source)
    parser = _PARSERS.get(source)
    if not fetcher or not parser:
        return 0
    data = fetcher()
    if not data:
        return 0
    try:
        entries = parser(data)
    except Exception:
        return 0
    if not entries:
        return 0
    try:
        with app.app_context():
            ExternalBbsCache.query.filter_by(source=source).delete()
            now = datetime.utcnow()
            for e in entries:
                db.session.add(ExternalBbsCache(source=source, cached_at=now, **e))
            db.session.commit()
    except Exception:
        return 0
    return len(entries)


def _refresh_source_async(app, source):
    if not _refresh_lock.acquire(blocking=False):
        return
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
    local_q = PeerBbs.query.filter_by(is_active=True, is_approved=True)
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
                flash('Submitted! Your BBS will appear after sysop review.', 'success')
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
