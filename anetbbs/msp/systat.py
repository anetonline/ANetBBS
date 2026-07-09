"""
SYSTAT / ActiveUser service — UDP port 11.

Per Synchronet docs (sbbsimsg/sbbsimsg-rev-1.25): the Instant Message
module discovers who's online on a remote BBS by sending a UDP packet
to port 11; the server responds with a Finger-style ASCII listing.

Format we emit (matches what Synchronet's fingerservice.js produces
closely enough to be parsed by their client):

    Synchronet <hostname> - <BBS_NAME>

    Node  User                   Action            Idle
    ----  ---------------------  ----------------  --------
       1  alice                  Reading mail       0:01
       2  bob                    Playing LORD       0:08

    Total active: 2
"""
import logging
import re
import socket
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_server_thread = None
_stop_event = threading.Event()
_listen_sock = None


def start_systat_server(app):
    """Spawn the SYSTAT/Finger UDP listener thread. Idempotent."""
    global _server_thread, _stop_event
    if not app.config.get('SYSTAT_ENABLED', True):
        logger.info('SYSTAT server disabled by configuration')
        return
    if _server_thread and _server_thread.is_alive():
        logger.warning('SYSTAT server already running')
        return
    _stop_event.clear()
    _server_thread = threading.Thread(
        target=_serve_loop, args=(app,), daemon=True, name='systat-server')
    _server_thread.start()


def stop_systat_server():
    global _stop_event, _listen_sock
    _stop_event.set()
    if _listen_sock is not None:
        try:
            _listen_sock.close()
        except OSError:
            pass


def _serve_loop(app):
    global _listen_sock
    bind_host = app.config.get('SYSTAT_BIND_HOST', '0.0.0.0')
    port = app.config.get('SYSTAT_PORT', 11)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        sock.settimeout(1.0)
    except OSError as exc:
        logger.error('SYSTAT: cannot bind UDP %s:%s — %s', bind_host, port, exc)
        return
    _listen_sock = sock
    logger.info('SYSTAT/ActiveUser UDP listening on %s:%s', bind_host, port)

    while not _stop_event.is_set():
        try:
            data, addr = sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            response = _build_response(app)
            sock.sendto(response.encode('utf-8', errors='replace'), addr)
            logger.debug('SYSTAT replied to %s with %d bytes',
                         addr[0], len(response))
        except Exception:
            logger.exception('SYSTAT error answering %s', addr)

    try:
        sock.close()
    except OSError:
        pass
    logger.info('SYSTAT server stopped')


def _build_response(app) -> str:
    """Render the active-user list as Finger-style text.

    Unions two data sources because front-ends populate different tables:
      - `NodeActivity` — telnet / SSH / rlogin terminal slot tracker
      - `UserSession` — web + general HTTP presence (same source `/who/` uses)
    Dedupes on username (a user logged into BOTH the web and a terminal
    counts as one entry). Without this union, anyone signed in only via
    web appears invisible to peer-BBS SYSTAT queries — the symptom is
    "Who's online" on `/imsg/directory/` showing nobody even when there
    obviously are users."""
    bbs_name = app.config.get('BBS_NAME', 'ANetBBS')
    bbs_host = app.config.get('BBS_HOSTNAME', '') or socket.gethostname()
    threshold = datetime.utcnow() - timedelta(minutes=10)

    lines = []
    lines.append(f'ANetBBS {bbs_host} - {bbs_name}')
    lines.append('')

    rows = []  # list of (sort_key, slot_str, user, action, last_seen)
    seen_users = set()
    with app.app_context():
        from ..models import NodeActivity, UserSession
        # Terminal sessions first — they have a real slot number.
        for r in (NodeActivity.query
                  .filter(NodeActivity.last_seen >= threshold)
                  .order_by(NodeActivity.slot.asc())
                  .all()):
            uname = (r.username or '(anon)')[:22]
            action = (r.action or r.page or '')[:24]
            rows.append((r.slot, str(r.slot), uname, action, r.last_seen))
            seen_users.add((uname or '').lower())

        # Web sessions — no slot, so synthesize 'w<N>' identifiers
        # starting after the highest terminal slot. Dedupe against
        # users already counted via NodeActivity.
        w_index = 1
        for s in (UserSession.query
                  .filter(UserSession.last_seen >= threshold)
                  .order_by(UserSession.last_seen.desc())
                  .all()):
            uname = ((s.user.username if s.user else None)
                     or '(anon)')[:22]
            if uname.lower() in seen_users:
                continue
            seen_users.add(uname.lower())
            # Page from UserSession is a raw URL — leak-suppress per the
            # /who/ sanitizer policy: for SYSTAT we expose only a coarse
            # area label, never the full path. Re-using the same map.
            page = s.page or ''
            action = _sanitize_web_page(page)[:24]
            rows.append((10000 + w_index, f'web{w_index}',
                         uname, action, s.last_seen))
            w_index += 1

    if rows:
        lines.append(f'{"Node":>4}  {"User":<22} {"Action":<24} {"Idle":>5}')
        lines.append(f'{"-"*4:>4}  {"-"*22:<22} {"-"*24:<24} {"-"*5:>5}')
        rows.sort(key=lambda r: r[0])
        for _, slot, user, action, last_seen in rows:
            idle_secs = int(
                (datetime.utcnow() - last_seen).total_seconds())
            idle = f'{idle_secs // 60}:{idle_secs % 60:02d}'
            lines.append(f'{slot:>4}  {user:<22} {action:<24} {idle:>5}')
        lines.append('')
        lines.append(f'Total active: {len(rows)}')
    else:
        lines.append('No users currently active.')

    return '\r\n'.join(lines) + '\r\n'


