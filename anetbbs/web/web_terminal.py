# anetbbs/web/web_terminal.py
"""
Browser-based terminal — xterm.js front-end, socketio back-end.

Lets a web user open a raw TCP socket (optionally TLS-wrapped) to ANY
host:port (defaults to the local BBS's telnet port) right inside the
browser tab — no native client required. This is a plain telnet-style
pipe, not a protocol client: there's no telnet IAC option negotiation
and no SSH implementation, so it works well against telnet/rlogin-style
servers that just stream bytes but will not complete a real SSH
handshake. This is the closest analog to fTelnet / SyncTERM-in-browser.

Architecture:
    Browser <—socketio—> Flask process <—TCP—> Remote BBS

Each browser tab gets its own outbound TCP socket; bytes flow both ways
through the socketio namespace `/term`.
"""
import ipaddress
import logging
import os
import socket
import ssl as ssl_module
import threading

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from flask_socketio import emit


logger = logging.getLogger(__name__)

term_bp = Blueprint('web_terminal', __name__, url_prefix='/terminal')


# Per-socketio-sid outbound terminal state
_sessions = {}
_lock = threading.Lock()


def _own_service_ports():
    """This BBS's own telnet/SSH/rlogin ports -- the only ports this
    feature is allowed to reach on loopback, matching its documented
    intended use (dialing into our own terminal service from the
    browser). Any OTHER loopback/private-network port is refused."""
    ports = set()
    for env_name, default in (('TELNET_PORT', '2233'),
                               ('SSH_PORT', '2234'),
                               ('RLOGIN_PORT', '513')):
        try:
            ports.add(int(os.environ.get(env_name, default)))
        except (TypeError, ValueError):
            pass
    return ports


