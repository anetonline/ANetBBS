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

    transfer_done = asyncio.Event()

    async def _proc_to_session():
        """Forward sz stdout → raw writer (binary, no encoding)."""
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                session.writer.write(chunk)
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
                    try:
                        proc.stdin.write(chunk)
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

    transfer_done = asyncio.Event()

    async def _proc_to_session():
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                session.writer.write(chunk)
                await session.writer.drain()
        except Exception as exc:
            logger.debug("xfer recv proc→session: %s", exc)
        finally:
            transfer_done.set()

    async def _session_to_proc():
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
                    try:
                        proc.stdin.write(chunk)
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
