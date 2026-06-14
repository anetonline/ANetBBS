"""
DOSBox TCP-nullmodem bridge.

Allocates a free TCP port, DOSBox connects to it via `serial1=nullmodem
server:127.0.0.1 port:NNNN`, and we ferry bytes between that TCP socket and
the BBS session's reader/writer. Lets vanilla DOSBox (0.74-3+) work for
telnet/SSH BBS doors without needing dosbox-staging's stdio support, snap, or
SIGILL-on-modern-CPU-instructions issues.

Architecture mirrors the binkterm-php project's approach but simpler — we
don't need WebSocket multiplexing since the BBS session is already a PTY
or socket on the parent side.

Usage from launch_door_game's parent process:
    bridge = DosBridge()
    port = bridge.start()                    # picks a free port, listens
    # Generate DOSBox conf with serial1=nullmodem server:127.0.0.1 port:<port>
    # Start DOSBox subprocess
    bridge.bind_pty(master_fd)              # ferry between TCP <-> PTY
    # On door exit, bridge.stop()
"""
import os
import socket
import select
import threading
import logging

logger = logging.getLogger(__name__)


class DosBridge:
    """One TCP nullmodem listener + bidirectional pump to a PTY/file descriptor."""

    BASE_PORT = 5000
    MAX_PORT = 5100

    def __init__(self, host: str = '127.0.0.1'):
        self.host = host
        self.port = None
        self.listener = None
        self._dos_sock = None
        self._pty_fd = None
        self._stop_event = threading.Event()
        self._threads = []
        # last_active timestamp — updated on every byte sent or received.
        # Used by bind_emit's idle-timeout watchdog. Initialized at zero
        # which means "no activity yet" — bind_emit will reset it to now.
        import time
        self._last_active = time.monotonic()

    def start(self) -> int:
        """Allocate a port + listen. DOSBox will dial in shortly."""
        for p in range(self.BASE_PORT, self.MAX_PORT + 1):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.host, p))
                s.listen(1)
                self.listener = s
                self.port = p
                logger.info('DosBridge listening on %s:%d', self.host, p)
                return p
            except OSError:
                continue
        raise RuntimeError(
            'No free TCP port in range %d-%d for DOSBox bridge'
            % (self.BASE_PORT, self.MAX_PORT)
        )

    def accept_async(self):
        """Wait (in a thread) for DOSBox to connect — non-blocking from caller."""
        def _accept():
            try:
                self.listener.settimeout(60)  # 60s for DOSBox to dial in
                self._dos_sock, addr = self.listener.accept()
                logger.info('DOSBox connected from %s', addr)
                self._dos_sock.setblocking(False)
            except (socket.timeout, OSError) as exc:
                logger.warning('DOSBox failed to dial in: %s', exc)
                self._stop_event.set()
        t = threading.Thread(target=_accept, daemon=True, name='dosbridge-accept')
        t.start()
        self._threads.append(t)

    def bind_emit(self, emit_fn, on_close=None, idle_timeout=300):
        """Once DOSBox connects via TCP, pump bytes from the socket to ``emit_fn``.

        ``emit_fn(bytes)`` is called for every chunk DOSBox writes to its
        nullmodem-emulated COM1. The caller can then forward those bytes
        to the BBS user's terminal (xterm.js, telnet/ssh PTY, whatever).

        For input — the BBS sends keystrokes via :meth:`write`, which in
        turn pushes them onto the TCP socket so DOSBox's COM1 receives
        them.

        ``on_close`` is an optional zero-arg callback invoked once when
        the pump exits — i.e., when DOSBox closes its end of the TCP
        nullmodem (which it does on exit). Use it to trigger BBS-side
        session cleanup. This is a more reliable end-of-game signal
        than waiting for PTY EOF, because xvfb-run can keep the PTY's
        slave_fd open via Xvfb past the actual door exit.

        Diagnostic logging: every chunk gets logged at INFO so we can
        confirm bytes are flowing through the bridge. If no chunks are
        seen for 10 seconds after DOSBox connects, that's the strong
        signal that LORD/the door isn't writing to FOSSIL — usually a
        BNU-not-loaded or door-still-in-local-mode problem.
        """
        self._emit_fn = emit_fn
        self._on_close = on_close
        import time

        # Track when bytes last flowed in EITHER direction. Updated here
        # on socket recv, and in :meth:`write` on socket send. The
        # idle-timeout watchdog fires bridge close when (now - last_active)
        # exceeds idle_timeout, which catches stuck-door cases (LORD's
        # exit-loop, infinite "press any key" prompts on hidden screens,
        # etc.) where the BBS would otherwise wait forever.
        self._last_active = time.monotonic()

        def _pump():
            # Wait for DOSBox to connect (set by accept_async)
            for _ in range(120):  # up to 60s
                if self._dos_sock or self._stop_event.is_set():
                    break
                self._stop_event.wait(0.5)
            if not self._dos_sock:
                logger.warning('DOSBox never connected; closing bridge')
                return
            sock = self._dos_sock
            connect_time = time.monotonic()
            total_bytes = 0
            chunks = 0
            silent_warned = False
            try:
                while not self._stop_event.is_set():
                    try:
                        rlist, _, _ = select.select([sock], [], [], 1.0)
                    except (OSError, ValueError):
                        break
                    if sock not in rlist:
                        # Idle tick — warn ONCE if 10s pass with no bytes.
                        if not silent_warned and not chunks and \
                           time.monotonic() - connect_time > 10:
                            logger.warning(
                                'DosBridge[port:%d]: 10s after DOSBox connect '
                                'and zero bytes received. Door is probably not '
                                'writing to FOSSIL/COM1. Check that BNU loaded '
                                '(try `mount d <fossil_dir>; BNU /P1` manually) '
                                'and that the door is configured for FOSSIL.',
                                self.port)
                            silent_warned = True
                        # Idle-timeout check.
                        if (idle_timeout
                                and time.monotonic() - self._last_active
                                > idle_timeout):
                            logger.warning(
                                'DosBridge[port:%d]: idle for >%ds — '
                                'force-closing bridge so caller can SIGTERM '
                                'the stuck door subprocess.',
                                self.port, idle_timeout)
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
                    if chunks <= 5:
                        # Log first 5 chunks verbatim (with byte count + a
                        # 60-byte preview so we can SEE what's flowing).
                        preview = data[:60].decode('cp437', errors='replace')
                        logger.info(
                            'DosBridge[port:%d]: chunk #%d, %d bytes — %r',
                            self.port, chunks, len(data), preview)
                    elif chunks == 6:
                        logger.info(
                            'DosBridge[port:%d]: data flowing (>5 chunks); '
                            'further logging suppressed.', self.port)
                    try:
                        emit_fn(data)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning('DosBridge emit_fn raised: %s', exc)
            finally:
                logger.info(
                    'DosBridge[port:%d]: closing — total %d chunks, %d bytes',
                    self.port, chunks, total_bytes)
                self.stop()
                # Tell the caller the door has disconnected — they may
                # need to fire session cleanup here (PTY EOF won't,
                # because xvfb-run leaves Xvfb holding the slave_fd).
                if on_close is not None:
                    try:
                        on_close()
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning(
                            'DosBridge on_close callback raised: %s', exc)

        t = threading.Thread(target=_pump, daemon=True, name='dosbridge-pump')
        t.start()
        self._threads.append(t)

    def write(self, data: bytes) -> None:
        """Forward ``data`` (a keystroke from the BBS user) onto the TCP
        socket so DOSBox's COM1 receives it. No-op if DOSBox isn't
        connected yet — early keystrokes are just dropped, which is fine
        because DOSBox isn't displaying anything yet either."""
        sock = self._dos_sock
        if not sock:
            return
        try:
            sock.sendall(data)
            # User keystroke counts as activity — keeps the idle-timeout
            # watchdog from killing a door that the user is actively
            # using even if the door isn't responding immediately.
            import time
            self._last_active = time.monotonic()
        except OSError as exc:
            logger.warning('DosBridge write failed: %s', exc)
            self.stop()

    def bind_pty(self, master_fd: int):
        """Legacy: pump bytes between a PTY's master fd and the TCP socket.
        Prefer :meth:`bind_emit` + :meth:`write` for new code — they don't
        require a PTY at all, which is cleaner because dosbox-with-nullmodem
        doesn't need stdio plumbing."""
        self._pty_fd = master_fd

        def _pump():
            for _ in range(120):
                if self._dos_sock or self._stop_event.is_set():
                    break
                self._stop_event.wait(0.5)
            if not self._dos_sock:
                logger.warning('DOSBox never connected; closing bridge')
                return
            sock = self._dos_sock
            try:
                while not self._stop_event.is_set():
                    rlist, _, _ = select.select([sock, master_fd], [], [], 1.0)
                    if sock in rlist:
                        try:
                            data = sock.recv(4096)
                        except OSError:
                            break
                        if not data:
                            break
                        try:
                            os.write(master_fd, data)
                        except OSError:
                            break
                    if master_fd in rlist:
                        try:
                            data = os.read(master_fd, 4096)
                        except OSError:
                            break
                        if not data:
                            break
                        try:
                            sock.sendall(data)
                        except OSError:
                            break
            finally:
                self.stop()

        t = threading.Thread(target=_pump, daemon=True, name='dosbridge-pump-pty')
        t.start()
        self._threads.append(t)

    def stop(self):
        """Close everything."""
        self._stop_event.set()
        for s in (self._dos_sock, self.listener):
            if s:
                try: s.close()
                except OSError: pass
        self._dos_sock = None
        self.listener = None


