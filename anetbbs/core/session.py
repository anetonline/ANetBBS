# anetbbs/core/session.py
import asyncio
import logging
import os
import re
from datetime import datetime
from .tz import fmt_eastern
from typing import Dict, Optional, Any

_ANSI_ESC_RE   = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
_ANSI_ESC_RE_B = re.compile(rb'\x1b\[[0-9;]*[A-Za-z]')

logger = logging.getLogger(__name__)
from .protocols import SessionProtocol
# Plain `pyflakes` (unlike flake8) has no `# noqa` suppression, so this
# needs a real reference to count as "used": telnet/SSH crashes without
# this import (see CHANGELOG v152) even though nothing here calls it
# directly -- it's load-bearing for a side effect at import time.
assert SessionProtocol is not None
from ..features.chat import ChatManager
from ..features.games import GameManager
from .user_manager import UserManager
from .service_locator import ServiceLocator

class CarrierLost(ConnectionError):
    """The transport has gone away mid-read. Raised by read_raw/read_key/
    read_line/read_password so menu loops can unwind instead of spinning
    on a stream that will only ever return EOF from here on."""


def _find_screen_variants(directory, base_name):
    """Find every variant of a display screen next to *base_name*.

    The plain file (e.g. 'welcome132.ans') is variant #1 if present. Any
    sibling named '{stem}_2.{ext}', '{stem}_3.{ext}', ... is an additional
    variant — no gap/contiguity requirement, whatever numbers exist are
    used in ascending order. All of them get shown together, in this
    order, every login (see _resolve_display_screens) — the classic
    Synchronet logon1.ans/logon2.ans/... multi-screen convention, not a
    rotate-a-different-one-per-login scheme.
    Returns a list of Paths (possibly empty), never raises.
    """
    stem, dot, ext = base_name.rpartition('.')
    if not dot:
        return []
    variants = []
    exact = directory / base_name
    if exact.is_file():
        variants.append(exact)
    numbered = []
    pattern = re.compile(rf'^{re.escape(stem)}_(\d+)\.{re.escape(ext)}$')
    try:
        for entry in directory.iterdir():
            m = pattern.match(entry.name)
            if m and entry.is_file():
                numbered.append((int(m.group(1)), entry))
    except OSError:
        pass
    numbered.sort(key=lambda t: t[0])
    variants.extend(p for _, p in numbered)
    return variants


def _find_random_screen_variants(directory, base_name):
    """Find the '_ran'-tagged sibling group for *base_name*, if any.

    Naming: 'welcome_ran.ans' is random variant #1, 'welcome_2_ran.ans',
    'welcome_3_ran.ans', ... are additional variants — the optional
    number goes *before* the '_ran' tag. A sysop who wants ONE screen
    picked at random each login (instead of the whole '{stem}_N.{ext}'
    sequence being shown together) uses this naming instead; the two
    schemes are independent; see _resolve_display_screens for how
    they're tried. Returns a list of Paths (possibly empty), never
    raises.
    """
    stem, dot, ext = base_name.rpartition('.')
    if not dot:
        return []
    variants = []
    exact = directory / f'{stem}_ran.{ext}'
    if exact.is_file():
        variants.append(exact)
    numbered = []
    pattern = re.compile(rf'^{re.escape(stem)}_(\d+)_ran\.{re.escape(ext)}$')
    try:
        for entry in directory.iterdir():
            m = pattern.match(entry.name)
            if m and entry.is_file():
                numbered.append((int(m.group(1)), entry))
    except OSError:
        pass
    numbered.sort(key=lambda t: t[0])
    variants.extend(p for _, p in numbered)
    return variants


def _resolve_display_screens(directory, base_name):
    """Resolve base_name to the list of screen Paths to show *this* login.

    Checks the '_ran'-tagged random naming first (welcome_ran.ans,
    welcome_2_ran.ans, ...) — if that group exists, one is picked at
    random and returned as a single-item list (a sysop who wants variety
    without a multi-screen slideshow). Otherwise falls back to the plain
    numbered naming (welcome.ans, welcome_2.ans, welcome_3.ans, ...),
    which returns ALL matching files in order — every login shows all of
    them, one after another, the same way Synchronet's
    logon1.ans/logon2.ans/... convention works. Each variant file is
    responsible for its own '@PAUSE@' if a pause is wanted before the
    next one loads — nothing is auto-inserted between them. A single
    matching file (either scheme) returns as a one-item list with no
    extra work — the common case (most sysops have exactly one
    welcome.ans) is unaffected. Returns None if nothing matches either
    naming scheme.
    """
    random_variants = _find_random_screen_variants(directory, base_name)
    if random_variants:
        if len(random_variants) == 1:
            return [random_variants[0]]
        import random
        return [random.choice(random_variants)]

    variants = _find_screen_variants(directory, base_name)
    if not variants:
        return None
    return variants


def _load_display_screens(paths):
    """Read and concatenate every screen in *paths*, in order. Each file
    is decoded independently (latin-1, matching how screen bodies are
    decoded elsewhere) and simply joined — see _resolve_display_screens
    for why there's no automatic pause insertion between them."""
    return ''.join(p.read_bytes().decode('latin-1') for p in paths)


def _stock_screen(slot, mode):
    """Load a bundled stock screen from anetbbs/screens/.

    Priority by mode:
      wide : {slot}132.ans → {slot}.ans → {slot}.asc
      ansi : {slot}.ans    → {slot}.asc
      ascii: {slot}.asc    → {slot}.ans
    Each candidate supports multiple display variants (see
    _resolve_display_screens) — either a full ordered sequence shown
    together, or a single random pick, depending on naming.
    Returns decoded string or None.
    """
    import pathlib as _pl
    _screens_dir = _pl.Path(__file__).parent.parent / 'screens'
    if mode == 'wide':
        candidates = [f'{slot}132.ans', f'{slot}.ans', f'{slot}.asc']
    elif mode == 'ascii':
        candidates = [f'{slot}.asc', f'{slot}.ans']
    else:
        candidates = [f'{slot}.ans', f'{slot}.asc']
    for fname in candidates:
        picked = _resolve_display_screens(_screens_dir, fname)
        if picked is not None:
            return _load_display_screens(picked), fname.endswith('.asc')
    return None, False


# Telnet commands
IAC = bytes([255])  # "Interpret As Command"
DONT = bytes([254])
DO = bytes([253])
WONT = bytes([252])
WILL = bytes([251])
SB = bytes([250])  # Subnegotiation Begin
SE = bytes([240])  # Subnegotiation End
BINARY = bytes([0])  # Binary Transmission
ECHO = bytes([1])  # Echo
SGA = bytes([3])  # Suppress Go Ahead
TTYPE = bytes([24])  # Terminal Type
NAWS = bytes([31])  # Window Size
LINEMODE = bytes([34])  # Linemode

# Telnet options
BINARY = bytes([0])  # Binary Transmission
ECHO = bytes([1])  # Echo
RCP = bytes([2])  # Reconnection
SGA = bytes([3])  # Suppress Go Ahead
NAMS = bytes([4])  # Negotiate About Message Size
STATUS = bytes([5])  # Status
TM = bytes([6])  # Timing Mark
RCTE = bytes([7])  # Remote Controlled Trans and Echo
NAOL = bytes([8])  # Negotiate About Output Line Width
NAOP = bytes([9])  # Negotiate About Output Page Size
NAOCRD = bytes([10])  # Negotiate About CR Disposition
NAOHTS = bytes([11])  # Negotiate About Horizontal Tab Stops
NAOHTD = bytes([12])  # Negotiate About Horizontal Tab Disposition
NAOFFD = bytes([13])  # Negotiate About Form Feed Disposition
NAOVTS = bytes([14])  # Negotiate About Vertical Tab Stops
NAOVTD = bytes([15])  # Negotiate About Vertical Tab Disposition
NAOLFD = bytes([16])  # Negotiate About Output LF Disposition
XASCII = bytes([17])  # Extended ASCII
LOGOUT = bytes([18])  # Logout
BM = bytes([19])  # Byte Macro
DET = bytes([20])  # Data Entry Terminal
SUPDUP = bytes([21])  # SUPDUP
SUPDUPOUTPUT = bytes([22])  # SUPDUP Output
SNDLOC = bytes([23])  # Send Location
TTYPE = bytes([24])  # Terminal Type
EOR = bytes([25])  # End of Record
TUID = bytes([26])  # TACACS User Identification
OUTMRK = bytes([27])  # Output Marking
TTYLOC = bytes([28])  # Terminal Location Number
VT3270REGIME = bytes([29])  # 3270 Regime
X3PAD = bytes([30])  # X.3 PAD
NAWS = bytes([31])  # Negotiate About Window Size
TSPEED = bytes([32])  # Terminal Speed
LFLOW = bytes([33])  # Remote Flow Control
LINEMODE = bytes([34])  # Linemode
XDISPLOC = bytes([35])  # X Display Location
OLD_ENVIRON = bytes([36])  # Environment Option
AUTHENTICATION = bytes([37])  # Authentication Option
ENCRYPT = bytes([38])  # Encryption Option
NEW_ENVIRON = bytes([39])  # New Environment Option

