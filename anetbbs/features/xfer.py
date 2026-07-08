# anetbbs/features/xfer.py
"""
Terminal file transfer via ZMODEM, XMODEM, and YMODEM.

Requires lrzsz on the server: apt install lrzsz
  sz / rz  — ZMODEM send / receive
  sb / rb  — YMODEM (batch) send / receive
  sx / rx  — XMODEM send / receive

ZMODEM sends use --binary to prevent newline translation of binary data.
--escape is intentionally omitted: it causes sz to send ZSINIT to negotiate
extended escaping, but many terminal emulators (including SyncTERM) respond
with ZRINIT instead of ZACK, breaking the handshake. XON/XOFF/DLE are still
escaped by default without the flag.

Reading from session.reader directly (not through handle_telnet_command)
preserves the raw binary stream needed for protocol transfers.
"""
import asyncio
import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)

# Protocol descriptors
_PROTOCOLS = {
    'zmodem': {
        'name':       'ZMODEM',
        'send_bin':   'sz',
        'recv_bin':   'rz',
        'send_flags': ['--binary'],
        'recv_flags': ['--escape'],
    },
    'ymodem': {
        'name':       'YMODEM',
        'send_bin':   'sb',
        'recv_bin':   'rb',
        'send_flags': ['--escape', '--binary'],
        'recv_flags': ['--escape'],
    },
    'xmodem': {
        'name':       'XMODEM',
        'send_bin':   'sx',
        'recv_bin':   'rx',
        'send_flags': ['--binary'],
        'recv_flags': [],
    },
}


def available_protocols():
    """Return ordered list of protocol keys whose binaries are installed."""
    return [k for k in ('zmodem', 'ymodem', 'xmodem')
            if shutil.which(_PROTOCOLS[k]['send_bin'])
            and shutil.which(_PROTOCOLS[k]['recv_bin'])]


# --- Telnet framing on the transfer stream -------------------------------
#
# The bridge below moves raw protocol bytes between the socket and sz/rz.
# On a telnet connection, 0xFF is IAC, and a compliant telnet client
# doubles it (0xFF -> 0xFF 0xFF) on the wire — required by RFC 854, and
# still required in BINARY mode by RFC 856 — and may interleave option
# commands. Those bytes must be collapsed/removed before they reach rz (or
# the ZMODEM stream is corrupt) and re-inserted on the way out. Without
# this, a compliant telnet client's doubled IAC reached rz as a spurious
# extra 0xFF and corrupted the transfer; only a client doing no telnet
# processing (a plain raw socket) happened to work.
#
# THIS MUST ONLY BE APPLIED TO TELNET SESSIONS. SSH and rlogin channels
# have no IAC/telnet-framing concept at all -- they're already 8-bit
# clean at the transport layer (see core/ssh_server.py's
# _SshStreamWriter, which just hands bytes straight to asyncssh).
# Applying this escaping unconditionally would "fix" telnet by breaking
# SSH/rlogin instead: literal 0xFF bytes in the file would get doubled
# on the wire, the BINARY/SGA negotiation sequence would get written
# into the SSH channel's data stream as if it were file content, and any
# byte sequence in an uploaded file that happens to resemble a telnet
# command would get silently stripped. _is_telnet_session() below gates
# every telnet-specific call so SSH/rlogin transfers pass bytes through
# completely unmodified, exactly as they did before this feature existed.
_IAC = 0xFF
_SB, _SE = 0xFA, 0xF0
_WILL, _WONT, _DO, _DONT = 0xFB, 0xFC, 0xFD, 0xFE
_OPT_BINARY, _OPT_SGA = 0x00, 0x03


def _is_telnet_session(session) -> bool:
    """True if this session's transport is telnet -- i.e. needs IAC
    framing at all. Matches the protocol-detection convention already
    used in core/session.py (checking the writer class name), since
    that's the only signal a generic BBSSession exposes about which
    transport it's actually running over."""
    wname = type(session.writer).__name__.lower()
    return 'ssh' not in wname and 'rlogin' not in wname


def _telnet_escape(data: bytes) -> bytes:
    """Double IAC so the peer reads a literal 0xFF as data, not a command."""
    return data.replace(b'\xff', b'\xff\xff')


