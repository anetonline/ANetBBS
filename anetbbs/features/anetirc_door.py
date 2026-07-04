# anetbbs/features/anetirc_door.py
"""PTY bridge: launches the ANetIRC native Linux IRC client as a BBS door."""
import asyncio
import fcntl
import os
import pty
import struct
import termios
import threading


def _find_binary():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, '..', '..'))
    path = os.path.join(root, 'doors', 'anetirc', 'anetirc')
    if not os.path.isfile(path):
        return None
    # Ensure executable — silently fixes deployments where tar preserved 0644
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    return path


def _user_data_dir(username):
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, '..', '..'))
    d = os.path.join(root, 'data', 'anetirc', 'users', username)
    os.makedirs(d, exist_ok=True)
    return d


def _write_door_sys(path, username):
    # Minimal DOOR.SYS — line 6 = alias (nick default), line 9 = real name
    lines = ['0', '9600', 'Y', '1', '0', '0',
             username, '0', '0', username,
             '60', '0', '0', '0', '0', '0', '0', '0', '0', '0']
    with open(path, 'w') as f:
        for ln in lines:
            f.write(ln + '\n')


async def launch_anetirc_telnet(user, session):
    """Launch ANetIRC and bridge its PTY to an SSH/telnet BBSSession."""
    binary = _find_binary()
    if not binary:
        await session.write(
            '\r\n\x1b[1;31mANetIRC binary not found.\x1b[0m\r\n'
            'Sysop: run  bash doors/anetirc/build.sh  to compile it.\r\n\r\n')
        await session.read_line('Press Enter to continue...')
        return

    username = (user or {}).get('username', 'BBSUser') or 'BBSUser'
    work_dir = _user_data_dir(username)
    _write_door_sys(os.path.join(work_dir, 'DOOR.SYS'), username)

    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue = asyncio.Queue()

    master_fd, slave_fd = pty.openpty()

    cols = getattr(session, 'cols', None) or 80
    rows = getattr(session, 'rows', None) or 24
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                struct.pack('HHHH', rows, cols, 0, 0))

    pid = os.fork()
    if pid == 0:
        # ---- child ----
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.chdir(work_dir)
        env = {
            'HOME': work_dir,
            'TERM': os.environ.get('TERM', 'xterm-256color'),
            'PATH': os.environ.get('PATH', '/usr/bin:/bin:/usr/local/bin'),
            'LANG': 'en_US.UTF-8',
        }
        os.execve(binary, [binary, '--bbs'], env)
        os._exit(1)

    # ---- parent ----
    os.close(slave_fd)

    def _read_pty():
        while True:
            try:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                loop.call_soon_threadsafe(out_queue.put_nowait, data)
            except OSError:
                break
        loop.call_soon_threadsafe(out_queue.put_nowait, b'')

    threading.Thread(target=_read_pty, daemon=True).start()

    async def _output_pump():
        # ANetIRC is a Linux C program compiled with UTF-8 source; always
        # decode PTY output as UTF-8 regardless of the session encoding.
        # Using session.encoding here would mis-decode UTF-8 multi-byte
        # sequences as individual CP437 bytes, producing garbled box-drawing.
        while True:
            data = await out_queue.get()
            if not data:
                break
            try:
                await session.write(data.decode('utf-8', errors='replace'))
            except Exception:
                break

    abort_event = asyncio.Event()

    async def _input_pump():
        # Read in chunks (up to 64 bytes) so that multi-byte escape sequences
        # (e.g. F2 = \x1bOQ, PgUp = \x1b[5~) arrive at the PTY in a single
        # write.  With 1-byte reads the asyncio scheduler runs between each
        # byte; if the C code's VTIME (100 ms) expires before the next byte
        # is written, the escape sequence is truncated → F2 misread as ESC.
        seen_ctrl = False
        while True:
            try:
                data = await session.read_raw(64)
            except Exception:
                break
            if not data:
                # empty return = telnet IAC consumed; keep reading
                continue
            # Fast path: no Ctrl+] byte in this chunk
            if not seen_ctrl and b'\x1d' not in data:
                try:
                    os.write(master_fd, data)
                except OSError:
                    break
                continue
            # Slow path: scan for Ctrl+] abort sequence
            for byte in data:
                b = bytes([byte])
                if seen_ctrl:
                    seen_ctrl = False
                    if b in (b'q', b'Q'):
                        abort_event.set()
                        return
                    try:
                        os.write(master_fd, b'\x1d' + b)
                    except OSError:
                        return
                    continue
                if byte == 0x1d:
                    seen_ctrl = True
                    continue
                try:
                    os.write(master_fd, b)
                except OSError:
                    return

    pump_task  = asyncio.ensure_future(_output_pump())
    input_task = asyncio.ensure_future(_input_pump())

    try:
        while True:
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
                if wpid != 0:
                    break
            except ChildProcessError:
                break
            if abort_event.is_set():
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
                break
            await asyncio.sleep(0.1)
    finally:
        pump_task.cancel()
        input_task.cancel()
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except Exception:
            pass
        out_queue.put_nowait(b'')

    # Restore clean terminal state after ANetIRC exits
    await session.write('\x1b[?25h\x1b[?1049l\x1b[0m\x1b[2J\x1b[H')
