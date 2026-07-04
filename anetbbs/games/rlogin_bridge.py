"""Outbound rlogin connection bridged to the BBS user's terminal.

Used by ``door_rlogin`` game type (Synchronet-style remote door servers,
DoorParty, etc.). The BBS opens a TCP connection to the remote rlogin
server, sends the standard 4-string handshake (NUL + client-user-name
+ NUL + password + NUL + terminal/speed + NUL), then bridges bytes
between the socket and the user's BBS session.

Same shape as ``dos_bridge.DosBridge`` so ``DoorSession`` can store
either kind of bridge in its ``dos_bridge`` slot — both expose
``write(bytes)``, ``stop()``, and a thread that pumps remote bytes
to an ``emit_fn`` callback.

Difference vs DosBridge: we're the CLIENT (no listener, we connect
out), and there's no DOSBox subprocess — the door is the remote BBS
on the other side of the TCP connection.
"""
import socket
import select
import threading
import logging
import time

logger = logging.getLogger(__name__)


class RloginConnection:
    """One outbound rlogin TCP connection + bidirectional pump to a callback."""

    def __init__(self, host: str, port: int, client_user: str,
                 password: str, terminal: str = 'xterm/57600'):
        self.host = host
        self.port = port
        self.client_user = client_user
        self.password = password
        self.terminal = terminal
        self._sock = None
        self._stop_event = threading.Event()
        self._threads = []
        self._last_active = time.monotonic()

    def connect(self, timeout: float = 15) -> None:
        """Open TCP and send the rlogin handshake. Blocking — call from
        the launch path before bind_emit. Raises OSError on failure."""
        sock = socket.create_connection((self.host, self.port),
                                         timeout=timeout)
        sock.setblocking(True)
        # Synchronet rlogin handshake — 4 NUL-terminated strings.
        # Note the field order: Synchronet's BBS-rlogin treats the
        # client-user-name slot as the AUTH field (password) and the
        # server-user-name slot as the BBS login user. This is opposite
        # of RFC 1282 semantics for non-BBS rlogin, but it's what
        # Synchronet's rlogin server expects from inbound game-server
        # connections (verified against a-net-online.lol's setup).
        # 1st (after initial NUL): client-user-name = PASSWORD
        # 2nd:                     server-user-name = the BBS username
        # 3rd:                     terminal/speed (e.g. xterm/57600
        #                          or xtrn=LORD408/57600 for direct
        #                          launch into a specific door)
        handshake = (b'\x00'
                     + self.password.encode('utf-8', errors='replace')
                     + b'\x00'
                     + self.client_user.encode('utf-8', errors='replace')
                     + b'\x00'
                     + self.terminal.encode('utf-8', errors='replace')
                     + b'\x00')
        sock.sendall(handshake)
        sock.setblocking(False)
        self._sock = sock
        self._last_active = time.monotonic()
        logger.info('Rlogin connected to %s:%d as %r',
                    self.host, self.port, self.client_user)

    def bind_emit(self, emit_fn, on_close=None, idle_timeout: int = 300):
        """Pump bytes from the rlogin socket to ``emit_fn``.

        Same contract as :meth:`dos_bridge.DosBridge.bind_emit`.
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
                        # Idle timeout check.
                        if (idle_timeout
                                and time.monotonic() - self._last_active
                                > idle_timeout):
                            logger.warning(
                                'Rlogin[%s:%d]: idle for >%ds — closing.',
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
                    try:
                        emit_fn(data)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning('Rlogin emit_fn raised: %s', exc)
            finally:
                logger.info('Rlogin[%s:%d]: closing — %d chunks, %d bytes',
                            self.host, self.port, chunks, total_bytes)
                self.stop()
                if on_close is not None:
                    try:
                        on_close()
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning('Rlogin on_close raised: %s', exc)

        t = threading.Thread(target=_pump, daemon=True,
                             name=f'rlogin-pump-{self.host}:{self.port}')
        t.start()
        self._threads.append(t)

    def write(self, data: bytes) -> None:
        """Send a keystroke (or any bytes) onto the rlogin socket."""
        sock = self._sock
        if not sock:
            return
        if isinstance(data, str):
            data = data.encode('utf-8', errors='replace')
        try:
            sock.sendall(data)
            self._last_active = time.monotonic()
        except OSError as exc:
            logger.warning('Rlogin write failed: %s', exc)
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


def expand_user_template(template: str, username: str, alias: str) -> str:
    """Substitute @USER@ / @ALIAS@ / %U / %u in a user-template string.

    Used by both the terminal and web rlogin launch paths so the format
    accepted in the Game admin form is consistent everywhere.
    """
    return (template
            .replace('@USER@', username)
            .replace('@ALIAS@', alias)
            .replace('%U', username)
            .replace('%u', username))


def build_client_user(user_template: str, username: str, alias: str,
                       bbs_tag: str = '') -> str:
    """Expand USER_TEMPLATE and append the optional BBS tag suffix.

    Sent as "username-TAG" (hyphen-joined, no space) in the
    client-user-name slot — the Mystic-style convention ANetBBS
    presents as, not Synchronet's own native `?rlogin -s-TAG` client
    convention (space + "-s-" prefix), which only applies when a real
    Synchronet BBS calls another real Synchronet BBS.

    The tag is kept in its own `Game.rlogin_bbs_tag` field rather than
    typed into command_line_args' USER_TEMPLATE text purely for admin-
    form clarity (a separate labeled box beats asking the sysop to
    hand-assemble a combined string) — not because the wire format
    itself requires it.

    Used by both the terminal and web rlogin launch paths so this only
    needs to be gotten right once.
    """
    client_user = expand_user_template(user_template, username, alias)
    tag = (bbs_tag or '').strip()
    if tag:
        client_user = f'{client_user}-{tag}'
    return client_user