class _TelnetUnescaper:
    """Remove telnet framing from an inbound stream so the raw protocol sees
    the original bytes: collapse doubled IAC back to 0xFF and drop option
    commands (WILL/WONT/DO/DONT and SB..SE). State is carried across reads
    because a sequence can straddle a 4 KiB chunk boundary."""

    def __init__(self):
        self._pending = b''

    def feed(self, data: bytes) -> bytes:
        buf = self._pending + data
        self._pending = b''
        out = bytearray()
        i, n = 0, len(buf)
        while i < n:
            b = buf[i]
            if b != _IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= n:                  # IAC, next byte not here yet
                self._pending = buf[i:]
                break
            cmd = buf[i + 1]
            if cmd == _IAC:                 # doubled IAC -> literal 0xFF
                out.append(_IAC)
                i += 2
            elif cmd in (_WILL, _WONT, _DO, _DONT):
                if i + 2 >= n:              # option byte not here yet
                    self._pending = buf[i:]
                    break
                i += 3                      # drop IAC <cmd> <opt>
            elif cmd == _SB:                # drop subnegotiation to IAC SE
                j = i + 2
                while j + 1 < n and not (buf[j] == _IAC and buf[j + 1] == _SE):
                    j += 1
                if j + 1 >= n:              # SE not seen yet
                    self._pending = buf[i:]
                    break
                i = j + 2
            else:                           # 2-byte command -> drop IAC <cmd>
                i += 2
        return bytes(out)


async def _negotiate_binary(session, enable: bool):
    """Ask the client for an 8-bit-clean channel before a transfer (and
    restore NVT after). WILL/DO BINARY plus DO SGA put a compliant client
    into raw character mode; WONT/DONT BINARY reverts. Client replies are
    consumed by the _TelnetUnescaper on the read side, so they never reach
    the protocol program.

    Caller must only invoke this for telnet sessions -- see
    _is_telnet_session()."""
    if enable:
        seq = bytes([_IAC, _WILL, _OPT_BINARY,
                     _IAC, _DO, _OPT_BINARY,
                     _IAC, _DO, _OPT_SGA])
    else:
        seq = bytes([_IAC, _WONT, _OPT_BINARY,
                     _IAC, _DONT, _OPT_BINARY])
    try:
        session.writer.write(seq)
        await session.writer.drain()
    except Exception:
        pass  # best-effort; the connection may already be gone


