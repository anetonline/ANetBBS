"""
Inbound MSP listener — RFC 1312 server.

Runs on TCP port 18 (configurable). For each incoming connection:
  1. Read until 5 NUL fields are present or the client closes
  2. Parse the packet
  3. Look up the recipient locally (by username, case-insensitive)
  4. Persist to the InstantMessage table (so it appears in the user's
     IM inbox even if they're offline)
  5. Push a real-time toast to any browser tab they have open via
     SocketIO (best-effort; absence of socketio is fine)
  6. Close the connection silently per spec

The listener runs as a daemon thread started from anetbbs.web_app.create_app.
"""
import logging
import socket
import threading
import time
from datetime import datetime

from .protocol import decode, MAX_MESSAGE, MSP_DEFAULT_PORT, NUM_FIELDS

logger = logging.getLogger(__name__)

# Real gap found in a security audit: unlike systat.py's UDP responder
# (already rate-limited after an earlier audit round), this TCP
# listener had no cap on inbound message volume or concurrent
# connections at all -- an unauthenticated remote peer could open
# unlimited connections and persist unlimited InstantMessage rows to
# any local username it can guess (inbox flooding / unbounded DB
# growth), and a client trickling one byte just often enough to reset
# the 20s idle timeout could hold its handler thread open forever
# (slow-trickle DoS against the same process that runs the rest of the
# web app). Three independent bounds, matching systat.py's per-IP +
# global shape for the rate limit:
_MSP_PER_IP_LIMIT = 20
_MSP_PER_IP_WINDOW = 600      # 20 messages / 10 min / source IP
_MSP_GLOBAL_LIMIT = 200
_MSP_GLOBAL_WINDOW = 600      # 200 messages / 10 min, site-wide
_MSP_MAX_CONCURRENT = 40      # total in-flight handler threads
_MSP_ABSOLUTE_DEADLINE_SEC = 30  # hard cap regardless of idle resets

_concurrency_sem = threading.Semaphore(_MSP_MAX_CONCURRENT)

_server_thread = None
_stop_event = threading.Event()
_listen_sock = None


def start_msp_server(app):
    """Spawn the MSP listener thread. Idempotent."""
    global _server_thread
    if not app.config.get('MSP_ENABLED', True):
        logger.info('MSP server disabled by configuration')
        return
    if _server_thread and _server_thread.is_alive():
        logger.warning('MSP server already running')
        return
    _stop_event.clear()
    _server_thread = threading.Thread(
        target=_serve_loop, args=(app,), daemon=True, name='msp-server')
    _server_thread.start()


def stop_msp_server():
    """Signal the MSP server to stop and close the listening socket."""
    _stop_event.set()
    if _listen_sock is not None:
        try:
            _listen_sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _serve_loop(app):
    global _listen_sock
    bind_host = app.config.get('MSP_BIND_HOST', '0.0.0.0')
    port = app.config.get('MSP_PORT', MSP_DEFAULT_PORT)
    backlog = 16

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        sock.listen(backlog)
        sock.settimeout(1.0)
    except OSError as exc:
        logger.error('MSP: cannot bind %s:%s — %s', bind_host, port, exc)
        return
    _listen_sock = sock
    logger.info('MSP server listening on %s:%s', bind_host, port)

    while not _stop_event.is_set():
        try:
            client, addr = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        # Cap total concurrent handler threads (non-blocking acquire --
        # if we're already at the cap, refuse this connection outright
        # rather than spawning a thread that blocks waiting for a slot,
        # which would just move the pileup from "threads" to "threads
        # blocked on a semaphore").
        if not _concurrency_sem.acquire(blocking=False):
            logger.info('MSP: at max concurrent connections (%d), '
                        'refusing %s', _MSP_MAX_CONCURRENT, addr[0])
            try:
                client.close()
            except OSError:
                pass
            continue
        # Handle each connection in its own short-lived thread so a slow
        # peer can't block subsequent senders.
        t = threading.Thread(
            target=_handle_connection, args=(app, client, addr),
            daemon=True, name=f'msp-conn-{addr[0]}')
        t.start()

    try:
        sock.close()
    except OSError:
        pass
    logger.info('MSP server stopped')


