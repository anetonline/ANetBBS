# anetbbs/web/peers.py
"""
BBS directory — three sections:
  Local         : sysop-managed entries + user self-submissions (pending approval)
  TelnetBBSGuide: monthly ZIP (ibbs{MM}{YYYY}.zip) → dialdirectory.xml inside
  IPTIA         : dialdirectory.xml from ipingthereforeiam.com
Both XML files use EtherTerm format: <BBS name="..." ip="..." port="..." />
Parsed with regex because the XML contains unescaped & in BBS names.
"""
import io
import re
import threading
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
# Shared EtherTerm XML parser
# Both TelnetBBSGuide (inside ZIP) and IPTIA use identical format:
#   <BBS name="..." ip="..." port="..." protocol="TELNET" ... />
# We use regex instead of ElementTree because BBS names contain unescaped &.
# ---------------------------------------------------------------------------

_BBS_RE = re.compile(r'<BBS\s+([^>]+?)\s*/>', re.IGNORECASE)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _parse_etherterm_xml(data):
    """Parse EtherTerm dialdirectory.xml (bytes or str). Returns list of dicts."""
    if isinstance(data, bytes):
        text = data.decode('latin-1', errors='replace')
    else:
        text = data
    entries = []
    for m in _BBS_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        name = attrs.get('name', '').strip()
        host = attrs.get('ip', '').strip()
        port_raw = attrs.get('port', '23').strip()
        try:
            port = int(port_raw) if port_raw else 23
        except ValueError:
            port = 23
        if not name and not host:
            continue
        entries.append({
            'name': name,
            'telnet_host': host,
            'telnet_port': port,
            'web_url': None,
            'location': None,
            'software': None,
            'sysop': None,
            'description': None,
        })
    return entries


# ---------------------------------------------------------------------------
# TelnetBBSGuide — monthly ZIP containing dialdirectory.xml
# ---------------------------------------------------------------------------

def _telnetbbsguide_urls():
    """Return candidate ZIP URLs: current month then previous as fallback."""
    today = date.today()
    urls = [f'https://www.telnetbbsguide.com/bbslist/ibbs{today.month:02d}{today.year}.zip']
    if today.month == 1:
        prev = date(today.year - 1, 12, 1)
    else:
        prev = date(today.year, today.month - 1, 1)
    urls.append(f'https://www.telnetbbsguide.com/bbslist/ibbs{prev.month:02d}{prev.year}.zip')
    return urls


def _parse_telnetbbsguide(data):
    """Unzip the monthly archive and parse dialdirectory.xml inside."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        # Prefer dialdirectory.xml; fall back to first file
        xml_names = [n for n in zf.namelist() if n.lower().endswith('.xml')]
        target = next((n for n in xml_names if 'dial' in n.lower()),
                      xml_names[0] if xml_names else zf.namelist()[0])
        xml_data = zf.read(target)
    except Exception:
        xml_data = data  # not a ZIP, try raw
    return _parse_etherterm_xml(xml_data)


# ---------------------------------------------------------------------------
# IPTIA — direct dialdirectory.xml download (same EtherTerm format)
# ---------------------------------------------------------------------------

def _parse_iptia(data):
    return _parse_etherterm_xml(data)


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
    peers = local_q.order_by(PeerBbs.name).all()

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
        elif action == 'edit':
            p = PeerBbs.query.get_or_404(request.form.get('peer_id', type=int))
            name = (request.form.get('name') or '').strip()[:120]
            host = (request.form.get('hostname') or '').strip()[:160]
            if name:
                p.name = name
            if host:
                p.hostname = host
            try:
                p.telnet_port = int(request.form.get('telnet_port') or 23)
            except ValueError:
                pass
            p.web_url     = (request.form.get('web_url') or '').strip()[:400] or None
            p.location    = (request.form.get('location') or '').strip()[:120] or None
            p.software    = (request.form.get('software') or '').strip()[:80] or None
            p.ftn_address = (request.form.get('ftn_address') or '').strip()[:60] or None
            p.description = (request.form.get('description') or '').strip() or None
            p.is_active   = bool(request.form.get('is_active'))
            db.session.commit()
            flash(f'Updated {p.name}.', 'success')
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
