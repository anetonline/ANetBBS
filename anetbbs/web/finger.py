# anetbbs/web/finger.py
"""
Finger client UI. Lets users query remote BBSes' finger services
(RFC 1288 — TCP 79).
"""
import socket
from flask import Blueprint, render_template, request
from flask_login import login_required

from ..core.net_safety import resolve_safe_destination


finger_bp = Blueprint('finger', __name__, url_prefix='/finger')


def _do_finger(host, user='', port=79, timeout=10):
    """Synchronous finger query. Returns the raw response text.

    Real gap found in a security/performance audit: this had NO SSRF
    guard at all -- any logged-in user (not just admins) could point
    `host`/`port` at internal infrastructure (a Redis/Postgres port on
    localhost, another container on the same Docker network, a cloud
    metadata endpoint at 169.254.169.254) and read back up to 64KB of
    the raw response, with the `user` field sent as the first line on
    the wire -- a full authenticated SSRF + internal-port-scanning
    primitive, distinguishable open/closed/filtered by response
    timing/content. Same fix pattern already used by web_terminal.py's
    outbound telnet client and RSS's feed-URL fetches: resolve once via
    the shared core.net_safety helper and connect to the resolved
    sockaddr directly, never re-resolving the hostname string at
    connect time (which would reopen the DNS-rebinding TOCTOU gap the
    helper exists to close). Finger has no legitimate reason to reach
    this BBS's own loopback services, so no own_ports exception here.
    """
    family, sockaddr, error = resolve_safe_destination(host, port)
    if error:
        return f'[connection failed: {error}]'
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(sockaddr)
    except OSError as exc:
        return f'[connection failed: {exc}]'
    try:
        query = (user + '\r\n').encode('utf-8', errors='replace')
        sock.sendall(query)
        chunks = []
        while True:
            try:
                data = sock.recv(4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            if len(b''.join(chunks)) > 64 * 1024:
                break
        return b''.join(chunks).decode('utf-8', errors='replace')
    finally:
        try:
            sock.close()
        except OSError:
            pass


@finger_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    response = ''
    host = ''
    user = ''
    if request.method == 'POST':
        host = (request.form.get('host') or '').strip()
        user = (request.form.get('user') or '').strip()
        port = request.form.get('port', type=int) or 79
        if host:
            response = _do_finger(host, user=user, port=port)
    return render_template('finger/index.html',
                           response=response, host=host, user=user)
