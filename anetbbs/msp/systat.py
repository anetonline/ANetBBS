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

# Real gap found in a security/performance audit: this UDP responder
# answered EVERY inbound datagram, from any source, with no rate
# limiting at all -- a classic UDP reflection/amplification vector.
# query_systat()'s own real request is a bare 2-byte "\r\n" packet,
# while _build_response()'s reply is easily 10-50x that (a full
# who's-online listing gets bigger with every additional user, and
# even the empty "No users currently active." fallback dwarfs the
# request) -- an attacker who spoofs a victim's source IP onto tiny
# request packets gets this server to blast amplified replies at the
# victim, no authentication needed since the protocol has none by
# design (matches Synchronet's own real fingerservice.js convention,
# which has the identical shape). Can't require auth without breaking
# real inter-BBS "who's online" discovery, so this bounds the damage
# instead: per-source-IP AND a global cap, using the same sliding-
# window limiter features/rate_limit.py already uses for HTTP routes
# (its _check() has no Flask dependency, so it works fine here too).
# A rate-limited datagram is dropped silently -- never partially
# amplified.
_SYSTAT_PER_IP_LIMIT = 10
_SYSTAT_PER_IP_WINDOW = 60
_SYSTAT_GLOBAL_LIMIT = 120
_SYSTAT_GLOBAL_WINDOW = 60


def start_systat_server(app):
    """Spawn the SYSTAT/Finger UDP listener thread. Idempotent."""
    global _server_thread
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

    from ..features.rate_limit import _check as _rate_limit_check

    while not _stop_event.is_set():
        try:
            data, addr = sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break
        if not _rate_limit_check(f'systat:{addr[0]}', _SYSTAT_PER_IP_LIMIT,
                                 _SYSTAT_PER_IP_WINDOW):
            logger.debug('SYSTAT: per-IP rate limit hit for %s, dropping', addr[0])
            continue
        if not _rate_limit_check('systat:global', _SYSTAT_GLOBAL_LIMIT,
                                 _SYSTAT_GLOBAL_WINDOW):
            logger.debug('SYSTAT: global rate limit hit, dropping request from %s',
                        addr[0])
            continue
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
        from ..models import db, NodeActivity, UserSession
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
        #
        # Real gap found in a security/performance audit: `s.user` below
        # is a lazy=True relationship (models.py's UserSession.user) --
        # iterating without eager-loading it means one extra SELECT
        # against `users` PER active web session row, on every SYSTAT
        # query this UDP responder answers (see the rate-limit comment
        # above -- this responder is deliberately reachable by any
        # unauthenticated peer that can hit the socket, so its per-request
        # cost matters). joinedload() folds that into the single UserSession
        # query via a JOIN, so cost no longer scales with the number of
        # concurrently-online web users.
        w_index = 1
        for s in (UserSession.query
                  .options(db.joinedload(UserSession.user))
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
    the single-datagram reply per Finger-over-UDP convention.

    SSRF guard: `host` is directory-sourced data (a BbsDirectoryEntry's
    hostname/IP), not locally trusted -- it's populated from a remote
    peer-list pull (msp/directory.py, msp/anetbbs_directory.py) or, on
    the hub, from whatever a peer self-registered. Without a check, the
    "Who's online" feature (imsg.py's directory_who(), reachable by any
    logged-in user with no admin gate) turns into a UDP probe primitive
    against arbitrary internal targets. Resolved once and connected to
    the resolved address (not the original hostname string) to avoid a
    DNS-rebinding gap between validation and send.
    """
    from ..core.net_safety import resolve_safe_destination
    family, sockaddr, error = resolve_safe_destination(host, port)
    if error:
        logger.info('SYSTAT query to %s:%s refused — %s', host, port, error)
        return ''
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(b'\r\n', sockaddr)
            data, _addr = s.recvfrom(8192)
            try:
                return data.decode('utf-8')
            except UnicodeDecodeError:
                return data.decode('latin-1', errors='replace')
    except (socket.timeout, OSError) as exc:
        logger.info('SYSTAT query to %s:%s failed: %s', host, port, exc)
        return ''


_SEPARATOR_RE = re.compile(r'^-{2,}(\s+-{2,})*$')


_COL_KEYWORDS = ('node', 'user', 'action', 'idle', 'time-on', 'age')


def parse_systat_response(text: str) -> list:
    """Parse a query_systat() reply into a list of
    {'node': str, 'user': str, 'action': str, 'idle': str} dicts.

    Real bug found live (2026-09-29): a direct UDP probe of a real
    Synchronet peer (a-net-online.lol) confirmed the network round trip
    works fine -- it replies -- but its header is column-ordered
    "User  Action  Time-on Age  Node" (Synchronet's own real
    fingerservice.js convention), the reverse of our own _build_response()
    "Node  User  Action  Idle". The old parser only recognized a header
    starting with the literal word "node", so every real Synchronet
    reply was silently dropped as "noise before the header" and the
    terminal MSP picker always reported "nobody online there" even
    against a peer that answered correctly.

    Fixed to detect the header by keyword regardless of column order,
    then determine field order from where each keyword's substring
    appears in the header text (not by splitting the header itself --
    "Time-on" and "Age" are only one space apart in the real reply
    above, a single 2+-space-split token spanning two real data
    columns). Each data row is still split on runs of 2+ spaces, and
    zipped against the header-derived field order positionally -- this
    stays correct even for the "Time-on"/"Age" case because both
    columns are counted (Age is simply dropped from the output after
    zipping), keeping every later column's position aligned.
    """
    if not text:
        return []
    lines = text.splitlines()
    header_i = None
    field_order = []
    for i, line in enumerate(lines):
        low = line.lower()
        if (re.search(r'\buser\b', low)
                and (re.search(r'\bnode\b', low) or re.search(r'\baction\b', low))):
            positions = [(low.find(kw), kw) for kw in _COL_KEYWORDS if kw in low]
            field_order = [kw for _pos, kw in sorted(positions)]
            header_i = i
            break
    if header_i is None or 'user' not in field_order:
        return []

    idle_key = ('idle' if 'idle' in field_order
                else ('time-on' if 'time-on' in field_order else None))

    rows = []
    for line in lines[header_i + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if low.startswith('total active') or low.startswith('no users'):
            continue
        if _SEPARATOR_RE.match(stripped):
            continue
        tokens = re.split(r'\s{2,}', stripped)
        vals = dict(zip(field_order, tokens))
        if not vals.get('user'):
            continue
        rows.append({
            'node': vals.get('node', ''),
            'user': vals.get('user', ''),
            'action': vals.get('action', ''),
            'idle': vals.get(idle_key, '') if idle_key else '',
        })
    return rows