class BBSSession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 config: Dict[str, Any], prefill_username: Optional[str] = None,
                 prefill_password: Optional[str] = None,
                 forced_term_mode: Optional[str] = None,
                 forced_width: Optional[int] = None):
        """
        prefill_username: when set (typically from SSH or rlogin), the login flow
            skips the welcome menu + username prompt.
        prefill_password: when also set (SSH client sent a password) the login
            flow attempts authentication silently and only falls back to the
            interactive prompt if it fails.
        forced_term_mode: set by anetbbs/core/petscii_server.py's dedicated
            PETSCII listener(s) -- every connection on those ports IS
            PETSCII unconditionally (real C64 telnet clients mostly don't
            announce themselves usefully via TTYPE negotiation, so this
            skips that guesswork entirely rather than trying to detect it).
            Overrides the normal window-size/TTYPE-based term_mode property.
        forced_width: paired with forced_term_mode -- 40 or 80, matching
            which PETSCII port (40-col vs 80-col) the connection came in on.
        """
        self.reader = reader
        self.writer = writer
        self.config = config
        self.user_manager = UserManager()
        self.user = None
        self._forced_term_mode = forced_term_mode
        self._forced_width = forced_width
        # CP437 — the encoding ANSI BBSes have always spoken. SyncTERM,
        # NetRunner, mTelnet, and modern terminals all support it (most
        # auto-detect via the IBM-PC font). v172 briefly flipped this to
        # UTF-8 to help mainstream terminal users, but it broke the
        # hardcoded menus on CP437 clients — reverted in v173.
        self.encoding = 'cp437'
        self.terminal_type = None
        self.window_size = (80, 24)
        self.chat = ChatManager(self)
        self.games = GameManager(self)
        self._chat_manager = None
        self._games_manager = None
        self._prefill_username = (prefill_username or '').strip() or None
        self._prefill_password = prefill_password or None
        self.buffer = bytearray()
        self.telnet_command_buffer = bytearray()
        self._echo_on = True
        self._line_mode = False
        self._binary_mode = False
        self._buffer = bytearray()
        self.user: Optional[Dict[str, Any]] = None

    def _client_ip(self) -> Optional[str]:
        """Best-effort peer IP for this connection -- used to rate-limit/
        ban-check login attempts (see UserManager.authenticate()'s `ip`
        param). Mirrors the same get_extra_info('peername') pattern
        already used for CallerLog/presence tracking further down this
        file, just callable before a login attempt instead of only
        after one succeeds. Returns None (not '') on failure so
        authenticate()'s `if ip:` check correctly treats "couldn't
        determine it" as "skip the check" rather than trying to look up
        an empty-string IP."""
        try:
            addr = self.writer.get_extra_info('peername')
        except Exception:
            return None
        if not addr:
            return None
        return addr[0] if isinstance(addr, tuple) else str(addr)

    @property
    def chat_manager(self):
        if self._chat_manager is None:
            from ..features.chat import ChatManager
            self._chat_manager = ChatManager(self)
        return self._chat_manager

    @property
    def echo(self):
        return self._echo_on

    @echo.setter
    def echo(self, value):
        self._echo_on = value

    @property
    def term_mode(self):
        """Detected terminal capability: 'petscii', 'wide', 'ansi', or 'ascii'.

        petscii — a Commodore PETSCII client, connected via one of the
                  dedicated PETSCII-only ports (see forced_term_mode in
                  __init__) -- never negotiation-detected, since real C64
                  telnet clients mostly don't announce themselves usefully.
        wide    — 132+ column ANSI terminal (SyncTERM 132x37, etc.)
        ansi    — standard 80-col ANSI/CP437 (default when unknown)
        ascii   — plain-text terminal (dumb, TTY, etc.)

        Defaults to 'ansi' when TTYPE is absent so existing behaviour is
        preserved for terminals that don't answer the negotiation.
        """
        if self._forced_term_mode:
            return self._forced_term_mode
        if self.window_size[0] >= 132:
            return 'wide'
        ttype = (self.terminal_type or '').upper()
        _ASCII_TERMS = {'DUMB', 'TTY', 'UNKNOWN', 'PLAIN', 'ASCII', 'GLASS-TTY'}
        if ttype in _ASCII_TERMS:
            return 'ascii'
        return 'ansi'

    @property
    def petscii_width(self):
        """40 or 80 -- which PETSCII port this session connected on.
        Only meaningful when term_mode == 'petscii'."""
        return self._forced_width or 40

    def get_box_chars(self):
        """Get the box drawing characters in CP437 encoding"""
        return {
            'tl': b'\xda',  # Top left
            'tr': b'\xbf',  # Top right
            'bl': b'\xc0',  # Bottom left
            'br': b'\xd9',  # Bottom right
            'h': b'\xc4',   # Horizontal
            'v': b'\xb3',   # Vertical
            'lc': b'\xc3',  # Left cross
            'rc': b'\xb4',  # Right cross
        }

    def create_box(self, width, title=""):
        """Create a box with the given width and optional title"""
        chars = self.get_box_chars()
        box = bytearray()
        
        box.extend(chars['tl'])
        box.extend(chars['h'] * (width - 2))
        box.extend(chars['tr'])
        box.extend(b'\r\n')
        
        if title:
            box.extend(chars['v'])
            title_bytes = title.encode(self.encoding)
            padding = width - 2 - len(title_bytes)
            left_pad = padding // 2
            right_pad = padding - left_pad
            box.extend(b' ' * left_pad)
            box.extend(title_bytes)
            box.extend(b' ' * right_pad)
            box.extend(chars['v'])
            box.extend(b'\r\n')
            
            box.extend(chars['lc'])
            box.extend(chars['h'] * (width - 2))
            box.extend(chars['rc'])
            box.extend(b'\r\n')
        
        return bytes(box)

    async def read_key(self, prompt: str = '') -> str:
        """Read a single keystroke and return it (uppercase ASCII).

        Returns '' if the user pressed Enter without a hotkey (so menu
        loops can no-op + redraw). Discards arrow keys / function keys
        and other multi-byte ESC sequences. The keystroke is echoed
        followed by CRLF — same UX as line input but without the wait
        for Enter.

        Raises CarrierLost if the transport has gone away — callers
        must not confuse this with bare-Enter (both used to look like '')."""
        if prompt:
            await self.write(prompt)
        while True:
            try:
                ch = await self.reader.read(1)
            except (ConnectionError, BrokenPipeError, EOFError) as e:
                raise CarrierLost(str(e)) from e
            if not ch:
                raise CarrierLost('client disconnected')
            # Bare Enter: no-op (lets menus redraw without selecting).
            if ch in (b'\r', b'\n'):
                await self.write('\r\n')
                return ''
            # Backspace / Ctrl-C: also no-op.
            if ch in (b'\x08', b'\x7f', b'\x03'):
                continue
            # ESC sequence (arrows etc.) — drain a few bytes and ignore.
            if ch == b'\x1b':
                try:
                    await asyncio.wait_for(self.reader.read(2), timeout=0.05)
                    # If it was a longer sequence, leave the rest in the
                    # buffer — most function keys are <=4 bytes total.
                except (asyncio.TimeoutError, Exception):
                    pass
                continue
            # Other control chars dropped.
            if ch[0] < 32:
                continue
            try:
                key = ch.decode('utf-8', errors='replace').upper()
            except Exception:
                key = '?'
            # Echo the key + CRLF so the user sees what they picked.
            await self.write(key + '\r\n')
            return key

    async def read_key_arrow(self) -> str:
        """Read a single keystroke including arrow/navigation keys. No echo.

        Returns one of: 'UP' 'DOWN' 'LEFT' 'RIGHT' 'PGUP' 'PGDN' 'HOME'
        'END' 'ENTER' 'ESC' 'CTRL_C' or an uppercase printable character.
        Raises CarrierLost if the connection drops.

        CSI sequences (PgUp = ESC [ 5 ~, PgDn = ESC [ 6 ~, etc.) are read
        byte-by-byte until the VT final byte (0x40–0x7E) so they are never
        fragmented by a partial stream.read() return."""
        while True:
            try:
                ch = await self.reader.read(1)
            except (ConnectionError, BrokenPipeError, EOFError) as e:
                raise CarrierLost(str(e)) from e
            if not ch:
                raise CarrierLost('client disconnected')
            if ch == b'\r':
                return 'ENTER'
            if ch == b'\n':
                continue  # discard trailing LF from CR+LF Telnet pairs
            if ch == b'\x03':
                return 'CTRL_C'
            if ch == b'\x1b':
                try:
                    nxt = await asyncio.wait_for(self.reader.read(1), timeout=0.12)
                except (asyncio.TimeoutError, Exception):
                    return 'ESC'
                if nxt == b'[':
                    # CSI: read one byte at a time until the VT final byte
                    # (any byte in 0x40–0x7E, which includes A-Z a-z @-~ and ~).
                    seq = b''
                    try:
                        while len(seq) < 16:
                            b = await asyncio.wait_for(
                                self.reader.read(1), timeout=0.12)
                            if not b:
                                break
                            seq += b
                            if 0x40 <= b[0] <= 0x7E:   # final byte
                                break
                    except (asyncio.TimeoutError, Exception):
                        pass
                    if seq == b'A': return 'UP'
                    if seq == b'B': return 'DOWN'
                    if seq == b'C': return 'RIGHT'
                    if seq == b'D': return 'LEFT'
                    if seq == b'H': return 'HOME'
                    if seq == b'F': return 'END'
                    if seq == b'5~': return 'PGUP'
                    if seq == b'6~': return 'PGDN'
                    if seq == b'V':  return 'PGUP'   # SyncTERM ANSI mode
                    if seq == b'U':  return 'PGDN'   # SyncTERM ANSI mode
                    if seq == b'1~': return 'HOME'
                    if seq == b'4~': return 'END'
                    if seq == b'7~': return 'HOME'
                    if seq == b'8~': return 'END'
                    if seq == b'3~': return 'DEL'
                    if seq == b'2~': return 'INS'
                    # Modifier variants like 1;5A (Ctrl+Arrow) → treat as arrow
                    if seq.endswith(b'A'): return 'UP'
                    if seq.endswith(b'B'): return 'DOWN'
                    if seq.endswith(b'C'): return 'RIGHT'
                    if seq.endswith(b'D'): return 'LEFT'
                    return 'ESC'
                if nxt == b'O':
                    # SS3 sequences (common in SyncTERM's xterm mode)
                    try:
                        fin = await asyncio.wait_for(
                            self.reader.read(1), timeout=0.12)
                    except (asyncio.TimeoutError, Exception):
                        fin = b''
                    if fin == b'A': return 'UP'
                    if fin == b'B': return 'DOWN'
                    if fin == b'C': return 'RIGHT'
                    if fin == b'D': return 'LEFT'
                    if fin == b'H': return 'HOME'
                    if fin == b'F': return 'END'
                return 'ESC'
            if ch[0] < 32:
                continue
            try:
                return ch.decode('latin-1', errors='replace').upper()
            except Exception:
                return '?'

    async def read_password(self, prompt: str = 'Password: ') -> str:
        """Read a password char-by-char, echoing '*' for each one. Standard
        terminals echo locally; we send a backspace+'*' to overwrite each char
        as the user types. Backspace removes the last star."""
        # Real gap found in a full auth-security audit: no cap existed on
        # how much a client could type here before pressing Enter -- a
        # client that never sends \r could hold this loop open
        # indefinitely, growing the in-memory buffer without bound (a
        # per-connection memory DoS). No real password needs anywhere
        # close to this; extra input beyond the cap is silently dropped
        # (still consumed from the stream, still echoed as '*', so the
        # loop keeps making forward progress) rather than raising, so a
        # legitimately-long paste just gets truncated instead of the
        # whole login attempt failing outright.
        _MAX_PASSWORD_CHARS = 256
        if prompt:
            await self.write(prompt)
        chars = []
        while True:
            try:
                ch = await self.reader.read(1)
            except (ConnectionError, BrokenPipeError, EOFError) as e:
                raise CarrierLost(str(e)) from e
            if not ch:
                raise CarrierLost('client disconnected')
            # Most telnet/SSH clients send \r when Enter is pressed; some send \r\n
            if ch in (b'\r', b'\n'):
                # consume any paired \n so it doesn't dirty the next read
                # (cheap-and-cheerful — not strictly needed for asyncio reader)
                await self.write('\r\n')
                break
            # A real C64 keyboard's DEL/INST key sends PETSCII 0x14, not
            # ASCII 0x7f/0x08 -- and the echo sequence needs its own
            # PETSCII cursor-left/space/cursor-left too, since ASCII
            # backspace (0x08) means something else entirely on real
            # hardware ("disable Shift-Commodore"), not erase-in-place.
            is_backspace = (ch == b'\x14') if self.term_mode == 'petscii' \
                          else (ch in (b'\x08', b'\x7f'))
            if is_backspace:
                if chars:
                    chars.pop()
                    if self.term_mode == 'petscii':
                        from ..features.petscii_codec import CURSOR_LEFT
                        await self.write(f'{CURSOR_LEFT} {CURSOR_LEFT}')
                    else:
                        await self.write('\b \b')
                continue
            if ch < b' ':  # ignore other control chars (Ctrl-C etc just stop)
                continue
            if self.term_mode == 'petscii':
                # Same case-inversion as read_line() -- swap back to the
                # intended logical character before it reaches the
                # password buffer, or every letter arrives wrong-case
                # and auth always fails.
                from ..features.petscii_codec import decode_char
                ch = decode_char(ch[0]).encode('utf-8')
            if len(chars) < _MAX_PASSWORD_CHARS:
                chars.append(ch)
            await self.write('*')
        return b''.join(chars).decode('utf-8', errors='replace').strip()

    def get_chat_manager(self):
        return ServiceLocator.get('chat_manager')

    async def _enforce_time_budget(self):
        """Look up the user's UserTimeBudget and schedule a hard
        session-end task. Sysops are exempt. No-op if no budget exists,
        the table is missing, or anything else goes wrong."""
        if self.user is None or self.user.get('is_admin'):
            return
        try:
            from ..models import UserTimeBudget
            from ..features.bbs_ui import _app
        except Exception:
            return
        try:
            with _app().app_context():
                budget = UserTimeBudget.query.filter_by(
                    user_id=self.user['id']).first()
                if not budget:
                    return
                session_min = budget.time_limit_min or 0
                daily_min = budget.daily_limit_min or 0
                used_today = budget.used_today_min or 0
                bank = budget.bank_minutes or 0
        except Exception:
            return
        # Compute the effective per-session ceiling.
        if session_min <= 0 and daily_min <= 0:
            return
        remaining = session_min if session_min > 0 else 0xFFFF
        if daily_min > 0:
            daily_left = max(0, daily_min - used_today) + bank
            remaining = min(remaining, daily_left)
        if remaining <= 0:
            try:
                await self.write(
                    "\r\n\x1b[1;31mYour daily time budget is exhausted. "
                    "See you tomorrow!\x1b[0m\r\n")
            except Exception:
                pass
            # Close the writer rather than raising — the start() loop
            # will see the closed connection and unwind cleanly.
            try:
                self.writer.close()
            except Exception:
                pass
            return
        # Schedule a soft warning at -5 min and a kill at expiry.
        async def _budget_watchdog():
            warn_at = max(0, (remaining - 5) * 60)
            kill_at = remaining * 60
            try:
                if warn_at > 0:
                    await asyncio.sleep(warn_at)
                    try:
                        await self.write(
                            "\r\n\x1b[1;33m[5 minutes remaining of your "
                            "session time]\x1b[0m\r\n")
                    except Exception:
                        pass
                    await asyncio.sleep(kill_at - warn_at)
                else:
                    await asyncio.sleep(kill_at)
                try:
                    await self.write(
                        "\r\n\x1b[1;31mTime's up — disconnecting.\x1b[0m\r\n")
                except Exception:
                    pass
                # Closing the writer will unwind the session.
                try:
                    self.writer.close()
                except Exception:
                    pass
            except asyncio.CancelledError:
                return
        self._budget_task = asyncio.create_task(_budget_watchdog())

    def _start_kick_watchdog(self):
        """Poll our own NodeActivity row for a sysop kick request.

        The /admin/control/nodespy/<slot>/kick endpoint runs in the
        anetbbs-web (gunicorn) process; terminal sessions live in
        anetbbs-telnet. They don't share memory, so the kick endpoint
        flips `kick_requested` on our DB row and we notice here.

        Polls every 5 seconds while we have a NodeActivity row.
        """
        async def _watch():
            while True:
                try:
                    await asyncio.sleep(5)
                    nid = getattr(self, '_node_activity_id', None)
                    if not nid:
                        return
                    from ..models import NodeActivity, db as _db
                    from ..features.bbs_ui import _app
                    kicked = False
                    reason = ''
                    with _app().app_context():
                        row = NodeActivity.query.get(nid)
                        if row and row.kick_requested:
                            kicked = True
                            reason = row.kick_reason or 'Disconnected by sysop'
                            # Clear flag + delete row so a reconnect
                            # starts clean.
                            try:
                                _db.session.delete(row)
                                _db.session.commit()
                            except Exception:
                                _db.session.rollback()
                    if kicked:
                        try:
                            await self.write(
                                f"\r\n\r\n\x1b[1;31m*** Disconnected by "
                                f"sysop: {reason} ***\x1b[0m\r\n")
                        except Exception:
                            pass
                        try:
                            self.writer.close()
                        except Exception:
                            pass
                        return
                except asyncio.CancelledError:
                    return
                except Exception:
                    # Don't let a transient DB error kill the watchdog.
                    continue
        self._kick_task = asyncio.create_task(_watch())

    async def _collect_security_questions(self):
        """Collect 3 password-recovery security Q&A during terminal registration."""
        if not self.user:
            return
        from ..models import SECURITY_QUESTIONS

        await self.write(
            "\r\n\x1b[1;36m=== Password Recovery Security Questions ===\x1b[0m\r\n"
            "These are used if you ever need to reset your password.\r\n\r\n"
        )
        for i, q in enumerate(SECURITY_QUESTIONS, 1):
            await self.write(f"  \x1b[33m{i:2}.\x1b[0m {q}\r\n")

        qa_pairs = []
        used = set()
        for slot in range(1, 4):
            while True:
                await self.write(f"\r\n\x1b[1mQuestion {slot} of 3\x1b[0m"
                                 f" — enter a number (1-{len(SECURITY_QUESTIONS)}): ")
                raw = await self.read_line("")
                raw = raw.strip()
                if not raw.isdigit():
                    await self.write("\r\nPlease enter a number.\r\n")
                    continue
                idx = int(raw) - 1
                if not (0 <= idx < len(SECURITY_QUESTIONS)):
                    await self.write(f"\r\nChoose between 1 and {len(SECURITY_QUESTIONS)}.\r\n")
                    continue
                if idx in used:
                    await self.write("\r\nYou already used that question — choose a different one.\r\n")
                    continue
                question = SECURITY_QUESTIONS[idx]
                answer = await self.read_line(f"{question}\r\nYour answer: ")
                answer = answer.strip()
                if len(answer) < 2:
                    await self.write("\r\nAnswer too short (minimum 2 characters).\r\n")
                    continue
                used.add(idx)
                qa_pairs.append((question, answer))
                break

        self.user_manager.save_security_answers(self.user['id'], qa_pairs)
        await self.write("\r\n\x1b[1;32mSecurity questions saved.\x1b[0m\r\n")

    async def _run_newuser_questionnaire(self):
        """Walk the sysop's NewUserQuestion list and store answers."""
        if not self.user:
            return
        try:
            from ..models import NewUserQuestion, NewUserAnswer
            from ..features.bbs_ui import _app
            with _app().app_context():
                qs = (NewUserQuestion.query
                      .filter_by(is_active=True)
                      .order_by(NewUserQuestion.sort_order,
                                NewUserQuestion.id).all())
                # Detach to plain tuples so we can iterate with await calls
                # outside the app context.
                questions = [(q.id, q.prompt, bool(q.is_required)) for q in qs]
        except Exception:
            return
        if not questions:
            return
        await self.write("\r\n\x1b[1;36m"
                         "=== New User Questionnaire ===\x1b[0m\r\n")
        for qid, prompt, required in questions:
            tag = ' (required)' if required else ' (Enter to skip)'
            while True:
                ans = await self.read_line(f'{prompt}{tag}: ')
                if ans is None:
                    break
                ans = ans.strip()
                if required and not ans:
                    await self.write(
                        "\x1b[1;31mThis question is required.\x1b[0m\r\n")
                    continue
                # Save (or skip if blank + optional).
                if ans:
                    try:
                        from ..features.bbs_ui import _app as _a
                        with _a().app_context():
                            from ..models import db
                            row = NewUserAnswer(
                                user_id=self.user['id'],
                                question_id=qid,
                                answer=ans[:2000])
                            db.session.add(row)
                            db.session.commit()
                    except Exception:
                        pass
                break

    def _open_node_activity(self, proto):
        """Insert a NodeActivity row so the sysop's NodeSpy can see this
        session. Updated by `_heartbeat_node()` and torn down on logoff.
        Best-effort — silently ignored on DB errors.
        """
        try:
            from ..models import NodeActivity, db as _db
            from ..features.bbs_ui import _app
            with _app().app_context():
                row = NodeActivity(
                    slot=self._node_entry.slot,
                    user_id=self.user['id'],
                    username=self.user.get('username', '?'),
                    protocol=proto,
                    peer=self._node_entry.peer or '',
                    page='login',
                    action='entered BBS',
                )
                _db.session.add(row)
                _db.session.commit()
                self._node_activity_id = row.id
        except Exception:
            self._node_activity_id = None

    def _heartbeat_node(self, page=None, action=None, screen=None):
        """Update the NodeActivity row with current page/action.
        Called from menu_engine after each menu render and after each
        action."""
        if not getattr(self, '_node_activity_id', None):
            return
        try:
            from ..models import NodeActivity, db as _db
            from ..features.bbs_ui import _app
            with _app().app_context():
                row = NodeActivity.query.get(self._node_activity_id)
                if row is None:
                    return
                if page is not None:
                    row.page = (page or '')[:80]
                if action is not None:
                    row.action = (action or '')[:120]
                if screen is not None:
                    row.last_screen = screen[-4000:]  # trim
                row.last_seen = datetime.utcnow()
                _db.session.commit()
        except Exception:
            pass

    def _close_node_activity(self):
        """Delete the NodeActivity row for this session."""
        if not getattr(self, '_node_activity_id', None):
            return
        try:
            from ..models import NodeActivity, db as _db
            from ..features.bbs_ui import _app
            with _app().app_context():
                row = NodeActivity.query.get(self._node_activity_id)
                if row is not None:
                    _db.session.delete(row)
                    _db.session.commit()
        except Exception:
            pass

    async def _bot_gate(self):
        """Anti-bot challenge — port scanners just connect and dump bytes.
        Real users press a key. Require ESC (0x1b) or '*' twice within a
        short window before continuing. Returns True if the user passed,
        False on timeout / wrong input."""
        try:
            from os import environ as _env
            timeout = int(_env.get('BOT_GATE_TIMEOUT', 30))
            await self.write(
                "\r\n\x1b[1;36mPress \x1b[33mESC\x1b[36m or "
                "\x1b[33m*\x1b[36m twice to continue...\x1b[0m\r\n")
            seen = 0
            # Read up to 64 bytes total — enough to absorb any leading
            # IAC negotiation noise but small enough to keep bots out.
            for _ in range(64):
                try:
                    raw = await asyncio.wait_for(
                        self.reader.read(1), timeout=timeout)
                except asyncio.TimeoutError:
                    return False
                if not raw:
                    return False
                # Strip telnet IAC commands (which are bytes >= 0xFA).
                # If we're seeing IAC, just keep waiting for a real key.
                if raw and raw[0] >= 0xF0:
                    continue
                if raw in (b'\x1b', b'*'):
                    seen += 1
                    if seen >= 2:
                        await self.write("\r\n")
                        return True
            return False
        except Exception:
            # Failsafe — don't lock real users out if anything goes wrong.
            return True

    async def _show_ansi_screen(self, slot, *, force_pause=False):
        """Render a sysop-defined ANSI screen by slot name. Best-effort —
        silently no-ops if the slot doesn't exist or the DB is unavailable.

        Resolution order:
          1. {DATA_DIR}/text/{slot}.ans  — file drop-in (highest priority)
          2. BbsAnsiScreen DB row        — set via /admin/menus/screens
          3. nothing                     — silently skip

        Multiple display variants: dropping in {slot}132_2.ans,
        {slot}132_3.ans, ... alongside {slot}132.ans (which counts as
        variant #1) shows ALL of them together, in order, every login —
        the classic Synchronet logon1.ans/logon2.ans/... multi-screen
        convention (each variant is responsible for its own @PAUSE@ if a
        pause is wanted before the next one loads). Name them
        {slot}132_ran.ans / {slot}132_2_ran.ans / ... instead to pick just
        ONE at random each login rather than showing the whole sequence.
        See _resolve_display_screens. Works for both the file-based
        override (step 1) and the bundled stock screen (step 3); a
        single file (the common case) behaves exactly as before.

        @PAUSE@ codes embedded in the ANSI body split the content into pages;
        the user presses any key to advance. force_pause=True adds a final
        pause after the last page (used by the 'ansi' menu action so the
        screen isn't immediately cleared when the menu redraws).

        IMPORTANT: never import anetbbs.web_app here. That module calls
        eventlet.monkey_patch() at import time, which corrupts threading
        primitives in the telnet/SSH/rlogin processes (those use plain
        asyncio + std-threading) and breaks SQLAlchemy's connection pool
        with "cannot notify on un-acquired lock" errors.
        """
        try:
            import pathlib as _pl
            from ..features.bbs_ui import _app

            mode = self.term_mode
            is_plain_text = False

            # 1. File-based override (mode-aware priority)
            #    wide : {slot}132.ans → {slot}.ans
            #    ansi : {slot}.ans
            #    ascii: {slot}.asc
            db_pause = False
            body = None
            _text_dir = None
            try:
                _data_dir = _app().config.get('DATA_DIR', '')
                _text_dir = _pl.Path(_data_dir) / 'text'
                if mode == 'wide':
                    _candidates = [f'{slot}132.ans', f'{slot}.ans']
                elif mode == 'ascii':
                    _candidates = [f'{slot}.asc']
                else:
                    _candidates = [f'{slot}.ans']
                for _fname in _candidates:
                    _picked = _resolve_display_screens(_text_dir, _fname)
                    if _picked is not None:
                        body = _load_display_screens(_picked)
                        is_plain_text = _fname.endswith('.asc')
                        break
            except Exception:
                pass

            # 2. DB lookup (ANSI content — skip for ascii mode)
            if not body and mode != 'ascii':
                from ..models import BbsAnsiScreen
                with _app().app_context():
                    row = BbsAnsiScreen.query.filter_by(
                        slot=slot, is_active=True).first()
                    # An active row with an EMPTY body (e.g. someone saved a
                    # blank draft in the ANSI editor and left it active) is
                    # not meaningfully "configured" — treat it the same as
                    # no row at all so step 3 still falls back to the
                    # bundled stock screen instead of silently showing
                    # nothing. Was previously `body = row.body if row else
                    # None`, which let a truthy row with body='' block the
                    # `if body is None` gate below without actually
                    # providing any content.
                    if row and row.body:
                        db_pause = bool(row.pause_after)
                        body = row.body

            # 3. Bundled stock screen (anetbbs/screens/) — all modes
            if not body:
                body, _plain = _stock_screen(slot, mode)
                if body is not None:
                    is_plain_text = _plain

            if not body:
                return
            # Substitute Synchronet @-codes / Mystic |XX placeholders so
            # the sysop can drop @USER@ / |UN / etc into welcome screens.
            # extract_bps MUST run before apply() since apply() strips @BPS:NNNN@.
            # @PAUSE@ is intentionally NOT in the resolver table — _apply_codes
            # leaves it in place as a literal marker so we can split on it below.
            bps = None
            try:
                from ..features.display_codes import apply as _apply_codes, extract_bps as _extract_bps
                from ..features.bbs_ui import _app as _bbs_app
                import anetbbs as _anetbbs_pkg
                _cfg = _bbs_app().config
                bps = _extract_bps(body)
                body = _apply_codes(
                    body,
                    user=self.user,
                    bbs_name=_cfg.get('BBS_NAME', ''),
                    sysop=_cfg.get('SYSOP_NAME', ''),
                    node=(self._node_entry.slot
                          if getattr(self, '_node_entry', None) else 1),
                    version=getattr(_anetbbs_pkg, '__version__', 'v1.0a'),
                )
            except Exception:
                pass

            # Split on @PAUSE@ for in-content pagination. _apply_codes leaves
            # unknown codes in place, so @PAUSE@ survives as a literal marker.
            _PAUSE_PROMPT = (
                b'\r\n[Press any key to continue]' if is_plain_text
                else b'\r\n\x1b[33m[Press any key to continue]\x1b[0m'
            )
            parts = body.split('@PAUSE@')

            async def _send_part(text, prefix=b'', suffix=b''):
                # ANSI screens are stored as Latin-1 mojibake of the original
                # CP437 bytes (each input byte 0xNN is now codepoint U+00NN in
                # the DB). encode('latin-1') recovers those original bytes,
                # which the user's CP437 terminal renders correctly.
                try:
                    data = prefix + text.encode('latin-1') + suffix
                    if bps:
                        # Throttle output to simulate the requested baud rate.
                        # 8N1 serial = 10 bits/byte → bytes_per_sec = bps / 10.
                        # 20 chunks/sec gives smooth rendering at all speeds.
                        _bps_sec = bps / 10
                        _chunk = max(1, int(_bps_sec / 20))
                        _delay = _chunk / _bps_sec
                        for _i in range(0, len(data), _chunk):
                            self.writer.write(data[_i:_i + _chunk])
                            await self.writer.drain()
                            if _i + _chunk < len(data):
                                await asyncio.sleep(_delay)
                    else:
                        self.writer.write(data)
                        await self.writer.drain()
                except UnicodeEncodeError:
                    await self.write(text)

            for i, part in enumerate(parts):
                is_last = (i == len(parts) - 1)
                prefix = b'\r\n' if i == 0 else b''
                suffix = b'\r\n' if is_last else b''
                await _send_part(part, prefix=prefix, suffix=suffix)
                if not is_last:
                    self.writer.write(_PAUSE_PROMPT)
                    await self.writer.drain()
                    await self.read_key('')

            if force_pause or db_pause:
                self.writer.write(_PAUSE_PROMPT)
                await self.writer.drain()
                await self.read_key('')
        except Exception:
            pass

    async def login_screen(self) -> bool:
        """Display login screen and handle user authentication"""
        # Auto-login path: SSH/rlogin pre-filled the username. Skip the welcome
        # menu and go straight to "Password for <user>: ". One try only — on
        # failure fall through to the normal interactive flow so the user can
        # correct the username.
        if self._prefill_username:
            # Best path: SSH/rlogin client also sent a password — try it
            # silently and skip the prompt entirely.
            if self._prefill_password:
                try:
                    user = self.user_manager.authenticate(
                        self._prefill_username, self._prefill_password,
                        ip=self._client_ip())
                except Exception:
                    user = None
                if user:
                    self.user = user
                    await self.clear_screen()
                    await self.write(
                        f"\r\n=== Login as {self._prefill_username} ===\r\n")
                    await self.write(f"\r\nWelcome back, {self._prefill_username}!\r\n")
                    await asyncio.sleep(1)
                    return True
                # Auto-login failed — fall through to interactive prompt.
                self._prefill_password = None

            await self.clear_screen()
            await self.write(f"\r\n=== Login as {self._prefill_username} ===\r\n\r\n")
            password = await self.read_password(f"Password for {self._prefill_username}: ")
            if password:
                user = self.user_manager.authenticate(
                    self._prefill_username, password, ip=self._client_ip())
                if user:
                    self.user = user
                    await self.write(f"\r\nWelcome back, {self._prefill_username}!\r\n")
                    await asyncio.sleep(1)
                    return True
                await self.write("\r\nInvalid password — falling back to manual login.\r\n")
                await asyncio.sleep(1)
            # Clear the prefill so we don't loop on it
            self._prefill_username = None

        # Sysop's pre-login welcome ANSI (telnet path only — auto-login
        # SSH/rlogin already short-circuited above and never reaches here).
        # Skipped for PETSCII: it's raw sysop-uploaded ANSI art with no
        # reliable degrade path onto a 40/80-col C64 screen.
        if self.term_mode != 'petscii':
            await self._show_ansi_screen('welcome')

        try:
            from ..features.bbs_ui import _app as _bbs_app
            _bbs_name = _bbs_app().config.get('BBS_NAME', 'ANetBBS')
        except Exception:
            _bbs_name = 'ANetBBS'

        while True:
            await self.clear_screen()
            _title = f'Welcome to {_bbs_name}'[:38].center(38)
            if self.term_mode == 'ascii':
                menu = (
                    "\r\n"
                    f"+----------------------------------------+\r\n"
                    f"| {_title} |\r\n"
                    f"+----------------------------------------+\r\n"
                    f"|  1. Login                              |\r\n"
                    f"|  2. New User Registration              |\r\n"
                    f"|  3. Exit                               |\r\n"
                    f"+----------------------------------------+\r\n"
                    "\r\n"
                    "Choice: "
                )
            elif self.term_mode == 'petscii':
                w = self.petscii_width
                inner = w - 4
                bar = '+' + '-' * (w - 2) + '+'

                def _row(s):
                    return '| ' + s[:inner].ljust(inner) + ' |'

                title_p = f'Welcome to {_bbs_name}'[:inner].center(inner)
                menu = (
                    "\r\n"
                    f"{bar}\r\n"
                    f"{_row(title_p)}\r\n"
                    f"{bar}\r\n"
                    f"{_row(' 1. Login')}\r\n"
                    f"{_row(' 2. New User Registration')}\r\n"
                    f"{_row(' 3. Exit')}\r\n"
                    f"{bar}\r\n"
                    "\r\n"
                    "Choice: "
                )
            else:
                B = '\x1b[1m'; R = '\x1b[0m'
                # Bold (B, above) is already combined with each of these
                # everywhere they're used below -- base 30-37 codes here,
                # not bare aixterm 90-97 (MagiTerm/NetRunner/PuTTY don't
                # recognize 90-97 and silently drop the color; SyncTerm
                # happens to, which is why this only showed up elsewhere).
                CY = '\x1b[36m'; YE = '\x1b[33m'; GR = '\x1b[32m'; WH = '\x1b[37m'; DI = '\x1b[37m'
                menu = (
                    "\r\n"
                    f"{B}{CY}╔════════════════════════════════════════╗{R}\r\n"
                    f"{B}{CY}║ {WH}{_title}{CY} ║{R}\r\n"
                    f"{B}{CY}╠════════════════════════════════════════╣{R}\r\n"
                    f"{B}{CY}║  {YE}1{DI}. {GR}Login{' ' * 30}{CY}║{R}\r\n"
                    f"{B}{CY}║  {YE}2{DI}. {GR}New User Registration{' ' * 14}{CY}║{R}\r\n"
                    f"{B}{CY}║  {YE}3{DI}. {GR}Exit{' ' * 31}{CY}║{R}\r\n"
                    f"{B}{CY}╚════════════════════════════════════════╝{R}\r\n"
                    "\r\n"
                    f"{B}{YE}Choice:{R} "
                )

            choice = await self.read_line(menu)
            
            if choice == '1':
                if await self.handle_login():
                    return True
            elif choice == '2':
                if await self.handle_registration():
                    return True
            elif choice == '3':
                return False
            
            # Only show "Press Enter" if login/registration failed
            if choice in ['1', '2']:
                await self.read_line("\r\nPress Enter to continue...")

    async def handle_login(self) -> bool:
        """Handle user login"""
        await self.write("\r\n=== User Login ===\r\n\r\n")
        username = await self.read_line("Username: ")
        if not username:
            return False

        password = await self.read_password("Password: ")
        if not password:
            return False

        user = self.user_manager.authenticate(username, password, ip=self._client_ip())
        if user:
            self.user = user
            await self.write(f"\r\nWelcome back, {username}!\r\n")
            await asyncio.sleep(1)
            return True
        else:
            await self.write("\r\nInvalid username or password.\r\n")
            return False

    async def handle_registration(self) -> bool:
        """Handle new user registration"""
        import re as _re
        await self.write("\r\n=== New User Registration ===\r\n\r\n")

        # Mirror the web form: 3-80 chars, must start with a letter or
        # number, allow spaces / hyphens / periods / apostrophes /
        # underscores in the rest. Previously this used ``str.isalnum``
        # which rejected anything with a space — so "Dr Test" was
        # invalid and the user had to fall back to "drtest". The web
        # registration form has never been that strict; aligning the
        # two paths so the same name works through either entry point.
        _NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._'\-]{1,79}$")

        while True:
            raw = await self.read_line(
                "Choose username (3-80 chars; letters, digits, spaces, "
                "._'- allowed): ")
            if not raw:
                return False
            # Trim + collapse runs of whitespace so " dr   test " becomes
            # "dr test" before the regex check (and before DB storage).
            username = ' '.join(raw.split())
            if not (3 <= len(username) <= 80) or not _NAME_RE.match(username):
                await self.write(
                    "\r\nInvalid username — must be 3-80 chars, start with "
                    "a letter or digit, and only use letters, digits, "
                    "spaces, dot, apostrophe, hyphen, or underscore.\r\n")
                continue
            if self.user_manager.username_exists(username):
                await self.write("\r\nThat username is already taken.\r\n")
                continue
            break

        while True:
            email = await self.read_line("Email address: ")
            if not email or '@' not in email:
                await self.write("\r\nInvalid email address.\r\n")
                continue
            if self.user_manager.email_exists(email):
                await self.write(
                    "\r\nAn account with that email address already exists.\r\n")
                continue
            break

        while True:
            password = await self.read_password("Choose password (min 8 chars): ")
            if not password or len(password) < 8:
                await self.write("\r\nPassword too short.\r\n")
                continue
            
            confirm = await self.read_password("Confirm password: ")
            if password != confirm:
                await self.write("\r\nPasswords don't match.\r\n")
                continue
            break

        result = self.user_manager.create_user(username, password, email)
        if result in ('ok', 'ok_pending'):
            await self.write("\r\nRegistration successful!\r\n")
            await self._show_ansi_screen('newuser')
            # 'ok_pending' (NUV_ENABLED) means this account needs sysop
            # approval before it can log in -- use the ungated internal
            # lookup (safe: it's the account this same request just
            # created) only long enough to collect security questions /
            # the newuser questionnaire, same as the web registration
            # flow does. authenticate() would correctly refuse it, since
            # granting a real logged-in session here is exactly the
            # NUV-approval bypass this fix closes.
            if result == 'ok_pending':
                self.user = self.user_manager.get_user_dict_by_username(username)
            else:
                self.user = self.user_manager.authenticate(
                    username, password, ip=self._client_ip())
            # Password-recovery security questions (same as web registration).
            try:
                await self._collect_security_questions()
            except Exception:
                pass
            # Newuser questionnaire — sysop-defined prompts, answers
            # stored per user. Best-effort, never blocks on errors.
            try:
                await self._run_newuser_questionnaire()
            except Exception:
                pass
            if result == 'ok_pending':
                self.user = None
                await self.write(
                    "\r\n\x1b[1;33mYour account is awaiting sysop approval "
                    "and cannot log in yet. Please try again later.\x1b[0m\r\n")
                await asyncio.sleep(1)
                return False
            await asyncio.sleep(1)
            return True
        elif result == 'email_taken':
            await self.write("\r\nAn account with that email address already exists.\r\n")
            return False
        else:
            await self.write("\r\nUsername already exists.\r\n")
            return False

    async def init_session(self):
        """Initialize the session with proper telnet negotiation"""
        # Send initial telnet negotiations
        await self.send_telnet_command(WILL + ECHO)
        await self.send_telnet_command(WILL + SGA)
        await self.send_telnet_command(DONT + LINEMODE)
        await self.send_telnet_command(DO + NAWS)
        await self.send_telnet_command(DO + TTYPE)

        # TTYPE is a two-round exchange:
        #   round 1: server sends DO TTYPE → client responds WILL TTYPE
        #   round 2: server sends SB TTYPE SEND IAC SE → client responds
        #            SB TTYPE IS <type> IAC SE
        # NAWS subnegotiation arrives in round 1 on its own.
        await asyncio.sleep(0.3)
        try:
            pending = await asyncio.wait_for(self.reader.read(512), timeout=0.05)
            if pending:
                await self.handle_telnet_command(pending)
                # If client agreed to TTYPE, request the actual type string
                if IAC + WILL + TTYPE in pending:
                    self.writer.write(IAC + SB + TTYPE + bytes([1]) + IAC + SE)
                    await self.writer.drain()
                    await asyncio.sleep(0.1)
                    try:
                        pending2 = await asyncio.wait_for(
                            self.reader.read(512), timeout=0.05)
                        if pending2:
                            await self.handle_telnet_command(pending2)
                    except (asyncio.TimeoutError, Exception):
                        pass
        except (asyncio.TimeoutError, Exception):
            pass
        self._buffer.clear()

    async def send_telnet_command(self, command):
        """Send a telnet command. Broken pipes during the initial greeting
        are normal (SCC port probes, scanners) and not worth logging —
        any *unexpected* error still hits the logger so real bugs surface."""
        try:
            self.writer.write(IAC + command)
            await self.writer.drain()
        except (BrokenPipeError, ConnectionResetError,
                ConnectionAbortedError):
            pass
        except Exception as e:
            logger.debug("send_telnet_command failed: %s", e)

    async def handle_telnet_command(self, data):
        """Handle incoming telnet commands"""
        if not data:
            return b''

        result = bytearray()
        i = 0
        while i < len(data):
            byte = data[i:i+1]
            if byte == IAC:
                if i + 1 >= len(data):
                    break
                command = data[i+1:i+2]
                
                if command in [WILL, WONT, DO, DONT]:
                    if i + 2 >= len(data):
                        break
                    option = data[i+2:i+3]
                    i += 3
                    continue
                    
                elif command == SB:
                    i += 2  # past IAC SB
                    if i >= len(data):
                        break
                    option = data[i]
                    i += 1
                    # Collect payload bytes until IAC SE
                    payload = bytearray()
                    while i < len(data):
                        if data[i:i+1] == IAC and i + 1 < len(data) and data[i+1:i+2] == SE:
                            i += 2
                            break
                        payload.append(data[i])
                        i += 1
                    # NAWS: cols-hi cols-lo rows-hi rows-lo
                    if option == NAWS[0] and len(payload) >= 4:
                        cols = (payload[0] << 8) | payload[1]
                        rows = (payload[2] << 8) | payload[3]
                        if cols > 0 and rows > 0:
                            self.window_size = (cols, rows)
                    # TTYPE IS: qualifier \x00 + type string
                    elif option == TTYPE[0] and len(payload) >= 2 and payload[0] == 0:
                        ttype = payload[1:].decode('ascii', errors='replace').strip()
                        if ttype:
                            self.terminal_type = ttype
                    continue
                
                i += 2
                continue
            
            result.append(ord(byte))
            i += 1

        return bytes(result)

    async def read_raw(self, n=1):
        """Read raw bytes from the connection. Honors `self.idle_timeout`
        if set — disconnects sessions that sit idle waiting for a keystroke.
        IRC/MRC are web features and don't go through this path, so the
        idle timer doesn't affect them.

        Raises CarrierLost when the transport disconnects or the idle
        timeout fires, so menu loops break out instead of spinning on
        a permanently-EOF stream."""
        try:
            timeout = getattr(self, 'idle_timeout', 0) or 0
            if timeout > 0:
                data = await asyncio.wait_for(self.reader.read(n),
                                              timeout=timeout)
            else:
                data = await self.reader.read(n)
            if not data:
                raise CarrierLost('client disconnected')
            return await self.handle_telnet_command(data)
        except asyncio.TimeoutError:
            try:
                await self.write(
                    "\r\n\x1b[1;31m[Idle timeout — "
                    "disconnecting]\x1b[0m\r\n")
            except Exception:
                pass
            try:
                self.writer.close()
            except Exception:
                pass
            raise CarrierLost('idle timeout')
        except CarrierLost:
            raise
        except (ConnectionError, BrokenPipeError, EOFError) as e:
            raise CarrierLost(str(e)) from e

    async def write(self, text: str):
        """Write text to the terminal. Broken pipes are normal — port
        probes and rage-quitting clients both close the socket while we
        still have IAC negotiation in flight. Don't spam the journal."""
        try:
            if isinstance(text, str):
                if self.term_mode == 'petscii':
                    # Not the CP437/ANSI encode path at all -- PETSCII is a
                    # different control-code system, not just a different
                    # code page. See anetbbs/features/petscii_codec.py.
                    from ..features import petscii_codec
                    text = petscii_codec.encode(_ANSI_ESC_RE.sub('', text))
                else:
                    if self.term_mode == 'ascii':
                        text = _ANSI_ESC_RE.sub('', text)
                    text = text.encode(self.encoding, errors='replace')
            elif self.term_mode == 'petscii':
                from ..features import petscii_codec
                text = petscii_codec.encode(_ANSI_ESC_RE_B.sub(b'', text).decode('latin-1'))
            elif self.term_mode == 'ascii':
                text = _ANSI_ESC_RE_B.sub(b'', text)
            self.writer.write(text)
            await self.writer.drain()
        except (BrokenPipeError, ConnectionResetError,
                ConnectionAbortedError):
            pass
        except Exception as e:
            logger.debug("write failed: %s", e)

    async def read_line(self, prompt: str = "", max_len: int = 2048) -> str:
        """Read a line of input with echo.

        Real gap found in a full auth-security audit: no cap existed on
        line length -- used for username/email/security-answer prompts
        among many other things, a client that never sends \\r could hold
        this loop open indefinitely, growing the buffer without bound (a
        per-connection memory DoS). 2048 is generous headroom for every
        legitimate use of this shared helper (menu choices, usernames,
        single lines of composed text); extra input beyond max_len is
        silently dropped (still consumed from the stream, still echoed,
        so the loop keeps making forward progress) rather than raising.
        """
        if prompt:
            await self.write(prompt)

        line = bytearray()
        while True:
            try:
                char = await self.read_raw(1)
                if not char:  # Connection closed (defensive — read_raw raises now)
                    raise CarrierLost('client disconnected')

                # Handle special characters. A real C64 keyboard's
                # DEL/INST key sends PETSCII 0x14, not ASCII 0x7f/0x08 --
                # and echoing back ASCII backspace (0x08) means something
                # completely different on real hardware ("disable Shift-
                # Commodore"), not erase-in-place, so the echo sequence
                # needs its own PETSCII cursor-left/space/cursor-left too.
                is_petscii = self.term_mode == 'petscii'
                is_backspace = (char == b'\x14') if is_petscii else \
                              (char == b'\x7f' or char == b'\x08')
                if is_backspace:
                    if line:
                        line.pop()
                        if self.echo:
                            if is_petscii:
                                from ..features.petscii_codec import CURSOR_LEFT
                                await self.write(f'{CURSOR_LEFT} {CURSOR_LEFT}')
                            else:
                                await self.write('\b \b')
                    continue

                elif char == b'\r':  # Enter key
                    if self.echo:
                        await self.write('\r\n')
                    break

                elif char == b'\x03':  # Ctrl-C
                    return ''

                # Regular character
                if len(char) == 1 and 32 <= char[0] <= 126:  # Printable ASCII
                    if is_petscii:
                        # A real C64 keyboard's byte-per-letter mapping is
                        # case-inverted from ASCII in this charset mode,
                        # same as screen output (see petscii_codec.py) --
                        # swap it back to the intended logical character
                        # before it reaches the line buffer / DB lookup,
                        # or every letter of a typed username/password
                        # arrives with the wrong case and auth always fails.
                        from ..features.petscii_codec import decode_char
                        logical_ch = decode_char(char[0])
                        if len(line) < max_len:
                            line.extend(logical_ch.encode(self.encoding, errors='replace'))
                        if self.echo:
                            await self.write(logical_ch)
                    else:
                        if len(line) < max_len:
                            line.extend(char)
                        if self.echo:
                            await self.write(char.decode(self.encoding))

            except CarrierLost:
                raise
            except Exception as e:
                logger.debug("read_line failed: %s", e)
                raise CarrierLost(str(e)) from e

        try:
            return bytes(line).decode(self.encoding).strip()
        except Exception as e:
            logger.debug("read_line decode failed: %s", e)
            return ""

    def get_formatted_time(self) -> str:
        return fmt_eastern(datetime.utcnow(), '%Y-%m-%d %H:%M:%S %Z')

    async def clear_screen(self):
        """Clear the screen and move cursor to home position. ``self.write``
        already drains internally and swallows write errors quietly, so
        the previous bare ``await self.writer.drain()`` here was just a
        double-drain — and it bubbled BrokenPipeError up out of the
        session loop when a port probe disconnected mid-greeting. The
        outer start() handler now classifies those as a clean carrier
        loss too, but removing the redundant drain is the cheap fix."""
        if self.term_mode == 'petscii':
            # The ANSI escape below has no PETSCII meaning -- write()
            # would just strip it to nothing, silently no-opping clear
            # screen entirely. Use the real C64 clear+home control byte.
            from ..features.petscii_codec import CLR_HOME
            await self.write(CLR_HOME)
            return
        await self.write("\x1b[2J\x1b[H\x1b[0m")

    async def _show_notification_summary(self):
        """Blocking pop-up shown once at login when anything is waiting:
        lists every unread Notification by name/detail (echomail/QWK
        replies, netmail, @mentions, etc. -- whatever notify() created,
        see anetbbs/features/notify.py) plus PM/IM counts, and requires
        the user to press Enter before continuing into the menu loop.

        Previously this only printed a passive one-line count ("*** You
        have new: 2 notifications") that scrolled past like any other
        banner -- easy to miss entirely on a fast connection or a client
        with limited scrollback, and it didn't say who or where. Silent
        (no pop-up, no pause) if nothing is waiting.
        """
        try:
            uid = self.user.get('id') if isinstance(self.user, dict) else None
            if not uid:
                return
            from ..models import (PrivateMessage, InstantMessage,
                                  Notification)
            pm_n = (PrivateMessage.query
                    .filter_by(recipient_id=uid, read_at=None,
                               is_deleted_recipient=False).count())
            im_n = (InstantMessage.query
                    .filter_by(recipient_id=uid, is_read=False).count())
            try:
                unread_notifs = (Notification.query
                                  .filter_by(user_id=uid, is_read=False)
                                  .order_by(Notification.id).all())
            except Exception:
                unread_notifs = []
            if not (pm_n or im_n or unread_notifs):
                return

            await self.write(
                '\r\n\x1b[1;33m'
                '+============================================+\r\n'
                '|         YOU HAVE NEW NOTIFICATIONS          |\r\n'
                '+============================================+'
                '\x1b[0m\r\n')
            if pm_n:
                await self.write(
                    f'  \x1b[1;36m{pm_n}\x1b[0m new Private Message'
                    + ('s' if pm_n != 1 else '') + '\r\n')
            if im_n:
                await self.write(
                    f'  \x1b[1;36m{im_n}\x1b[0m new InterBBS IM'
                    + ('s' if im_n != 1 else '') + '\r\n')
            # Each Notification's own title/body already carries the
            # "who, and in what area/context" detail (see notify() call
            # sites -- e.g. anetbbs/echomail/notify_reply.py's "Jane
            # wrote to you" / "in FidoNet (General)"), so list them by
            # name rather than folding into a bare count.
            for n in unread_notifs:
                line = f'  \x1b[33m-\x1b[0m {n.title}'
                if n.body:
                    line += f' \x1b[36m({n.body})\x1b[0m'
                await self.write(line + '\r\n')
            await self.write('\r\n\x1b[1mPress ENTER to continue...\x1b[0m')
            try:
                await self.read_line('')
            except Exception:
                pass
        except Exception:
            pass

    async def _show_pending_broadcasts(self):
        """Render any active sysop broadcasts as a banner on session entry.

        We use a separate per-user marker (last_seen_broadcast_id stored on
        the user_metadata-ish UserActivity log) — actually simpler: just show
        the most recent 5 broadcasts that haven't expired. The user can still
        see them on every login until they expire."""
        try:
            from datetime import datetime as _dt
            from ..models import db, SysopBroadcast
            now = _dt.utcnow()
            recents = (SysopBroadcast.query
                       .filter(db.or_(SysopBroadcast.expires_at.is_(None),
                                      SysopBroadcast.expires_at > now))
                       .order_by(SysopBroadcast.created_at.desc())
                       .limit(5).all())
            if not recents:
                return
            await self.write('\r\n\x1b[1;33m=== Sysop Broadcasts ===\x1b[0m\r\n')
            for b in reversed(recents):  # oldest first
                ts = fmt_eastern(b.created_at, '%m/%d %H:%M')
                sender = b.sender.username if b.sender else 'sysop'
                await self.write(
                    f'\x1b[33m[{ts}] {sender}:\x1b[0m {b.text}\r\n')
            await self.write('\r\n')
        except Exception:
            # Never block login on broadcast surfacing failure.
            pass

    async def show_main_menu(self):
        menu = (
            "\r\n"
            "╔════════════════════════════════════════╗\r\n"
            "║           Welcome to AnetBBS           ║\r\n"
            "╚════════════════════════════════════════╝\r\n"
            f" Date: {self.get_formatted_time()} \r\n"
            f" User: {self.user['username']:<30} \r\n"
            "╔════════════════════════════════════════╗\r\n"
            "║  1. Chat Menu                          ║\r\n"
            "║  2. Game Center                        ║\r\n"
            "║  3. Main BBS Menu (boards/PM/files)    ║\r\n"
            "║  4. Exit                               ║\r\n"
            "╚════════════════════════════════════════╝\r\n"
            "\r\n"
            "Choice: "
        )
        return await self.read_line(menu)

    async def start(self):
        """Start the BBS session"""
        presence = None
        _hb_task = None
        self._session_started_at = datetime.utcnow()
        # Idle timeout for terminal menus (seconds). IRC/MRC use a different
        # path so they are unaffected. Defaults to 0 (off) — sysop opts in
        # by setting IDLE_TIMEOUT_SECONDS in .env. Bullet-proofed against
        # malformed values.
        try:
            raw = os.environ.get('IDLE_TIMEOUT_SECONDS', '').strip()
            self.idle_timeout = int(raw) if raw else 0
            if self.idle_timeout < 0:
                self.idle_timeout = 0
        except Exception:
            self.idle_timeout = 0
        try:
            await self.init_session()
            await asyncio.sleep(0.1)  # Give time for telnet negotiation

            # Bot defense — auto-scanners just connect and dump bytes.
            # Real users press a key. Skip when an SSH/rlogin client has
            # already sent valid auth (those went through real protocols).
            if not self._prefill_username:
                if not await self._bot_gate():
                    return

            if not await self.login_screen():
                return

            # Multinode slot acquisition — claim a node 1..BBS_NODES so
            # the user shows up on the multinode roster. If all nodes are
            # in use we politely turn the user away.
            try:
                from ..features.multinode import acquire_slot, broadcast
                max_nodes = max(1, min(100,
                    int(os.environ.get('BBS_NODES', '8'))))
                wname = type(self.writer).__name__.lower()
                proto = ('ssh' if 'ssh' in wname else
                         ('rlogin' if 'rlogin' in wname else 'telnet'))
                self._node_entry = acquire_slot(self.user, proto,
                                                str(self._session_started_at),
                                                max_nodes,
                                                session=self)
                if self._node_entry is None:
                    await self.write(
                        f"\r\n\x1b[1;31mAll {max_nodes} nodes are "
                        f"in use — try again later.\x1b[0m\r\n")
                    return
                broadcast(self.user.get('username', '?'),
                          f'logged in on node {self._node_entry.slot} '
                          f'({proto})', kind='join')
                # Open the NodeActivity row for sysop NodeSpy and start
                # the cross-process kick watchdog.
                self._open_node_activity(proto)
                try:
                    self._start_kick_watchdog()
                except Exception:
                    pass
            except Exception:
                self._node_entry = None

            # Cross-protocol presence: register this user in the SAME UserSession
            # table the web uses, so the web's "online" widget shows telnet/SSH/
            # rlogin users alongside web users.
            from .presence import SessionPresence
            peer = ''
            try:
                addr = self.writer.get_extra_info('peername')
                if addr:
                    peer = f'{addr[0]}:{addr[1]}' if isinstance(addr, tuple) else str(addr)
            except Exception:
                pass
            # Detect protocol from the writer class name (TelnetWriter / SshWriter / etc)
            wname = type(self.writer).__name__.lower()
            proto = 'ssh' if 'ssh' in wname else ('rlogin' if 'rlogin' in wname else 'telnet')
            presence = SessionPresence(self.user['id'], protocol=proto, peer=peer)

            # Caller log row — best effort, never block login. Mirrors
            # web/auth.py's login route, which already does this for web
            # logins; telnet/SSH/rlogin never had an equivalent, so "Last
            # Callers" only ever showed web users on an otherwise
            # telnet-first BBS.
            try:
                from ..models import CallerLog, db as _db
                from ..features.bbs_ui import _app
                # `peer` (set above) is always defined -- 'ip:port' or ''
                # -- unlike `addr`, which can be left unbound if the
                # get_extra_info() call above raised.
                _ip = peer.rsplit(':', 1)[0] if peer else ''
                with _app().app_context():
                    _cl = CallerLog(
                        user_id=self.user['id'],
                        username=self.user.get('username', '?'),
                        service=proto,
                        ip_address=_ip)
                    _db.session.add(_cl)
                    _db.session.commit()
                    try:
                        from ..echomail.interbbs_sync import post_lastcaller_to_interbbs
                        post_lastcaller_to_interbbs(_cl)
                    except Exception:
                        pass
                    try:
                        from ..features.webhooks import fire
                        fire('login', {'user': self.user.get('username', '?'),
                                       'service': proto})
                    except Exception:
                        pass
            except Exception:
                pass

            # Fast logon check — if the sysop has enabled it, offer the user
            # a chance to skip logon modules and jump straight to the menu.
            # Read directly from .env so changes take effect without restart.
            fast_logon = False
            # Logon modules (wall, ansi screens, shell hooks, etc.) have no
            # PETSCII-aware rendering at all -- wall.py and the ansi-screen
            # loader both write raw CP437/ANSI bytes directly to the socket,
            # bypassing write()'s petscii branch entirely, and none of them
            # are on the Phase 1 core-BBS feature list. Skip them the same
            # way Games/doors/ANetCRAFT/DarkForces/MRC/IRC are skipped --
            # not shown at all, rather than shown broken.
            if self.term_mode != 'petscii':
                try:
                    import os as _os
                    _fl_enabled = False
                    _fl_env = _os.path.abspath(_os.path.join(
                        _os.path.dirname(_os.path.abspath(__file__)),
                        '..', '..', '.env'))
                    try:
                        with open(_fl_env) as _fe:
                            for _fl_line in _fe:
                                if _fl_line.startswith('FAST_LOGON_ENABLED='):
                                    _fl_val = _fl_line.split('=', 1)[1].strip().lower()
                                    _fl_enabled = _fl_val in ('true', '1', 'yes')
                                    break
                    except Exception:
                        pass
                    if _fl_enabled:
                        _fl_resp = (await self.read_line(
                            '\r\n\x1b[1;33m[F]ast logon — skip intro modules? [y/N]:\x1b[0m ') or '')
                        fast_logon = _fl_resp.strip().lower() == 'y'
                except Exception:
                    pass

                # Run logon modules (wall, ansi screens, shell hooks, etc.)
                try:
                    from ..features.login_modules import run_modules as _run_logon
                    await _run_logon(self, 'logon', fast_logon=fast_logon)
                except Exception:
                    pass

            # Prefer the data-driven menu engine if any BbsMenu rows exist;
            # otherwise fall back to the hard-coded BBSMenuUI.show_main().
            from ..features.menu_engine import run_menu
            presence.set_page('main')

            # Periodic heartbeat so idle users stay in the "who's online"
            # 5-minute window even when they're not actively navigating menus.
            async def _presence_heartbeat():
                while True:
                    await asyncio.sleep(120)
                    presence.heartbeat()
            _hb_task = asyncio.ensure_future(_presence_heartbeat())

            # Surface any unread sysop broadcasts on entry — telnet/SSH/rlogin
            # users see them on the way into the menu loop, mirroring the
            # toast that pops in the web UI.
            await self._show_notification_summary()
            await self._show_pending_broadcasts()
            # Establish the baseline for check_new_notifications()'s
            # "while already online" check in the menu loop below --
            # everything unread as of THIS point was already covered by
            # _show_notification_summary() above, so the menu loop must
            # only announce notifications that arrive AFTER login.
            from ..features.notify import check_new_notifications
            await check_new_notifications(self)
            try:
                if self.term_mode == 'petscii':
                    # PETSCII sessions get their own dedicated, hand-built
                    # menu loop (Phase 1: core BBS only -- boards/echomail/
                    # PM/files/who's-online/profile) instead of the
                    # general ANSI-native run_menu()/BBSMenuUI.show_main()
                    # -- see anetbbs/features/petscii_ui.py.
                    from ..features.petscii_ui import run_petscii_menu
                    await run_petscii_menu(self)
                else:
                    await run_menu(self, start='main')
            except (CarrierLost, BrokenPipeError, ConnectionResetError,
                    ConnectionAbortedError):
                # Clean unwind — client (or a TCP port probe) disconnected.
                # Don't dump a traceback: the SCC's listener probes hit
                # every protocol every few seconds and the BBS would
                # otherwise spam the journal with BrokenPipeError stacks.
                pass
            except Exception as menu_exc:
                import traceback as _tb
                print(f"Menu error: {menu_exc}")
                print(_tb.format_exc())
                try:
                    await self.write(
                        f"\r\n\x1b[1;31mMenu error: {menu_exc}\x1b[0m\r\n")
                except Exception:
                    pass
        except (CarrierLost, BrokenPipeError, ConnectionResetError,
                ConnectionAbortedError):
            # Carrier dropped during login / pre-menu setup, OR a TCP
            # port probe hit telnet/SSH/rlogin and immediately closed.
            # Both look identical at this layer — unwind quietly.
            pass
        except Exception as e:
            import traceback as _tb
            print(f"Session error: {e}")
            print(_tb.format_exc())
            try:
                await self.write(
                    f"\r\n\x1b[1;31mSession error: {e}\x1b[0m\r\n")
            except Exception:
                pass
        finally:
            # Cancel the periodic presence heartbeat task.
            if _hb_task is not None:
                _hb_task.cancel()
            # Run logoff modules before tearing down the session -- same
            # petscii skip as the logon modules above (wall/ansi-screen/
            # shell-hook modules have no PETSCII-aware rendering at all).
            if self.user and self.term_mode != 'petscii':
                try:
                    from ..features.login_modules import run_modules as _run_logoff
                    await _run_logoff(self, 'logoff', fast_logon=False)
                except Exception:
                    pass
            if presence is not None:
                presence.disconnect()
            # Release the multinode slot + announce departure.
            try:
                from ..features.multinode import release_slot, broadcast
                if getattr(self, '_node_entry', None) is not None:
                    if self.user:
                        broadcast(self.user.get('username', '?'),
                                  f'logged off from node '
                                  f'{self._node_entry.slot}', kind='part')
                    release_slot(self._node_entry)
            except Exception:
                pass
            # Cancel the kick watchdog before clearing NodeActivity so
            # it doesn't try to query a row we just deleted.
            try:
                kt = getattr(self, '_kick_task', None)
                if kt and not kt.done():
                    kt.cancel()
            except Exception:
                pass
            # Tear down NodeActivity row.
            try:
                self._close_node_activity()
            except Exception:
                pass
            # Sysop-defined goodbye ANSI (falls back to the simple text below
            # if no 'goodbye' slot is configured). _show_ansi_screen has no
            # PETSCII-aware rendering -- it writes raw CP437/ANSI bytes
            # directly to the socket, bypassing write()'s petscii branch --
            # so petscii sessions skip straight to the plain-text fallback,
            # which already goes through write() correctly.
            if self.term_mode != 'petscii':
                try:
                    await self._show_ansi_screen('goodbye')
                except Exception:
                    pass
            await self.write("\r\nGoodbye!\r\n")