def _resolve_safe_destination(host, port):
    """SSRF guard: this feature (see module docstring) deliberately lets
    a logged-in user connect to ANY *external* host:port -- that's the
    whole point, it's a browser telnet client for dialing other BBSes.
    Without a check here, though, the exact same code path lets any
    logged-in user (not just admins) point the server at internal
    infrastructure instead: other hosts on a private LAN, other
    containers on the same Docker network, or a cloud metadata endpoint
    (169.254.169.254) -- turning this into an authenticated SSRF/port-
    scanning primitive. Rejects private/link-local/reserved/multicast
    destinations, with a narrow exception for loopback on one of this
    BBS's own service ports.

    Resolves the hostname ONCE here and returns the resolved
    (family, sockaddr) pair for the caller to connect() with directly,
    rather than letting the caller re-resolve the hostname string at
    connect time -- re-resolving would leave a DNS-rebinding gap where
    a hostname that resolves safely at check-time could resolve to an
    internal address by connect-time.

    Returns (family, sockaddr, None) on success, or
    (None, None, error_message) on rejection.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None, None, f'Could not resolve host: {host}'
    if not infos:
        return None, None, f'Could not resolve host: {host}'

    family, _socktype, _proto, _canonname, sockaddr = infos[0]
    try:
        ip = ipaddress.ip_address(sockaddr[0])
    except ValueError:
        return None, None, 'Invalid resolved address'

    if ip.is_loopback and port in _own_service_ports():
        return family, sockaddr, None
    if (ip.is_private or ip.is_link_local or ip.is_loopback
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return None, None, 'Connections to private/internal addresses are not allowed'
    return family, sockaddr, None


class _TermSession:
    """One browser tab's terminal — spawned greenthread reads from the
    outbound socket and forwards bytes to the user via socketio."""

    def __init__(self, sid, host, port, use_ssl=False):
        self.sid = sid
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.sock = None
        self.connected = False

    def open(self):
        # _resolve_safe_destination() (see its own docstring) is the
        # SSRF guard -- resolves once and hands back the exact address
        # to connect to, so this doesn't re-resolve self.host itself
        # (which would reopen a DNS-rebinding gap between the check
        # and the connect).
        family, sockaddr, error = _resolve_safe_destination(self.host, self.port)
        if error:
            self._emit('term_error', {'message': error})
            return False
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(15)
            sock.connect(sockaddr)
            if self.use_ssl:
                ctx = ssl_module.create_default_context()
                # Many BBSes use self-signed certs; relax verification.
                ctx.check_hostname = False
                ctx.verify_mode = ssl_module.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=self.host)
            self.sock = sock
            self.connected = True
            return True
        except (OSError, ssl_module.SSLError) as exc:
            self._emit('term_error', {'message': f'Connect failed: {exc}'})
            return False

    def write(self, data):
        if self.sock is None or not self.connected:
            return
        if isinstance(data, str):
            data = data.encode('utf-8', errors='replace')
        try:
            self.sock.sendall(data)
        except OSError as exc:
            self._emit('term_error', {'message': f'Write failed: {exc}'})
            self.connected = False

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.connected = False
        self.sock = None

    def run(self):
        """Read loop — push everything we receive back to the browser as
        UTF-8 (browser side decodes / xterm.js writes it raw).

        Uses a recv timeout so we can interleave server→browser keepalive
        events when the remote BBS is idle. `socket.timeout` distinguishes
        idle-timeout from remote-close: timeout = continue, b'' = remote
        sent FIN, exception = error."""
        try:
            if self.sock is not None:
                self.sock.settimeout(20)
        except OSError:
            pass

        import time as _time
        last_keepalive = _time.monotonic()

        try:
            while self.connected:
                sock = self.sock  # snapshot so close()-racing can't None it mid-call
                if sock is None:
                    break
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    chunk = None  # idle — fall through to keepalive
                except OSError:
                    break
                if chunk is not None:
                    if not chunk:
                        break  # remote closed cleanly
                    try:
                        text = chunk.decode('cp437', errors='replace')
                    except Exception:
                        text = chunk.decode('utf-8', errors='replace')
                    self._emit('term_output', {'data': text})
                now = _time.monotonic()
                if now - last_keepalive >= 20:
                    self._emit('term_keepalive', {'t': now})
                    last_keepalive = now
        finally:
            self.connected = False
            self._emit('term_disconnected', {})
            with _lock:
                _sessions.pop(self.sid, None)

    def _emit(self, event, payload):
        from ..web_app import socketio
        socketio.emit(event, payload, to=self.sid, namespace='/term')


@term_bp.route('/')
@login_required
def index():
    """xterm.js terminal page. Defaults to localhost:2233 (our telnet)."""
    default_host = os.environ.get('BBS_PUBLIC_HOST', 'localhost')
    default_port = int(os.environ.get('TELNET_PORT', '2233'))
    return render_template('terminal/index.html',
                           default_host=default_host,
                           default_port=default_port)


def register_socketio_handlers(socketio):
    @socketio.on('connect', namespace='/term')
    def on_connect():
        if not getattr(current_user, 'is_authenticated', False):
            return False

    @socketio.on('disconnect', namespace='/term')
    def on_disconnect():
        sid = request.sid
        with _lock:
            sess = _sessions.pop(sid, None)
        if sess:
            sess.close()

    @socketio.on('term_open', namespace='/term')
    def on_open(data):
        if not getattr(current_user, 'is_authenticated', False):
            return False
        sid = request.sid
        host = (data.get('host') or 'localhost').strip()
        port = int(data.get('port') or 23)
        use_ssl = bool(data.get('use_ssl'))
        with _lock:
            existing = _sessions.get(sid)
        if existing:
            existing.close()

        sess = _TermSession(sid, host, port, use_ssl)
        if not sess.open():
            return
        with _lock:
            _sessions[sid] = sess
        socketio.start_background_task(sess.run)
        emit('term_connected', {'host': host, 'port': port, 'use_ssl': use_ssl})

    @socketio.on('term_input', namespace='/term')
    def on_input(data):
        sid = request.sid
        with _lock:
            sess = _sessions.get(sid)
        if not sess:
            return
        sess.write(data.get('data') or '')

    @socketio.on('term_close', namespace='/term')
    def on_close():
        sid = request.sid
        with _lock:
            sess = _sessions.pop(sid, None)
        if sess:
            sess.close()
            emit('term_disconnected', {})
