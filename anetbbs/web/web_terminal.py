# anetbbs/web/web_terminal.py
"""
Browser-based terminal — xterm.js front-end, socketio back-end.

Lets a web user open a telnet/SSH/rlogin session to ANY host (defaults to
the local BBS) right inside the browser tab — no native client required.
This is the closest analog to fTelnet / SyncTERM-in-browser.

Architecture:
    Browser <—socketio—> Flask process <—TCP—> Remote BBS

Each browser tab gets its own outbound TCP socket; bytes flow both ways
through the socketio namespace `/term`.
"""
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
        try:
            sock = socket.create_connection((self.host, self.port), timeout=15)
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