async def send_file(session, filepath: str, protocol: str = 'zmodem') -> bool:
    """Send *filepath* to the connected terminal user.

    Bypasses session.handle_telnet_command so raw binary bytes flow
    unmodified.  Returns True on clean exit, False on error/timeout.
    """
    cfg = _PROTOCOLS.get(protocol) or _PROTOCOLS['zmodem']
    cmd_path = shutil.which(cfg['send_bin'])
    if not cmd_path:
        await session.write(
            f"\r\n[{cfg['name']}] '{cfg['send_bin']}' not found -"
            f"sysop must install lrzsz (apt install lrzsz).\r\n")
        return False

    cmd = [cmd_path] + cfg['send_flags'] + [filepath]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        await session.write(f"\r\nFailed to launch {cfg['send_bin']}: {exc}\r\n")
        return False

    # Telnet only: ask the client for 8-bit-clean mode before the
    # transfer. This is best-effort; the escape/unescape codec below is
    # what actually guarantees byte-transparency, not the client's
    # BINARY agreement. Never done for SSH/rlogin -- see the module
    # docstring above _is_telnet_session().
    is_telnet = _is_telnet_session(session)
    if is_telnet:
        await _negotiate_binary(session, True)

    transfer_done = asyncio.Event()

    async def _proc_to_session():
        """Forward sz stdout → writer, doubling IAC for a telnet peer."""
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                session.writer.write(_telnet_escape(chunk) if is_telnet else chunk)
                await session.writer.drain()
        except Exception as exc:
            logger.debug("xfer send proc→session: %s", exc)
        finally:
            transfer_done.set()

    async def _session_to_proc():
        """Forward raw reader → sz stdin.

        Uses asyncio.wait (not wait_for) to avoid cancelling the pending
        read on each poll timeout.  asyncio.wait_for cancels the inner
        coroutine on timeout but does not await its cleanup, which can
        leave StreamReader._waiter non-None and cause RuntimeError on the
        next read after the transfer ends.  Here one read task lives for
        its full lifetime; we cancel it explicitly in the finally block
        and await the cancellation so the StreamReader is clean before
        the caller's read_line() runs.
        """
        unesc = _TelnetUnescaper() if is_telnet else None
        read_fut = asyncio.ensure_future(session.reader.read(4096))
        try:
            while True:
                try:
                    done, _ = await asyncio.wait({read_fut}, timeout=0.5)
                except asyncio.CancelledError:
                    break

                if read_fut in done:
                    try:
                        chunk = read_fut.result()
                    except Exception as exc:
                        logger.debug("xfer send session→proc read: %s", exc)
                        break
                    if not chunk:
                        break
                    clean = unesc.feed(chunk) if unesc is not None else chunk
                    if clean:
                        try:
                            proc.stdin.write(clean)
                            await proc.stdin.drain()
                        except Exception as exc:
                            logger.debug("xfer send session→proc write: %s", exc)
                            break
                    if transfer_done.is_set():
                        break
                    read_fut = asyncio.ensure_future(session.reader.read(4096))
                elif transfer_done.is_set():
                    break
        except Exception:
            pass
        finally:
            if not read_fut.done():
                read_fut.cancel()
                try:
                    await read_fut
                except (asyncio.CancelledError, Exception):
                    pass

    t_out = asyncio.ensure_future(_proc_to_session())
    t_in  = asyncio.ensure_future(_session_to_proc())

    try:
        await asyncio.wait_for(proc.wait(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        await session.write("\r\n[Transfer timed out - cancelled.]\r\n")
        t_out.cancel(); t_in.cancel()
        return False
    finally:
        t_out.cancel(); t_in.cancel()
        try:
            proc.stdin.close()
        except Exception:
            pass
        await asyncio.gather(t_out, t_in, return_exceptions=True)
        if is_telnet:
            await _negotiate_binary(session, False)

    return proc.returncode == 0


async def recv_file(session, protocol: str = 'zmodem') -> list:
    """Receive file(s) from the terminal user.

    Runs rz/rb/rx in a temp directory.  Returns a list of
    (original_filename, temp_filepath) tuples.  The *caller* must move
    the file to its final location and clean up the temp directory.

    Returns an empty list on failure.
    """
    cfg = _PROTOCOLS.get(protocol) or _PROTOCOLS['zmodem']
    cmd_path = shutil.which(cfg['recv_bin'])
    if not cmd_path:
        await session.write(
            f"\r\n[{cfg['name']}] '{cfg['recv_bin']}' not found -"
            f"sysop must install lrzsz (apt install lrzsz).\r\n")
        return []

    tmpdir = tempfile.mkdtemp(prefix='anetbbs_upload_')
    cmd = [cmd_path] + cfg['recv_flags']

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=tmpdir,
        )
    except OSError as exc:
        await session.write(f"\r\nFailed to launch {cfg['recv_bin']}: {exc}\r\n")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return []

    # Telnet only -- see send_file() above and the module docstring
    # above _is_telnet_session() for why this must not run for SSH/rlogin.
    is_telnet = _is_telnet_session(session)
    if is_telnet:
        await _negotiate_binary(session, True)

    transfer_done = asyncio.Event()

    async def _proc_to_session():
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                session.writer.write(_telnet_escape(chunk) if is_telnet else chunk)
                await session.writer.drain()
        except Exception as exc:
            logger.debug("xfer recv proc→session: %s", exc)
        finally:
            transfer_done.set()

    async def _session_to_proc():
        unesc = _TelnetUnescaper() if is_telnet else None
        read_fut = asyncio.ensure_future(session.reader.read(4096))
        try:
            while True:
                try:
                    done, _ = await asyncio.wait({read_fut}, timeout=0.5)
                except asyncio.CancelledError:
                    break

                if read_fut in done:
                    try:
                        chunk = read_fut.result()
                    except Exception as exc:
                        logger.debug("xfer recv session→proc read: %s", exc)
                        break
                    if not chunk:
                        break
                    clean = unesc.feed(chunk) if unesc is not None else chunk
                    if clean:
                        try:
                            proc.stdin.write(clean)
                            await proc.stdin.drain()
                        except Exception as exc:
                            logger.debug("xfer recv session→proc write: %s", exc)
                            break
                    if transfer_done.is_set():
                        break
                    read_fut = asyncio.ensure_future(session.reader.read(4096))
                elif transfer_done.is_set():
                    break
        except Exception:
            pass
        finally:
            if not read_fut.done():
                read_fut.cancel()
                try:
                    await read_fut
                except (asyncio.CancelledError, Exception):
                    pass

    t_out = asyncio.ensure_future(_proc_to_session())
    t_in  = asyncio.ensure_future(_session_to_proc())

    try:
        await asyncio.wait_for(proc.wait(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        await session.write("\r\n[Transfer timed out - cancelled.]\r\n")
        t_out.cancel(); t_in.cancel()
        shutil.rmtree(tmpdir, ignore_errors=True)
        return []
    finally:
        t_out.cancel(); t_in.cancel()
        try:
            proc.stdin.close()
        except Exception:
            pass
        await asyncio.gather(t_out, t_in, return_exceptions=True)
        if is_telnet:
            await _negotiate_binary(session, False)

    if proc.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return []

    received = []
    for fname in sorted(os.listdir(tmpdir)):
        fpath = os.path.join(tmpdir, fname)
        if os.path.isfile(fpath):
            received.append((fname, fpath))

    if not received:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return received  # caller cleans up: shutil.rmtree(os.path.dirname(received[0][1]))
