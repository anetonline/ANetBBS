# anetbbs/games/mystic_compat.py
# -*- coding: utf-8 -*-
# (PEP 263 declaration above is required for Python 2 compatibility -- the
#  docstring below contains non-ASCII characters and Py2 parses by default
#  in ASCII mode.)
"""
Mystic BBS Python Compatibility Layer for ANetBBS

Implements the mystic_bbs module API so that Mystic BBS Python door games
(.mpy scripts) can run unmodified on ANetBBS.

When a Mystic game does `from mystic_bbs import *`, this module intercepts
and provides compatible implementations that work with ANET's PTY/WebSocket system.

Usage (internal -- called by door_runner when game_type == 'door_mystic'):
    Write this module to a temp directory and prepend that directory to
    PYTHONPATH before executing the .mpy script.
"""
import os
import re
import sys
import select


# ---------------------------------------------------------------------------
# Pipe / MCI code -> ANSI colour map
# ---------------------------------------------------------------------------

_PIPE_ANSI = {
    '00': '\033[0;30m',   # black
    '01': '\033[0;34m',   # dark blue
    '02': '\033[0;32m',   # dark green
    '03': '\033[0;36m',   # dark cyan
    '04': '\033[0;31m',   # dark red
    '05': '\033[0;35m',   # dark magenta
    '06': '\033[0;33m',   # dark yellow / brown
    '07': '\033[0;37m',   # light grey
    '08': '\033[1;30m',   # dark grey
    '09': '\033[1;34m',   # bright blue
    '10': '\033[1;32m',   # bright green
    '11': '\033[1;36m',   # bright cyan
    '12': '\033[1;31m',   # bright red
    '13': '\033[1;35m',   # bright magenta
    '14': '\033[1;33m',   # bright yellow
    '15': '\033[1;37m',   # bright white
    '16': '\033[40m',     # background black
    '17': '\033[44m',     # background dark blue
    '18': '\033[42m',     # background dark green
    '19': '\033[46m',     # background dark cyan
    '20': '\033[41m',     # background dark red
    '21': '\033[45m',     # background dark magenta
    '22': '\033[43m',     # background dark yellow
    '23': '\033[47m',     # background light grey
}

_MYSTIC_COLOR_ANSI = {
    0: '\033[0;30m', 1: '\033[0;34m', 2: '\033[0;32m', 3: '\033[0;36m',
    4: '\033[0;31m', 5: '\033[0;35m', 6: '\033[0;33m', 7: '\033[0;37m',
    8: '\033[1;30m', 9: '\033[1;34m', 10: '\033[1;32m', 11: '\033[1;36m',
    12: '\033[1;31m', 13: '\033[1;35m', 14: '\033[1;33m', 15: '\033[1;37m',
}