def _handle_connection(app, client, addr):
    try:
        client.settimeout(20.0)
        # Read until we have all 7 NULs (RFC 1312 MSP-2) or the
        # connection closes or we hit a generous upper bound. The 20s
        # socket timeout only resets on each successful recv() -- a
        # peer trickling a byte every ~19s would never trip it and
        # could hold this handler (and its concurrency-semaphore slot)
        # open indefinitely, so also enforce an absolute wall-clock
        # deadline across the whole read, independent of idle resets.
        deadline = time.monotonic() + _MSP_ABSOLUTE_DEADLINE_SEC
        cap = (3 * MAX_MESSAGE) + (4 * 64) + 32
        buf = bytearray()
        while len(buf) < cap and buf.count(b'\x00') < NUM_FIELDS:
            if time.monotonic() >= deadline:
                logger.info('MSP: connection from %s exceeded %ds deadline, '
                            'closing', addr[0], _MSP_ABSOLUTE_DEADLINE_SEC)
                break
            try:
                chunk = client.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf.extend(chunk)
        if not buf:
            return

        try:
            msg = decode(bytes(buf))
        except ValueError as exc:
            logger.info('MSP: bad packet from %s: %s. raw=%r',
                        addr, exc, bytes(buf)[:512])
            if buf and chr(buf[0]) == 'A':
                try: client.sendall(b'-bad-packet\n')
                except OSError: pass
            return

        from ..features.rate_limit import _check as _rate_limit_check
        if not (_rate_limit_check(f'msp-inbound:{addr[0]}', _MSP_PER_IP_LIMIT,
                                  _MSP_PER_IP_WINDOW)
                and _rate_limit_check('msp-inbound:global', _MSP_GLOBAL_LIMIT,
                                     _MSP_GLOBAL_WINDOW)):
            logger.info('MSP: rate-limited inbound message from %s', addr[0])
            if msg.get('msg_type') == 'A':
                try: client.sendall(b'-rate-limited\n')
                except OSError: pass
            return

        delivered = _deliver(app, msg, peer_host=addr[0])
        # RFC 1312: acknowledged messages get a reply line.
        if msg.get('msg_type') == 'A':
            try:
                client.sendall(b'+\n' if delivered else b'-no-such-user\n')
            except OSError:
                pass
    finally:
        _concurrency_sem.release()
        try:
            client.close()
        except OSError:
            pass


def _deliver(app, msg: dict, peer_host: str) -> bool:
    """Persist the inbound IM and push it via SocketIO if anyone's online.

    Returns True if delivered to a local user, False otherwise.
    """
    with app.app_context():
        from ..models import db, User, InstantMessage

        # Recipient may be "user" or "user@host" — strip the @host part since
        # routing has already landed the packet at us.
        recipient_raw = (msg.get('recipient') or '').strip()
        recipient_name = recipient_raw.split('@', 1)[0]
        sender_label = (msg.get('sender') or '').strip()
        body = (msg.get('message') or '').strip()

        if not recipient_name or not body:
            logger.info('MSP from %s: empty recipient/message — dropping. '
                        'fields=%r',
                        peer_host, msg)
            return False

        user = (User.query
                .filter(db.func.lower(User.username) == recipient_name.lower())
                .first())
        if user is None:
            user = (User.query
                    .filter(db.func.lower(User.display_name)
                            == recipient_name.lower())
                    .first())
        if user is None:
            logger.info('MSP from %s for unknown user %r — dropping',
                        peer_host, recipient_name)
            return False

        im = InstantMessage(
            recipient_id=user.id,
            sender_label=sender_label[:120],
            sender_host=peer_host,
            body=body,
            received_at=datetime.utcnow(),
            is_read=False,
            origin='msp',
        )
        db.session.add(im)
        db.session.commit()
        logger.info('MSP delivered to %s (%d bytes from %s @ %s)',
                    user.username, len(body), sender_label, peer_host)

        # Best-effort live toast via SocketIO (no-op if eventlet/socketio
        # isn't loaded — IRC chat already pulls in socketio so this is
        # usually available).
        #
        # Noted in a security audit: no client-side `socket.on('instant_
        # message', ...)` listener exists anywhere in this codebase yet
        # (this emit is currently dead on the wire), but `sender`/`body`
        # here are raw, unauthenticated text from a remote MSP peer.
        # Whoever wires up the client-side toast MUST render it via
        # textContent/an escaping template helper, never innerHTML with
        # these fields interpolated directly — the server-rendered
        # inbox already relies on Jinja's autoescaping for the exact
        # same fields, and a client-side toast is the same trust
        # boundary.
        try:
            sio = app.extensions.get('socketio')
            if sio is not None:
                sio.emit('instant_message', {
                    'id': im.id,
                    'sender': sender_label or peer_host,
                    'body': body[:500],
                }, room=f'user-{user.id}')
        except Exception:
            pass

        return True