class DosEmuBridge:
    """Bridge between dosemu2's COM1 PTY master fd and the BBS session.

    dosemu2 is configured with ``$_com1 = "/dev/pts/N"`` pointing to the slave
    end of a PTY pair we create before forking. The FOSSIL driver (BNU) sees
    that device as COM1. All door serial I/O flows through the PTY to/from this
    bridge — completely separate from dosemu2's keyboard stdin — no competition,
    no dropped keystrokes.

    ``com_slave_fd`` (optional): the slave end of the same PTY pair.  Keeping it
    open in the bridge prevents a race where the parent closes the slave before
    dosemu2 opens ``/dev/pts/N``, which would cause an immediate EIO on the
    master and kill the pump before dosemu2 starts.  The bridge closes it in
    ``stop()`` so the master sees EIO only after real cleanup is triggered.

    Interface is intentionally identical to :class:`DosBridge` so callers can
    treat both interchangeably.
    """

    def __init__(self, com_master_fd: int, com_slave_fd: int = None):
        self._fd = com_master_fd
        self._slave_fd = com_slave_fd
        self._stop_event = threading.Event()
        self._threads = []
        import time
        self._last_active = time.monotonic()

    def bind_emit(self, emit_fn, on_close=None, idle_timeout=300):
        """Start a thread that reads from the COM1 PTY master and calls
        ``emit_fn(bytes)`` for each chunk. Stops when dosemu2 exits (EIO on
        the PTY master). ``on_close`` is called once when the pump exits."""
        import time
        self._last_active = time.monotonic()

        def _pump():
            fd = self._fd
            total_bytes = 0
            chunks = 0
            try:
                # Use the same blocking-read pattern as _pty_reader.
                # select.select() on a PTY fd under eventlet/epoll can
                # silently miss readability events; blocking os.read() goes
                # through eventlet's hub correctly (same as socket.recv).
                while not self._stop_event.is_set():
                    try:
                        data = os.read(fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    self._last_active = time.monotonic()
                    chunks += 1
                    total_bytes += len(data)
                    if chunks <= 5:
                        preview = data[:60].decode('cp437', errors='replace')
                        logger.info(
                            'DosEmuBridge: chunk #%d, %d bytes — %r',
                            chunks, len(data), preview)
                    elif chunks == 6:
                        logger.info(
                            'DosEmuBridge: data flowing (>5 chunks); '
                            'further logging suppressed.')
                    try:
                        emit_fn(data)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning('DosEmuBridge emit_fn raised: %s', exc)
            finally:
                logger.info('DosEmuBridge: closing — total %d chunks, %d bytes',
                            chunks, total_bytes)
                self.stop()
                if on_close is not None:
                    try:
                        on_close()
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning('DosEmuBridge on_close raised: %s', exc)

        t = threading.Thread(target=_pump, daemon=True, name='dosemu-bridge-pump')
        t.start()
        self._threads.append(t)

    def write(self, data: bytes) -> None:
        """Forward user keystrokes to dosemu2's COM1."""
        try:
            os.write(self._fd, data)
            import time
            self._last_active = time.monotonic()
        except OSError as exc:
            logger.warning('DosEmuBridge write failed: %s', exc)
            self.stop()

    def stop(self):
        self._stop_event.set()
        for fd in (self._fd, self._slave_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._slave_fd = None
