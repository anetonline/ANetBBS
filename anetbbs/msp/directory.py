"""
sbbsimsg.lst directory — list of BBSes that participate in inter-BBS IM.

The master file is generated daily on Vertrauen and distributed at
ftp://vert.synchro.net/sbbsimsg.lst. Format (tab-delimited, three fields):

    <hostname padded to ~64 chars>\\t<ip address>\\t<BBS display name>

We fetch it (FTP or HTTP), parse it, and persist into BbsDirectoryEntry
so the UI can show the list and the user can pick a target BBS to ping.
A scheduled background refresh updates it once a day.
"""
import logging
import threading
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_LIST_URL = 'ftp://vert.synchro.net/sbbsimsg.lst'

_refresher_thread = None
_stop_event = threading.Event()


def parse_list(text: str):
    """Parse the tab-delimited sbbsimsg.lst body. Returns a list of dicts."""
    out = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith('#') or line.startswith(';'):
            continue
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        host = parts[0].strip()
        ip = parts[1].strip() if len(parts) > 1 else ''
        name = parts[2].strip() if len(parts) > 2 else host
        if not host:
            continue
        out.append({'hostname': host, 'ip': ip, 'name': name})
    return out


def fetch_list(url: str = DEFAULT_LIST_URL, timeout: float = 30.0) -> str:
    """Fetch the raw list file (FTP or HTTP)."""
    if url.startswith(('ftp://', 'ftps://')):
        return _fetch_ftp(url, timeout)
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310 -- admin-configured registry/directory URL, not user input
        data = resp.read()
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('latin-1', errors='replace')


def _fetch_ftp(url: str, timeout: float) -> str:
    import ftplib  # nosec B402 -- FTP directory listing is an intentional MSP feature
    import io
    u = urlparse(url)
    host = u.hostname
    port = u.port or 21
    path = (u.path or '/').lstrip('/')
    cls = ftplib.FTP_TLS if u.scheme == 'ftps' else ftplib.FTP
    buf = io.BytesIO()
    with cls() as ftp:
        ftp.connect(host, port, timeout=timeout)
        ftp.login(u.username or 'anonymous', u.password or 'anetbbs@')
        if u.scheme == 'ftps':
            try: ftp.prot_p()
            except Exception: pass
        ftp.set_pasv(True)
        ftp.retrbinary(f'RETR {path}', buf.write)
    raw = buf.getvalue()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('latin-1', errors='replace')


def refresh(app, url: str = None) -> int:
    """Pull the master list, upsert into BbsDirectoryEntry. Returns row count."""
    url = url or app.config.get('SBBSIMSG_LIST_URL', DEFAULT_LIST_URL)
    logger.info('sbbsimsg directory: fetching %s', url)
    try:
        text = fetch_list(url)
    except Exception as exc:
        logger.error('sbbsimsg fetch failed: %s', exc)
        return 0
    entries = parse_list(text)
    if not entries:
        logger.warning('sbbsimsg: parsed zero entries from %s', url)
        return 0

    with app.app_context():
        from ..models import db, BbsDirectoryEntry
        now = datetime.utcnow()
        seen_hosts = set()
        for e in entries:
            seen_hosts.add(e['hostname'].lower())
            row = (BbsDirectoryEntry.query
                   .filter_by(hostname=e['hostname']).first())
            if row is None:
                row = BbsDirectoryEntry(hostname=e['hostname'])
                db.session.add(row)
            row.ip_address = e['ip']
            row.name = e['name']
            row.last_seen_at = now
        # Mark rows missing from this fetch as not-seen-now (don't delete —
        # they may come back).
        db.session.commit()
        kept = (BbsDirectoryEntry.query
                .filter(db.func.lower(BbsDirectoryEntry.hostname)
                        .in_(seen_hosts)).count())
    logger.info('sbbsimsg directory: %d active entries refreshed', kept)
    return kept


def start_refresher(app):
    """Spawn a daemon thread that refreshes the directory once a day."""
    global _refresher_thread, _stop_event
    if not app.config.get('SBBSIMSG_AUTO_REFRESH', True):
        logger.info('sbbsimsg auto-refresh disabled')
        return
    if _refresher_thread and _refresher_thread.is_alive():
        return
    _stop_event.clear()
    _refresher_thread = threading.Thread(
        target=_refresh_loop, args=(app,), daemon=True,
        name='sbbsimsg-refresher')
    _refresher_thread.start()


def _refresh_loop(app):
    interval = int(app.config.get('SBBSIMSG_REFRESH_SECONDS', 86400))
    # Initial delay so the listener finishes binding etc.
    if not _stop_event.wait(60):
        try:
            refresh(app)
        except Exception:
            logger.exception('sbbsimsg initial refresh crashed')
    while not _stop_event.wait(interval):
        try:
            refresh(app)
        except Exception:
            logger.exception('sbbsimsg refresh crashed')


def stop_refresher():
    _stop_event.set()