class MysticCompat:
    """Compatibility layer that maps mystic_bbs API calls to PTY I/O."""

    def __init__(self, pty_fd, user, game_session):
        self.pty_fd = pty_fd
        self.user = user
        self.session = game_session
        self.cursor_x = 1
        self.cursor_y = 1

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def write(self, text):
        """Send text to user (with MCI code processing)."""
        processed = self._process_mci(str(text))
        os.write(self.pty_fd, processed.encode('utf-8', errors='replace'))

    def writeln(self, text=''):
        """Send text + CRLF to user."""
        self.write(str(text) + '\r\n')

    def pwrite(self, text):
        """Process pipe colour codes then send."""
        processed = self._process_pipe_codes(str(text))
        os.write(self.pty_fd, processed.encode('utf-8', errors='replace'))

    def pwriteln(self, text=''):
        self.pwrite(str(text) + '\r\n')

    def rwrite(self, text):
        """Raw write -- no processing."""
        os.write(self.pty_fd, str(text).encode('utf-8', errors='replace'))

    def rwriteln(self, text=''):
        self.rwrite(str(text) + '\r\n')

    def showfile(self, filename):
        """Display the contents of a file to the user."""
        try:
            with open(filename, 'r', errors='replace') as fh:
                self.rwrite(fh.read())
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def getstr(self, maxlen=80, options=None, timeout=None, default=None):
        """Read a string from the user (reads until Enter)."""
        buf = ''
        while True:
            ready, _, _ = select.select([self.pty_fd], [], [],
                                        timeout if timeout else 300)
            if not ready:
                return default or buf
            ch = os.read(self.pty_fd, 1).decode('utf-8', errors='replace')
            if ch in ('\r', '\n'):
                self.rwrite('\r\n')
                return buf
            if ch == '\x08' or ch == '\x7f':  # backspace
                if buf:
                    buf = buf[:-1]
                    self.rwrite('\x08 \x08')
            elif len(buf) < maxlen and ch.isprintable():
                buf += ch
                self.rwrite(ch)

    def onekey(self, valid_keys, timeout=False):
        """Wait for a keypress from the allowed set.

        Multi-digit hack: if the first key is a digit, wait briefly (~600ms)
        for a second digit so menus with >9 numeric options (RDQ3, etc.) work.
        Pressing Enter ends digit collection early. Single-key scripts pay only
        the brief delay on digits."""
        valid = valid_keys.upper() if isinstance(valid_keys, str) else valid_keys
        while True:
            ready, _, _ = select.select([self.pty_fd], [], [],
                                        30 if timeout else 300)
            if not ready:
                return ''
            ch = os.read(self.pty_fd, 1).decode('utf-8', errors='replace').upper()
            if ch not in valid:
                continue
            if not ch.isdigit():
                return ch
            buf = ch
            while len(buf) < 3:
                more, _, _ = select.select([self.pty_fd], [], [], 0.6)
                if not more:
                    break
                nx = os.read(self.pty_fd, 1).decode('utf-8', errors='replace')
                if nx in ('\r', '\n'):
                    break
                if not nx.isdigit():
                    break
                buf += nx
            return buf

    def pause(self):
        """Show pause prompt and wait for any keypress."""
        self.writeln('\r\n|07Press any key to continue...|00')
        select.select([self.pty_fd], [], [], 300)
        os.read(self.pty_fd, 1)

    # ------------------------------------------------------------------
    # Cursor / screen
    # ------------------------------------------------------------------

    def gotoxy(self, x, y):
        self.rwrite('\033[{};{}H'.format(y, x))
        self.cursor_x = x
        self.cursor_y = y

    def wherex(self):
        return self.cursor_x

    def wherey(self):
        return self.cursor_y

    def clrscr(self):
        self.rwrite('\033[2J\033[H')
        self.cursor_x = 1
        self.cursor_y = 1

    # ------------------------------------------------------------------
    # Colour
    # ------------------------------------------------------------------

    def textcolor(self, color):
        self.rwrite(_MYSTIC_COLOR_ANSI.get(int(color), '\033[0m'))

    def textbackground(self, color):
        bg_map = {
            0: '\033[40m', 1: '\033[44m', 2: '\033[42m', 3: '\033[46m',
            4: '\033[41m', 5: '\033[45m', 6: '\033[43m', 7: '\033[47m',
        }
        self.rwrite(bg_map.get(int(color) % 8, '\033[40m'))

    # ------------------------------------------------------------------
    # User data
    # ------------------------------------------------------------------

    def getuser(self):
        return {
            'handle': self.user.username,
            'realname': getattr(self.user, 'display_name', None) or self.user.username,
            'email': self.user.email or '',
            'security': 255 if self.user.is_admin else 50,
            'timeleft': 120,
            'calls': self.user.login_count or 0,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _process_mci(text):
        """Convert basic Mystic MCI codes to ANSI. (staticmethod so it works
        when called as MysticCompat._process_mci(text) under both Py2 and Py3.)"""
        text = text.replace('|CL', '\033[2J\033[H')
        text = text.replace('|CR', '\r\n')
        text = MysticCompat._process_pipe_codes(text)
        return text

    @staticmethod
    def _process_pipe_codes(text):
        """Convert pipe colour codes |00-|23 to ANSI escape sequences."""
        def _replace(m):
            code = m.group(1)
            return _PIPE_ANSI.get(code, '')
        return re.sub(r'\|(\d{2})', _replace, text)

    def _mystic_color_to_ansi(self, code):
        return _MYSTIC_COLOR_ANSI.get(int(code), '\033[0m')


# ---------------------------------------------------------------------------
# Module-level API (for `from mystic_bbs import *`)
# These are injected by the runner via exec() in the correct scope.
# ---------------------------------------------------------------------------

_compat = None  # Populated by _init_compat() if used in-process


def _init_compat(compat_instance):
    """Called by the in-process launcher (web SocketIO path) to inject the
    PTY-aware MysticCompat instance. The subprocess path (telnet/SSH/rlogin)
    leaves _compat = None and the helpers below fall back to sys.stdin/stdout."""
    global _compat
    _compat = compat_instance


# Standalone stdin/stdout helpers used when _compat is None (subprocess mode).
# These let a Mystic-style Python script run via a forked python process and
# still talk to the user via the parent's PTY (the child's stdin/stdout ARE
# the PTY in that setup).

import sys as _sys


def _stdout_write(text):
    _sys.stdout.write(text if isinstance(text, str) else str(text))
    _sys.stdout.flush()


def _stdin_read_line(maxlen=80):
    """Read a line from stdin. Relies on the terminal's line-buffering AND
    local echo (the default ICANON+ECHO mode), so the user can type multi-char
    input like "20" + Enter and the script sees "20" returned in one read.
    We do NOT manually echo here — that would double-print under line mode."""
    try:
        line = _sys.stdin.readline()
    except Exception:
        return ''
    return (line or '').rstrip('\r\n')[:maxlen]


def _stdin_read_key():
    """Read a single keypress. Temporarily switches the terminal to raw/cbreak
    mode so the read returns immediately on the first byte instead of waiting
    for Enter (which is the canonical/line-mode behavior). Restores afterwards."""
    try:
        import termios as _termios, tty as _tty
        if not _sys.stdin.isatty():
            return _sys.stdin.read(1) or ''
        fd = _sys.stdin.fileno()
        old = _termios.tcgetattr(fd)
        try:
            _tty.setcbreak(fd)
            ch = _sys.stdin.read(1)
        finally:
            _termios.tcsetattr(fd, _termios.TCSANOW, old)
        return ch or ''
    except Exception:
        return _sys.stdin.read(1) or ''


# ---------------------------------------------------------------------------
# Module-level helpers (mirror the in-process MysticCompat methods, fall back
# to direct stdio when running in subprocess mode).
# ---------------------------------------------------------------------------

def write(text):
    if _compat is not None:
        _compat.write(text)
    else:
        _stdout_write(MysticCompat._process_mci(str(text)))


def writeln(text=''):
    write(str(text) + '\r\n')


def pwrite(text):
    if _compat is not None:
        _compat.pwrite(text)
    else:
        _stdout_write(MysticCompat._process_pipe_codes(str(text)))


def pwriteln(text=''):
    pwrite(str(text) + '\r\n')


def rwrite(text):
    if _compat is not None:
        _compat.rwrite(text)
    else:
        _stdout_write(str(text))


def rwriteln(text=''):
    rwrite(str(text) + '\r\n')


def showfile(filename):
    """Display a file's contents (used for .ANS / .MSG screens). Tries the
    cwd, then the script's dir, then a couple of common subdirs (ansi/, ans/,
    text/, msg/) so scripts that use bare names like 'welcome.ans' work even
    when the file is organized into subfolders."""
    if _compat is not None:
        _compat.showfile(filename)
        return
    import os as _os
    candidates = [
        filename,
        _os.path.join('ansi', filename),
        _os.path.join('ans', filename),
        _os.path.join('text', filename),
        _os.path.join('msg', filename),
        _os.path.join('display', filename),
    ]
    # Also try with .ans appended if not already
    if not filename.lower().endswith('.ans'):
        candidates.extend([c + '.ans' for c in candidates])

    for path in candidates:
        if _os.path.isfile(path):
            try:
                # Open binary so ANSI escape codes pass through unchanged.
                with open(path, 'rb') as fh:
                    data = fh.read()
                _sys.stdout.buffer.write(data) if hasattr(_sys.stdout, 'buffer') else _stdout_write(data)
                _sys.stdout.flush()
                return
            except (OSError, IOError):
                continue
    # File not found anywhere — write a visible note so users know what's missing
    _stdout_write('\r\n[showfile: %s not found in cwd or common subdirs]\r\n' % filename)


def getstr(fieldlen=80, maxlen=None, mode=None, default=None, **kwargs):
    """Mystic API: bbs.getstr(fieldlen, maxlen, mode, default)
    where fieldlen is display width and maxlen is the max chars stored.
    Old positional callers passing (maxlen) only — we treat that as both.

    Previously this function had maxlen as arg1, which silently truncated
    multi-digit input from scripts like RDQ3 that call
    bbs.getstr(1, width, width, '') — user types '20' but we returned only '2'."""
    # If timeout was passed via kwargs (older callers used keyword args), ignore.
    if maxlen is None:
        maxlen = fieldlen
    # Some callers may legitimately want a 1-char field shown but allow more
    # chars stored — Mystic spec — so trust maxlen.
    if _compat is not None:
        return _compat.getstr(maxlen, mode, kwargs.get('timeout'), default)
    return _stdin_read_line(maxlen) or (default or '')


def onekey(valid_keys, timeout=False):
    """Wait for a keypress from the allowed set, with a multi-digit hack:
    after the first digit, briefly poll for additional digits so menus with
    more than 9 numeric items (RDQ3 etc.) get the full number. Enter ends
    digit collection early; non-digit input ends it without consuming the key.

    Critical: the entire wait — first key AND the multi-digit window — runs
    inside a SINGLE cbreak block. If we restored canonical mode between calls,
    the kernel's line discipline would buffer the second digit until Enter,
    breaking the hack. Holding cbreak open lets each keystroke flow through
    immediately."""
    if _compat is not None:
        return _compat.onekey(valid_keys, timeout)
    import select as _select
    try:
        import termios as _termios, tty as _tty
    except ImportError:
        # No POSIX terminal control — fall back to plain read (best-effort).
        while True:
            ch = _sys.stdin.read(1) or ''
            if not ch:
                return ''
            if not valid_keys or ch.upper() in valid_keys.upper():
                return ch

    if not _sys.stdin.isatty():
        # Pipe/file — line-buffered; just take the first char of next line.
        line = _sys.stdin.readline() or ''
        for ch in line:
            if not valid_keys or ch.upper() in valid_keys.upper():
                return ch
        return ''

    fd = _sys.stdin.fileno()
    old = _termios.tcgetattr(fd)
    primary_timeout = 30 if timeout else 300
    try:
        _tty.setcbreak(fd)
        while True:
            ready, _, _ = _select.select([_sys.stdin], [], [], primary_timeout)
            if not ready:
                return ''
            ch = _sys.stdin.read(1) or ''
            if not ch:
                return ''
            if valid_keys and ch.upper() not in valid_keys.upper():
                continue
            if not ch.isdigit():
                return ch
            # Multi-digit accumulator — stays in cbreak the whole time.
            buf = ch
            while len(buf) < 3:
                ready2, _, _ = _select.select([_sys.stdin], [], [], 0.6)
                if not ready2:
                    break
                nx = _sys.stdin.read(1) or ''
                if not nx:
                    break
                if nx in ('\r', '\n'):
                    break
                if not nx.isdigit():
                    break
                buf += nx
            return buf
    finally:
        _termios.tcsetattr(fd, _termios.TCSANOW, old)


def pause():
    rwrite('\r\n[Press any key to continue]')
    if _compat is not None:
        _compat.pause()
    else:
        _stdin_read_key()
        rwrite('\r\n')


def gotoxy(x, y):
    if _compat is not None:
        _compat.gotoxy(x, y)
    else:
        _stdout_write('\x1b[{};{}H'.format(int(y), int(x)))


def wherex():
    return _compat.wherex() if _compat is not None else 1


def wherey():
    return _compat.wherey() if _compat is not None else 1


def clrscr():
    if _compat is not None:
        _compat.clrscr()
    else:
        _stdout_write('\x1b[2J\x1b[H')


def textcolor(color):
    if _compat is not None:
        _compat.textcolor(color)
    else:
        # ANSI fg color from Mystic 0-15 palette
        ansi_map = [30, 34, 32, 36, 31, 35, 33, 37, 90, 94, 92, 96, 91, 95, 93, 97]
        c = int(color) & 0x0F
        _stdout_write('\x1b[{}m'.format(ansi_map[c]))


def textbackground(color):
    if _compat is not None:
        _compat.textbackground(color)
    else:
        ansi_map = [40, 44, 42, 46, 41, 45, 43, 47]
        c = int(color) & 0x07
        _stdout_write('\x1b[{}m'.format(ansi_map[c]))


def getuser(*args, **kwargs):
    """getuser([record_no]) — Mystic's API takes an optional user record number.
    We only have one user (the BBS session user), so we ignore the argument."""
    if _compat is not None:
        return _compat.getuser()
    # Subprocess mode -- environment-based fallback (set by the launcher)
    import os as _os
    return {
        'username': _os.environ.get('BBS_USERNAME', ''),
        'handle': _os.environ.get('BBS_USERNAME', ''),
        'real_name': _os.environ.get('BBS_REAL_NAME', ''),
        'realname': _os.environ.get('BBS_REAL_NAME', ''),
        'email': _os.environ.get('BBS_EMAIL', ''),
        'security': int(_os.environ.get('BBS_SECURITY', '50') or 50),
        'time_left': int(_os.environ.get('BBS_TIME_LEFT', '60') or 60),
        'timeleft': int(_os.environ.get('BBS_TIME_LEFT', '60') or 60),
        'calls': int(_os.environ.get('BBS_LOGIN_COUNT', '0') or 0),
    }


# ---------------------------------------------------------------------------
# Additional Mystic BBS API functions (used by dopewars.mps source and RDQ2)
# ---------------------------------------------------------------------------

_DEFAULT_READ_TIMEOUT = 300  # seconds to wait for user input before giving up


def Input(prompt='', maxlen=80, timeout=None):
    """Display prompt and read a string from the user."""
    if prompt:
        rwrite(prompt)
    return getstr(maxlen=maxlen, timeout=timeout)


def ReadKey(timeout=None):
    """Read a single key from the user."""
    if _compat is not None:
        ready, _, _ = __import__('select').select(
            [_compat.pty_fd], [], [],
            timeout if timeout is not None else _DEFAULT_READ_TIMEOUT)
        if not ready:
            return ''
        ch = __import__('os').read(_compat.pty_fd, 1)
        return ch.decode('utf-8', errors='replace')
    # Subprocess mode -- read from stdin
    return _stdin_read_key()


def WriteXY(x, y, attr, text):
    """Write text at absolute screen position (x, y) with Mystic colour attr."""
    _compat.gotoxy(x, y)
    _compat.textcolor(attr & 0x0F)
    _compat.textbackground((attr >> 4) & 0x07)
    _compat.rwrite(str(text))


def PadRt(s, width, pad_char=' '):
    """Right-pad string s to width with pad_char."""
    return str(s).ljust(int(width), str(pad_char))


def PadLt(s, width, pad_char=' '):
    """Left-pad string s to width with pad_char."""
    return str(s).rjust(int(width), str(pad_char))


def PadCt(s, width, pad_char=' '):
    """Centre-pad string s to width with pad_char."""
    return str(s).center(int(width), str(pad_char))


def Int2Str(n):
    """Convert integer to string (Mystic Pascal helper)."""
    return str(int(n))


def StrComma(n):
    """Format a number with comma thousands separators."""
    try:
        return '{:,}'.format(int(n))
    except (ValueError, TypeError):
        return str(n)
