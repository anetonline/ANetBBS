"""Outbound telnet connection bridged to the BBS user's terminal.

Used by ``door_telnet`` game type (external telnet-only game servers —
TWGS/Trade Wars Game Server and similar, which have no rlogin
pre-authentication and expect the user to log in interactively on the
remote side, exactly like connecting with any telnet client directly).

Same shape as ``rlogin_bridge.RloginConnection`` (``connect()``,
``bind_emit()``, ``write()``, ``stop()``) so ``DoorSession`` can store
either kind of bridge in its ``dos_bridge`` slot.

Difference vs rlogin: no handshake to send — telnet servers expect a
raw NVT (Network Virtual Terminal) byte stream from the client, with
option negotiation (IAC WILL/WONT/DO/DONT — RFC 854) happening inline
in that same stream rather than as a fixed preamble. This module is a
"dumb" telnet client: it refuses every option the remote proposes
(replies DONT/WONT to everything) so the remote settles into plain
NVT/character mode instead of hanging around waiting for negotiated
features we don't implement, and strips negotiation bytes out of what
the user actually sees.
"""
import socket
import select
import threading
import logging
import time

logger = logging.getLogger(__name__)

# Telnet protocol constants (RFC 854).
_IAC, _DONT, _DO, _WONT, _WILL, _SB, _SE = 255, 254, 253, 252, 251, 250, 240


class TelnetIACFilter:
    """Minimal, stateful Telnet IAC option-negotiation filter.

    Feed raw bytes from the remote into :meth:`process`; get back
    ``(display_bytes, reply_bytes)`` — ``display_bytes`` is what the
    user should see (IAC sequences stripped out), ``reply_bytes`` is
    what the caller should write back to the remote socket (DONT/WONT
    refusals for any WILL/DO the remote proposed). A bare ``IAC IAC``
    (escaped literal 0xFF) is preserved in ``display_bytes`` as one
    0xFF byte. Subnegotiation blocks (``IAC SB ... IAC SE``) are
    discarded entirely — this client doesn't support any of them.

    Stateful across calls, so an IAC sequence split across two
    ``recv()``/``read()`` chunks is still parsed correctly.
    """

    def __init__(self):
        self._state = 'data'   # data | iac | negotiate | subneg | subneg_iac
        self._cmd = 0

    def process(self, data: bytes):
        out = bytearray()
        reply = bytearray()
        for b in data:
            if self._state == 'data':
                if b == _IAC:
                    self._state = 'iac'
                else:
                    out.append(b)
            elif self._state == 'iac':
                if b == _IAC:
                    out.append(b)
                    self._state = 'data'
                elif b in (_WILL, _WONT, _DO, _DONT):
                    self._cmd = b
                    self._state = 'negotiate'
                elif b == _SB:
                    self._state = 'subneg'
                else:
                    # Single-byte IAC command (NOP, GA, AYT, ...) -- discard.
                    self._state = 'data'
            elif self._state == 'negotiate':
                if self._cmd == _WILL:
                    reply += bytes([_IAC, _DONT, b])
                elif self._cmd == _DO:
                    reply += bytes([_IAC, _WONT, b])
                # WONT/DONT from the remote needs no reply.
                self._state = 'data'
            elif self._state == 'subneg':
                if b == _IAC:
                    self._state = 'subneg_iac'
            elif self._state == 'subneg_iac':
                self._state = 'data' if b == _SE else 'subneg'
        return bytes(out), bytes(reply)


class TelnetConnection:
    """One outbound telnet TCP connection + bidirectional pump to a callback."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock = None
        self._stop_event = threading.Event()
        self._threads = []
        self._last_active = time.monotonic()
        self._iac = TelnetIACFilter()

    def connect(self, timeout: float = 15) -> None:
        """Open TCP. No handshake — telnet servers just want a raw NVT
        stream. Blocking — call from the launch path before bind_emit.
        Raises OSError on failure."""
        sock = socket.create_connection((self.host, self.port),
                                         timeout=timeout)
        sock.setblocking(False)
        self._sock = sock
        self._last_active = time.monotonic()
        logger.info('Telnet connected to %s:%d', self.host, self.port)

    def bind_emit(self, emit_fn, on_close=None, idle_timeout: int = 300):
        """Pump bytes from the telnet socket to ``emit_fn``.

        Same contract as :meth:`rlogin_bridge.RloginConnection.bind_emit`.
        """
        def _pump():
            sock = self._sock
            chunks = 0
            total_bytes = 0
            try:
                while not self._stop_event.is_set():
                    if sock is None:
                        break
                    try:
                        rlist, _, _ = select.select([sock], [], [], 1.0)
                    except (OSError, ValueError):
                        break
                    if sock not in rlist:
                        if (idle_timeout
                                and time.monotonic() - self._last_active
                                > idle_timeout):
                            logger.warning(
                                'Telnet[%s:%d]: idle for >%ds — closing.',
                                self.host, self.port, idle_timeout)
                            break
                        continue
                    try:
                        data = sock.recv(4096)
                    except OSError:
                        break
                    if not data:
                        break
                    self._last_active = time.monotonic()
                    chunks += 1
                    total_bytes += len(data)
                    display, reply = self._iac.process(data)
                    if reply:
                        try:
                            sock.sendall(reply)
                        except OSError:
                            break
                    if display:
                        try:
                            emit_fn(display)
                        except Exception as exc:  # pylint: disable=broad-except
                            logger.warning('Telnet emit_fn raised: %s', exc)
            finally:
                logger.info('Telnet[%s:%d]: closing — %d chunks, %d bytes',
                            self.host, self.port, chunks, total_bytes)
                self.stop()
                if on_close is not None:
                    try:
                        on_close()
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning('Telnet on_close raised: %s', exc)

        t = threading.Thread(target=_pump, daemon=True,
                             name=f'telnet-pump-{self.host}:{self.port}')
        t.start()
        self._threads.append(t)

    def write(self, data: bytes) -> None:
        """Send a keystroke (or any bytes) onto the telnet socket."""
        sock = self._sock
        if not sock:
            return
        if isinstance(data, str):
            data = data.encode('utf-8', errors='replace')
        try:
            sock.sendall(data)
            self._last_active = time.monotonic()
        except OSError as exc:
            logger.warning('Telnet write failed: %s', exc)
            self.stop()

    def stop(self) -> None:
        """Close the TCP connection."""
        self._stop_event.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