def _sanitize_web_page(page):
    """Same intent as anetbbs/web/who.py's _friendly_where — keep peer
    BBSes from learning the exact URL a user is browsing on this BBS."""
    if not page or page == '/':
        return 'Home'
    parts = page.lstrip('/').split('/', 1)
    return _WEB_AREA_LABELS.get(parts[0], 'Browsing')


_WEB_AREA_LABELS = {
    'admin': 'Admin', 'boards': 'Boards', 'bulletins': 'Bulletins',
    'calendar': 'Calendar', 'contacts': 'Contacts', 'docs': 'Docs',
    'echomail': 'Echomail', 'files': 'Files', 'file-areas': 'Files',
    'gallery': 'Gallery', 'games': 'Games', 'groups': 'Groups',
    'imsg': 'Inter-BBS IM', 'irc': 'IRC', 'leaderboard': 'Leaderboard',
    'messages': 'Messaging', 'mrc': 'MRC Chat', 'netmail': 'Netmail',
    'notifications': 'Notifications', 'oneliners': 'Oneliners',
    'page': 'Personal Pages', 'polls': 'Polls', 'profile': 'Profile',
    'rss': 'RSS Reader', 'saved': 'Saved Posts', 'shoutbox': 'Shoutbox',
    'stats': 'Stats', 'terminal': 'Web Terminal', 'who': "Who's Online",
    'wiki': 'Wiki', 'nodelist': 'Nodelist',
}


def query_systat(host: str, port: int = 11, timeout: float = 5.0) -> str:
    """Outbound: ask a remote BBS for its active-user list. Returns the
    text body, or '' on failure. Sends an empty UDP packet and reads
    the single-datagram reply per Finger-over-UDP convention."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(b'\r\n', (host, port))
            data, _addr = s.recvfrom(8192)
            try:
                return data.decode('utf-8')
            except UnicodeDecodeError:
                return data.decode('latin-1', errors='replace')
    except (socket.timeout, OSError) as exc:
        logger.info('SYSTAT query to %s:%s failed: %s', host, port, exc)
        return ''


_SEPARATOR_RE = re.compile(r'^-{2,}(\s+-{2,})*$')


def parse_systat_response(text: str) -> list:
    """Parse a query_systat() reply into a list of
    {'node': str, 'user': str, 'action': str, 'idle': str} dicts.

    Matches our own _build_response() format (a "Node  User  Action
    Idle" header + dashed separator, then one fixed-width-ish line per
    active user) closely enough to also handle real Synchronet peers,
    which use the same convention. Splits each data line on runs of 2+
    spaces rather than fixed column offsets, so small width differences
    from a peer's implementation don't break parsing. Returns [] for an
    empty reply, "No users currently active.", or anything that doesn't
    contain a recognizable header -- callers should treat an empty
    result as "probe succeeded but nothing to show" and fall back to
    manual entry, same as a failed/timed-out probe.
    """
    if not text:
        return []
    rows = []
    seen_header = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if low.startswith('total active') or low.startswith('no users'):
            continue
        if _SEPARATOR_RE.match(stripped):
            continue
        if not seen_header:
            if low.startswith('node') and 'user' in low:
                seen_header = True
            # Anything before the header (the "<software> <host> - <bbs
            # name>" banner line) is noise, not data -- skip either way.
            continue
        parts = re.split(r'\s{2,}', stripped)
        if len(parts) < 2 or not parts[1].strip():
            continue
        rows.append({
            'node': parts[0].strip(),
            'user': parts[1].strip(),
            'action': parts[2].strip() if len(parts) > 2 else '',
            'idle': parts[3].strip() if len(parts) > 3 else '',
        })
    return rows
