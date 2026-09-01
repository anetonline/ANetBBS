# anetbbs/core/session.py
import asyncio
import logging
import os
import re
import textwrap
from datetime import datetime
from .tz import fmt_eastern
from typing import Dict, Optional, Any

_ANSI_ESC_RE   = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
_ANSI_ESC_RE_B = re.compile(rb'\x1b\[[0-9;]*[A-Za-z]')


def _prompt_width(session) -> int:
    """Terminal width for wrapping long registration/questionnaire
    prompts -- petscii_width takes priority (a real C64 screen's width
    is fixed; window_size is normally 80x24 there regardless of the
    actual 40/80-col session, see petscii_ui.py's own _width() helper),
    falling back to window_size[0], then 80."""
    w = getattr(session, 'petscii_width', None)
    if w:
        return w
    ws = getattr(session, 'window_size', None)
    try:
        return int(ws[0]) if ws else 80
    except (TypeError, ValueError, IndexError):
        return 80


def _wrap_text_lines(text: str, width: int) -> list:
    """Word-wrap *text* to *width* columns, one physical line per list
    entry. Real bug found live on the Pi (PETSCII, 40 columns): long
    security-question / newuser-questionnaire prompts were written as a
    single unwrapped line each and left to the terminal's own hardware
    auto-wrap, which breaks mid-word with no word-boundary awareness."""
    width = max(10, width)
    lines = []
    for para in (text or '').split('\n'):
        lines.extend(textwrap.wrap(para, width) or [''])
    return lines

# Synchronet-style spinning cursor (User.cursor_style == 'spinning') --
# rotates while genuinely idle waiting for the next keystroke, then
# erases itself the instant real input arrives. Tick interval loosely
# matches Synchronet's own K_SPIN feel; not configurable, this is a
# cosmetic/accessibility choice, not something worth a setting-for-a-setting.
_SPIN_CHARS = ('|', '/', '-', '\\')
_SPIN_TICK_SECONDS = 0.5

# AFK warning + screensaver (AFK_WARNING_SECONDS in .env) -- see
# _run_afk_sequence(). Module-level so tests can shrink them for fast
# runs, same convention as _SPIN_TICK_SECONDS above.
_AFK_WARNING_COUNTDOWN_SECONDS = 20   # mirrors the rcsafk.mps reference script's own TimeOut default
_AFK_TICK_SECONDS = 1.0
_AFK_FRAME_INTERVAL_SECONDS = 0.09


class _AFKInterrupted(Exception):
    """Internal-only signal: an AFK warning/screensaver sequence just
    ended (cancelled during the warning countdown, or dismissed from
    the screensaver) -- never propagates outside this module. The
    dismissing keystroke is deliberately consumed, not treated as real
    input, matching the reference rcsafk.mps script's own behavior
    (reads the wake key, then Halts -- never passes it through as a
    menu selection). Caught at each of the three keystroke-read entry
    points (read_key/read_line/read_key_arrow), which redraw whatever
    they can (their own prompt, plus any already-typed buffer for
    read_line) and loop back to read the caller's real next keystroke."""


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
    is SAUCE-stripped (see features/sauce.py -- an unstripped trailer
    renders as literal garbage at the end of the screen; this was the
    only file-based screen loader in the whole app that never did this)
    and decoded independently (latin-1, matching how screen bodies are
    decoded elsewhere), then simply joined — see _resolve_display_screens
    for why there's no automatic pause insertion between them."""
    from ..features.sauce import strip as _strip_sauce
    return ''.join(_strip_sauce(p.read_bytes()).decode('latin-1') for p in paths)


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


# Inline in-place animation for ANSI screens: '@ANIMSTART@<frame>@FRAME@
# <frame>@FRAME@...@ANIMEND@' embedded directly in a screen's body plays
# each frame in place (cursor up N lines, reprint, repeat) instead of a
# one-shot static block. N is inferred per-play from the first frame's own
# line count -- every frame must be exactly that many '\r\n'-terminated
# lines, so reprinting one naturally re-descends the cursor by the same N
# it moved up, landing back exactly where it started once the last frame
# is shown. No parameters are embedded in the marker itself (deliberately
# NOT '@ANIM:rows:delay@' -- that shape collides with display_codes.py's
# '@CODE:value@' stripping regex, which runs earlier in the pipeline and
# would silently delete it before this ever sees it). Uppercase-only
# names (ANIMSTART/FRAME/ANIMEND) match the existing @PAUSE@ precedent:
# unknown @-codes are left in place by display_codes.apply(), not stripped.
_SCREEN_ANIM_SPLIT_RE = re.compile(r'(@PAUSE@|@ANIMSTART@.*?@ANIMEND@)', re.DOTALL)
_ANIM_FRAME_DELAY = 0.3  # seconds between frames -- 0.18 read as an
# imperceptible flicker in live testing (screen already had a lot on it
# by the time the animation fired); slower reads as an actual gesture.


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
                 forced_width: Optional[int] = None,
                 direct_door_slug: Optional[str] = None):
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
        direct_door_slug: set by rlogin_server.py when the inbound handshake's
            terminal/speed field carried 'xtrn=<slug>' (Jerry's own A-Net Game
            Server convention, and the same one ANetBBS's own outbound
            rlogin_bridge.py already speaks to other game servers). On a
            successful login, start() launches this game directly -- skipping
            logon modules and the main menu -- and the session ends (hanging
            up the connection) when the game exits, matching how a real
            Synchronet rlogin game-server target behaves.
        """
        self.reader = reader
        self.writer = writer
        self.config = config
        self.user_manager = UserManager()
        self.user = None
        self._forced_term_mode = forced_term_mode
        self._forced_width = forced_width
        self._direct_door_slug = (direct_door_slug or '').strip() or None
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

    async def _read_byte_maybe_spinning(self, n=1, overall_timeout=None, allow_afk=False):
        """Read n bytes from the transport, blocking exactly like
        `self.reader.read(n)` -- except when the user has the
        'spinning' cursor preference set, in which case it redraws a
        rotating |/-\\ glyph in place at short intervals while
        genuinely idle waiting for the next byte, then erases it the
        instant real input arrives.

        Mirrors Synchronet's own K_SPIN: confirmed (via their BAJA
        scripting docs) that it's a mode flag passed to the blocking
        input-read call itself, not a separate always-running
        background task. Building it the same way here means every
        input primitive that routes through this helper gets spinning
        "for free" with no extra state to track across screens, and it
        can never keep spinning after a real keystroke arrives (unlike
        a standalone timer task, which would need explicit cancellation
        at every call site).

        This is the ONLY read call site that checks cursor_style --
        sub-reads inside an already-started escape sequence (CSI
        continuation bytes, password entry, etc.) intentionally keep
        using a plain `self.reader.read()`/`read_raw()` call, since
        spinning only matters while waiting for the FIRST byte of a
        new keystroke, not during the sub-100ms parsing of a sequence
        already in flight.

        overall_timeout: if given, raises asyncio.TimeoutError once
        this many seconds have elapsed with no byte received at all --
        lets read_raw's existing idle-timeout disconnect keep working
        even while spinning is also enabled (short spin ticks accrue
        toward the same overall deadline, rather than each tick
        resetting it).

        Also the single detection point for the AFK warning/screensaver
        (self.afk_warning_seconds, see start() and _run_afk_sequence()),
        gated behind `allow_afk` -- unlike spinning, this is NOT "every
        input primitive gets it for free": read_raw() is called directly
        by several features (door games' own poll loops, IRC/telnet
        bridges, the ANSI editor, dialout) with their own timeout/retry
        semantics and broad `except Exception` handlers that would
        silently misinterpret an AFK interruption as "the game/door
        ended" rather than "resume where you were" -- real risk found
        auditing every read_raw() call site before wiring this up.
        Only read_key()/read_key_arrow() (direct callers) and
        read_line() (via read_raw(..., allow_afk=True)) opt in; every
        other caller is completely unaffected by this feature.
        """
        # Only single-byte "waiting for a keystroke" reads ever spin or
        # trigger AFK detection -- a hypothetical future n>1 bulk read
        # (file transfers use their own raw self.reader.read() calls,
        # never this helper, but guard anyway) must never have spin-
        # glyph bytes injected into what could be a binary data stream,
        # and sub-reads inside an already-started escape sequence (CSI
        # continuation bytes, password entry, etc.) shouldn't count
        # toward idle time or interrupt mid-sequence.
        cursor_style = 'default'
        afk_seconds = 0.0
        if n == 1:
            if isinstance(self.user, dict):
                cursor_style = self.user.get('cursor_style') or 'default'
            if allow_afk:
                afk_seconds = getattr(self, 'afk_warning_seconds', 0) or 0
        spinning = (cursor_style == 'spinning')
        if not spinning and afk_seconds <= 0:
            if overall_timeout:
                return await asyncio.wait_for(self.reader.read(n),
                                              timeout=overall_timeout)
            return await self.reader.read(n)
        idx = 0
        elapsed = 0.0
        while True:
            tick = _SPIN_TICK_SECONDS
            if overall_timeout is not None:
                remaining = overall_timeout - elapsed
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                tick = min(tick, remaining)
            if afk_seconds > 0:
                to_afk = afk_seconds - elapsed
                if to_afk <= 0:
                    # Always raises: _AFKInterrupted (cancelled/dismissed)
                    # or asyncio.TimeoutError (the outer idle_timeout ran
                    # out while nobody ever came back) -- never returns
                    # normally, so nothing after this call executes.
                    remaining_after = (
                        (overall_timeout - elapsed) if overall_timeout is not None
                        else None)
                    await self._run_afk_sequence(remaining_after)
                tick = min(tick, to_afk)
            try:
                data = await asyncio.wait_for(self.reader.read(n),
                                              timeout=tick)
            except asyncio.TimeoutError:
                elapsed += tick
                if overall_timeout is not None and elapsed >= overall_timeout:
                    raise
                if spinning:
                    idx = (idx + 1) % len(_SPIN_CHARS)
                    try:
                        # Draw the glyph, then back the cursor up so the
                        # next real keystroke's echo lands exactly where
                        # the user expects it, not one column to the right.
                        await self.write(_SPIN_CHARS[idx] + '\b')
                    except Exception:
                        pass
                continue
            if data and spinning:
                try:
                    # Erase the last-drawn glyph so it doesn't linger
                    # once real input starts arriving.
                    await self.write(' \b')
                except Exception:
                    pass
            return data

    async def _afk_peek(self, timeout):
        """Non-blocking-in-practice single-byte read used by
        _run_afk_sequence's own warning/screensaver loops. Deliberately
        bypasses read_raw()/_read_byte_maybe_spinning() entirely (a
        plain self.reader.read(1) instead) -- calling back through
        those would restart AFK detection recursively with its own
        fresh elapsed=0, since that state lives in the caller's stack
        frame, not on self. Returns the byte(s) read, or None on
        timeout (no key pressed yet)."""
        try:
            return await asyncio.wait_for(self.reader.read(1), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def _run_afk_sequence(self, remaining_timeout):
        """Idle-detected: warn with a live countdown, then (if nobody
        responds) run a matrix-rain screensaver, mirroring the
        reference rcsafk.mps script's own two-stage flow. Always ends
        by raising -- either _AFKInterrupted (a keystroke cancelled the
        warning or dismissed the screensaver -- consumed, not passed
        through as real input) or asyncio.TimeoutError (remaining_timeout,
        the outer idle_timeout ceiling, ran out while nobody ever came
        back -- bubbles up through read_raw()'s existing idle-disconnect
        handling completely unchanged, no new hangup logic needed).

        remaining_timeout: seconds left of the outer idle_timeout, or
        None if that's off (screensaver then runs indefinitely with no
        forced disconnect, matching the reference script's own optional
        `If CfgTimeOut > 0` hangup guard).
        """
        from ..features.ansi_ui import FG, BOLD, RESET
        from ..features.afk_screensaver import MatrixRain

        loop = asyncio.get_event_loop()
        deadline = None
        if remaining_timeout is not None:
            deadline = loop.time() + max(0.0, remaining_timeout)

        def _time_left():
            if deadline is None:
                return None
            return deadline - loop.time()

        async def _afk_return():
            try:
                self._heartbeat_node(action='Returned from AFK')
            except Exception:
                pass
            try:
                await self.write(
                    '\x1b[2J\x1b[H'
                    f"{FG['grn']}{BOLD}Welcome back!{RESET}\r\n\r\n")
            except Exception:
                pass

        try:
            self._heartbeat_node(action='Possibly AFK')
        except Exception:
            pass

        # ---- Warning stage ----
        try:
            await self.write(
                '\x1b[r'                                          # reset any leftover scroll region
                '\x1b[2J\x1b[H'
                f"{FG['yel']}{BOLD}You've been idle a while...{RESET}\r\n\r\n"
                f"{FG['wht']}Press any key to stay connected.{RESET}\r\n")
        except Exception:
            pass

        remain = _AFK_WARNING_COUNTDOWN_SECONDS
        while remain > 0:
            left = _time_left()
            if left is not None and left <= 0:
                raise asyncio.TimeoutError()
            tick = _AFK_TICK_SECONDS if left is None else min(_AFK_TICK_SECONDS, left)
            try:
                await self.write(
                    f'\r{FG["cyan"]}Screensaver in {remain:>2}s...{RESET} ')
            except Exception:
                pass
            data = await self._afk_peek(tick)
            if data:
                await _afk_return()
                raise _AFKInterrupted()
            remain -= 1

        # ---- Screensaver stage ----
        try:
            self._heartbeat_node(action='Away From Keyboard (screensaver)')
        except Exception:
            pass
        ws = getattr(self, 'window_size', None) or (80, 24)
        cols = max(1, int(ws[0]) - 2)
        rows = max(1, int(ws[1]) - 2)
        rain = MatrixRain(cols, rows)
        try:
            await self.write('\x1b[2J\x1b[H')
        except Exception:
            pass

        while True:
            left = _time_left()
            if left is not None and left <= 0:
                raise asyncio.TimeoutError()
            frame_start = loop.time()
            rain.step()
            try:
                await self.write(''.join(rain.frame_lines()))
            except Exception:
                pass
            frame_elapsed = loop.time() - frame_start
            wait = max(0.0, _AFK_FRAME_INTERVAL_SECONDS - frame_elapsed)
            if left is not None:
                wait = min(wait, left)
            data = await self._afk_peek(wait)
            if data:
                await _afk_return()
                raise _AFKInterrupted()

    async def _maybe_send_steady_cursor(self):
        """FR from Winzlo (accessibility): a blinking cursor makes
        iOS/macOS zoom's "follow keyboard focus" repeatedly recenter
        the screen on it, fighting anyone trying to look elsewhere
        (e.g. a menu) while connected -- confirmed reproducible across
        four separate SSH clients. DECSCUSR Ps=4 asks the client for a
        steady (non-blinking) underline cursor -- matches the shape
        most terminal clients already show by default, just without
        the blink. Sent once at login rather than on every prompt; a
        client that doesn't understand DECSCUSR harmlessly ignores it
        (well-supported xterm extension). No-op for every cursor_style
        other than 'steady' -- 'default' changes nothing, 'spinning' is
        handled entirely by _read_byte_maybe_spinning instead."""
        if isinstance(self.user, dict) and self.user.get('cursor_style') == 'steady':
            try:
                await self.write('\x1b[4 q')
            except Exception:
                pass

    async def read_key(self, prompt: str = '', on_afk_redraw=None) -> str:
        """Read a single keystroke and return it (uppercase ASCII).

        Returns '' if the user pressed Enter without a hotkey (so menu
        loops can no-op + redraw). Discards arrow keys / function keys
        and other multi-byte ESC sequences. The keystroke is echoed
        followed by CRLF — same UX as line input but without the wait
        for Enter.

        on_afk_redraw: optional async no-arg callable, invoked instead
        of the bare prompt-only redraw when an AFK screensaver just
        ended (see _AFKInterrupted below). Real gap found live: `prompt`
        alone is often just a trailing "Choice: " -- the actual menu
        content (banner/items/etc) was drawn by the CALLER before ever
        calling read_key(), and the AFK screensaver's own screen-clear
        wipes it, so a prompt-only redraw left the caller staring at a
        bare prompt with no menu until they pressed ANOTHER key. Callers
        that own real screen state (menu_engine.py's run_menu(), the
        main offender) should pass their own redraw routine here; every
        other call site is unaffected (falls back to the old prompt-only
        behavior, unchanged).

        Raises CarrierLost if the transport has gone away — callers
        must not confuse this with bare-Enter (both used to look like '')."""
        if prompt:
            await self.write(prompt)
        while True:
            try:
                ch = await self._read_byte_maybe_spinning(1, allow_afk=True)
            except _AFKInterrupted:
                # AFK warning/screensaver just ended -- the dismissing
                # keystroke is consumed, not treated as this prompt's
                # real answer. Prefer the caller's own full redraw if it
                # gave us one; otherwise fall back to just the prompt
                # (all we have at this level -- the screensaver cleared
                # the screen). Then go back to waiting for the real next
                # keystroke.
                if on_afk_redraw is not None:
                    try:
                        await on_afk_redraw()
                    except Exception:
                        if prompt:
                            await self.write(prompt)
                elif prompt:
                    await self.write(prompt)
                continue
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

    async def read_key_arrow(self, on_afk_redraw=None) -> str:
        """Read a single keystroke including arrow/navigation keys. No echo.

        Returns one of: 'UP' 'DOWN' 'LEFT' 'RIGHT' 'PGUP' 'PGDN' 'HOME'
        'END' 'ENTER' 'ESC' 'CTRL_C' or an uppercase printable character.
        Raises CarrierLost if the connection drops.

        on_afk_redraw: see read_key()'s own docstring for the full
        reasoning -- same optional full-screen-redraw hook.

        CSI sequences (PgUp = ESC [ 5 ~, PgDn = ESC [ 6 ~, etc.) are read
        byte-by-byte until the VT final byte (0x40–0x7E) so they are never
        fragmented by a partial stream.read() return."""
        while True:
            try:
                ch = await self._read_byte_maybe_spinning(1, allow_afk=True)
            except _AFKInterrupted:
                # No prompt owned at this level -- if the caller gave us
                # a redraw hook, use it (a lightbar-style menu still
                # benefits from an immediate redraw instead of waiting
                # for its next navigation keypress); otherwise this
                # primitive's callers redraw their own screen on every
                # navigation cycle anyway, so just consume the
                # dismissing keystroke and wait for the real next one.
                if on_afk_redraw is not None:
                    try:
                        await on_afk_redraw()
                    except Exception:
                        pass
                continue
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
            # Real gap found in a security/performance audit: this used
            # to read directly via self.reader.read(1), bypassing
            # read_raw()/handle_telnet_command() -- the same telnet-IAC
            # stripping read_line()/read_key() already route through.
            # IAC (0xFF) is > b' ', so it passed the `ch < b' '` control-
            # byte filter below completely untouched -- a resize mid-
            # entry landed the ENTIRE raw IAC SB NAWS ... IAC SE sequence
            # in the password buffer verbatim. Brings read_password() up
            # to the same protection read_line()/read_key() already had,
            # and (see handle_telnet_command()'s own docstring) that
            # underlying protection was itself hardened this same audit
            # to correctly handle a sequence split across single-byte
            # reads, not just a sequence delivered whole.
            # allow_afk defaults to False here (unlike read_line()'s
            # allow_afk=True) -- deliberately unchanged behavior, this
            # fix is scoped to the IAC gap only, not adding AFK-during-
            # password-entry handling as a side effect.
            try:
                ch = await self.read_raw(1)
            except (ConnectionError, BrokenPipeError, EOFError) as e:
                raise CarrierLost(str(e)) from e
            if not ch:
                # read_raw() can legitimately return b'' when this
                # particular byte was consumed entirely as telnet
                # negotiation (e.g. the final byte of an IAC SB NAWS
                # ... IAC SE sequence) -- NOT a disconnect signal.
                # read_raw() already raises CarrierLost itself on a
                # true disconnect (checked against the raw, pre-
                # telnet-processing socket read), so any non-exception
                # return here, empty or not, means the connection is
                # still alive; just loop and read the next byte.
                continue
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
        """Poll our own NodeActivity row for a sysop kick request, and
        keep last_seen alive while we're at it.

        The /admin/control/nodespy/<slot>/kick endpoint runs in the
        anetbbs-web (gunicorn) process; terminal sessions live in
        anetbbs-telnet. They don't share memory, so the kick endpoint
        flips `kick_requested` on our DB row and we notice here.

        Real bug found live (2026-08-30): `last_seen` was ONLY bumped by
        `_heartbeat_node()`, itself only called from active menu
        navigation/door-game/MRC/AFK-transition call sites. A session
        that just sits on one screen -- reading a message, idling in a
        door that doesn't heartbeat every tick, watching MRC without
        typing -- never touches any of those call sites, so last_seen
        goes stale and the session silently drops out of every
        presence surface's 5-minute online cutoff (web NodeSpy, the
        in-BBS Node Monitor, and anetbbs-monitor) even though it's
        fully connected. This watchdog already polls our row every 5s
        regardless of what the user is doing, so it's the natural place
        to keep last_seen fresh independent of activity, instead of
        adding a second timer -- fixes the "still connected but shows
        as offline/never showed up at all" symptom at the source, for
        every consumer of NodeActivity, not just one of them.

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
                        elif row:
                            row.last_seen = datetime.utcnow()
                            try:
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

    def _start_presence_alert_watchdog(self):
        """Poll for OTHER users' login/logout events and print a
        classic "X just logged in/out" line, live, wherever this user
        currently is in the BBS -- not just inside multinode chat
        (features/multinode.py's own 'join'/'part' broadcast is a
        narrower, chat-room-only courtesy; this is the general
        cross-protocol alert most BBS software shows unconditionally).

        Polls every 5 seconds (same cadence as the kick watchdog,
        cheap enough not to matter). Starts its "already seen" marker
        at the current max PresenceEvent id so a freshly-connected
        session never replays history from before it connected.
        """
        async def _watch():
            from ..features.bbs_ui import _app
            last_id = 0
            try:
                from ..models import PresenceEvent
                with _app().app_context():
                    row = (PresenceEvent.query
                          .order_by(PresenceEvent.id.desc()).first())
                    last_id = row.id if row else 0
            except Exception:
                last_id = 0
            while True:
                try:
                    await asyncio.sleep(5)
                    from ..models import PresenceEvent
                    with _app().app_context():
                        events = (PresenceEvent.query
                                 .filter(PresenceEvent.id > last_id)
                                 .filter(PresenceEvent.user_id != self.user['id'])
                                 .order_by(PresenceEvent.id.asc())
                                 .limit(20).all())
                        # Snapshot the fields we need INSIDE the app
                        # context -- the ORM objects themselves go stale
                        # the moment this `with` block exits.
                        pending = [(e.id, e.username, e.kind) for e in events]
                    for eid, username, kind in pending:
                        last_id = max(last_id, eid)
                        verb = 'logged in' if kind == 'login' else 'logged out'
                        color = '1;32' if kind == 'login' else '1;33'
                        try:
                            await self.write(
                                f"\r\n\x1b[{color}m*** {username} just "
                                f"{verb} ***\x1b[0m\r\n")
                        except Exception:
                            pass
                except asyncio.CancelledError:
                    return
                except Exception:
                    # Don't let a transient DB error kill the watchdog.
                    continue
        self._presence_alert_task = asyncio.create_task(_watch())

    async def _collect_security_questions(self):
        """Collect 3 password-recovery security Q&A during terminal registration."""
        if not self.user:
            return
        from ..models import SECURITY_QUESTIONS

        wrap_width = max(20, _prompt_width(self))
        header = '\r\n'.join(_wrap_text_lines(
            '=== Password Recovery Security Questions ===', wrap_width))
        intro = '\r\n'.join(_wrap_text_lines(
            'These are used if you ever need to reset your password.', wrap_width))
        await self.write(f"\r\n\x1b[1;36m{header}\x1b[0m\r\n{intro}\r\n\r\n")
        indent = ' ' * 6  # matches the visible width of "  NN. " below
        wrap_width = max(20, wrap_width - len(indent))
        for i, q in enumerate(SECURITY_QUESTIONS, 1):
            wrapped = _wrap_text_lines(q, wrap_width)
            await self.write(f"  \x1b[33m{i:2}.\x1b[0m {wrapped[0]}\r\n")
            for cont in wrapped[1:]:
                await self.write(f"{indent}{cont}\r\n")

        qa_pairs = []
        used = set()
        for slot in range(1, 4):
            while True:
                q_prefix = f"Question {slot} of 3"
                q_suffix = f" — enter a number (1-{len(SECURITY_QUESTIONS)}): "
                q_wrapped = _wrap_text_lines(q_prefix + q_suffix, _prompt_width(self))
                # Bold only the "Question N of 3" portion -- always the
                # start of the first wrapped line, since word-wrap never
                # reorders characters, only breaks between them.
                q_wrapped[0] = f"\x1b[1m{q_prefix}\x1b[0m" + q_wrapped[0][len(q_prefix):]
                await self.write("\r\n" + "\r\n".join(q_wrapped))
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
                wrapped_question = '\r\n'.join(
                    _wrap_text_lines(question, _prompt_width(self)))
                answer = await self.read_line(f"{wrapped_question}\r\nYour answer: ")
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
            wrapped_prompt = '\r\n'.join(
                _wrap_text_lines(f'{prompt}{tag}: ', _prompt_width(self)))
            while True:
                ans = await self.read_line(wrapped_prompt)
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

    def _log_activity(self, activity_type, details=None):
        """Write a UserActivity row for this session, correlated to its
        CallerLog row (self._caller_log_id, set at login above) so the
        admin caller-log drill-down can show a full per-session timeline
        -- the terminal-side counterpart to web/auth.py's _log_activity().
        Best-effort, same as _heartbeat_node()/_close_node_activity()
        above."""
        if not getattr(self, 'user', None):
            return
        try:
            from ..features.bbs_ui import _app
            from .activity_log import log_activity
            peer_ip = ''
            try:
                addr = self.writer.get_extra_info('peername')
                if addr:
                    peer_ip = addr[0] if isinstance(addr, tuple) else str(addr)
            except Exception:
                pass
            with _app().app_context():
                log_activity(
                    self.user['id'], activity_type, details,
                    ip_address=peer_ip,
                    service=getattr(self, '_proto', None),
                    caller_log_id=getattr(self, '_caller_log_id', None))
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

    async def _play_screen_animation(self, marker):
        """Play one '@ANIMSTART@<frame>@FRAME@<frame>@...@ANIMEND@' block
        (see _SCREEN_ANIM_SPLIT_RE) found embedded in an ANSI screen body.

        Each frame is exactly N '\\r\\n'-terminated lines, N inferred from
        the first frame — the caller (whoever authored the screen) is
        responsible for making every frame the same line count. Playing a
        frame is just: cursor up N lines, reprint those N lines. Because
        every line in a frame ends in its own '\\r\\n', reprinting always
        re-descends the cursor by exactly N, so it lands back at the exact
        column/row it started from regardless of terminal width — no
        column tracking needed. This only stays correct if nothing else
        was printed between the original N lines and this marker (true by
        construction: the marker sits immediately after them in the file).
        Best-effort — a write/encode failure just ends the animation early
        and leaves whatever was last drawn on screen, same failure
        philosophy as the rest of this method's caller.
        """
        inner = marker[len('@ANIMSTART@'):-len('@ANIMEND@')]
        frames = [f for f in inner.split('@FRAME@') if f]
        if not frames:
            return
        rows = frames[0].count('\r\n')
        if rows <= 0:
            return
        up = f'\x1b[{rows}A'.encode('latin-1')
        try:
            for frame in frames:
                self.writer.write(up)
                self.writer.write(frame.encode('latin-1'))
                await self.writer.drain()
                await asyncio.sleep(_ANIM_FRAME_DELAY)
        except Exception:
            pass

    async def _show_ansi_screen(self, slot, *, force_pause=False):
        """Render a sysop-defined ANSI screen by slot name. Best-effort —
        silently no-ops if the slot doesn't exist or the DB is unavailable.

        Resolution order:
          1. {DATA_DIR}/mods/text/{slot}.ans — file override (highest
             priority), matching real Synchronet's own mods/text/
             convention (wiki.synchro.net/dir:mods: "if a file exists
             in both the [text] directory and the mods/text directory,
             the version in the mods/text directory will be displayed
             rather than the version in the text directory").
          2. {DATA_DIR}/text/{slot}.ans  — file drop-in (older,
             still-supported location predating the mods/ tree)
          3. BbsAnsiScreen DB row        — set via /admin/menus/screens
          4. nothing                     — silently skip

        Multiple display variants: dropping in {slot}132_2.ans,
        {slot}132_3.ans, ... alongside {slot}132.ans (which counts as
        variant #1) shows ALL of them together, in order, every login —
        the classic Synchronet logon1.ans/logon2.ans/... multi-screen
        convention (each variant is responsible for its own @PAUSE@ if a
        pause is wanted before the next one loads). Name them
        {slot}132_ran.ans / {slot}132_2_ran.ans / ... instead to pick just
        ONE at random each login rather than showing the whole sequence.
        See _resolve_display_screens. Works for the file-based
        overrides (steps 1-2) and the bundled stock screen (step 4); a
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

            # 1-2. File-based overrides (mode-aware priority), mods/text/
            #      checked before the older text/ location for each
            #      candidate filename in turn:
            #    wide : {slot}132.ans → {slot}.ans
            #    ansi : {slot}.ans
            #    ascii: {slot}.asc
            db_pause = False
            body = None
            _text_dir = None
            try:
                _data_dir = _app().config.get('DATA_DIR', '')
                _mods_text_dir = _pl.Path(_data_dir) / 'mods' / 'text'
                _text_dir = _pl.Path(_data_dir) / 'text'
                if mode == 'wide':
                    _candidates = [f'{slot}132.ans', f'{slot}.ans']
                elif mode == 'ascii':
                    _candidates = [f'{slot}.asc']
                else:
                    _candidates = [f'{slot}.ans']
                for _fname in _candidates:
                    _picked = (_resolve_display_screens(_mods_text_dir, _fname)
                              or _resolve_display_screens(_text_dir, _fname))
                    if _picked is not None:
                        body = _load_display_screens(_picked)
                        is_plain_text = _fname.endswith('.asc')
                        break
            except Exception:
                pass

            # 3. DB lookup (ANSI content — skip for ascii mode)
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

            # 4. Bundled stock screen (anetbbs/screens/) — all modes
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

            # Split on @PAUSE@ (in-content pagination) and @ANIMSTART@...
            # @ANIMEND@ (inline in-place animation, see _SCREEN_ANIM_SPLIT_RE
            # above) — both survive _apply_codes as literal markers the same
            # way, since unknown @-codes are left in place, not stripped.
            _PAUSE_PROMPT = (
                b'\r\n[Press any key to continue]' if is_plain_text
                else b'\r\n\x1b[33m[Press any key to continue]\x1b[0m'
            )
            segments = _SCREEN_ANIM_SPLIT_RE.split(body)
            parts = segments[0::2]
            markers = segments[1::2]

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
                    marker = markers[i]
                    if marker == '@PAUSE@':
                        self.writer.write(_PAUSE_PROMPT)
                        await self.writer.drain()
                        await self.read_key('')
                    else:
                        await self._play_screen_animation(marker)

            if force_pause or db_pause:
                self.writer.write(_PAUSE_PROMPT)
                await self.writer.drain()
                await self.read_key('')
        except Exception:
            pass

    async def _login_lightbar_menu(self, bbs_name: str) -> str:
        """Interactive ANSI lightbar replacement for the old static
        numbered login box -- Up/Down (or Left/Right) moves the
        highlight, Enter selects. Direct 1/2/3 and L/N/E hotkeys still
        jump straight to a choice too, so muscle memory from the old
        box (and any client with broken arrow-key support) still works.

        Returns '1' (Login), '2' (New User Registration), or '3' (Exit)
        -- the same contract login_screen()'s dispatch already expects
        from the ascii/petscii branches' read_line()-based menus, so
        the caller doesn't need to know which branch produced it.
        """
        items = (('1', 'L', 'Login'),
                 ('2', 'N', 'New User Registration'),
                 ('3', 'E', 'Exit'))
        sel = 0
        IW = 40  # inner content width -- matches the box border below
        B = '\x1b[1m'; R = '\x1b[0m'
        CY = '\x1b[36m'; YE = '\x1b[33m'; GR = '\x1b[32m'; WH = '\x1b[37m'
        SEL = '\x1b[7m\x1b[1m'   # reverse + bold -- same highlight convention as the BBS's other lightbar menus (bbs_ui.py's _rss_lightbar)
        EOL = '\x1b[K'
        # NOT the raw CP437 control-picture bytes (0x10/0x18/0x19) --
        # those only render as arrow/triangle glyphs on a real legacy
        # DOS-style terminal emulator that does control-range glyph
        # substitution (SyncTerm, etc.); a modern/web ANSI client can
        # just as reasonably treat 0x00-0x1F as literal control codes
        # and print a Unicode "control picture" placeholder instead --
        # confirmed live (Pi test): showed up as ␐/␘/␙, not arrows.
        # 0xAF ('»') is in cp437's high half (a real printable
        # character, not a control-code substitution trick) and
        # renders identically everywhere; the hint text just spells
        # out "Up/Down" instead of risking another control-byte glyph.
        CURSOR = '»'
        ITEM_ROW = {0: 6, 1: 7, 2: 8}   # absolute row numbers, set by _draw_full()'s own layout below

        def _at(row):
            return f'\x1b[{row};1H'

        def _item_row(idx):
            _, _, label = items[idx]
            marker = CURSOR if idx == sel else ' '
            text = f'  {marker} {label}'.ljust(IW)
            if idx == sel:
                return f'{B}{CY}║{SEL}{text}{R}{B}{CY}║{R}{EOL}'
            return f'{B}{CY}║{R}{GR}{text}{B}{CY}║{R}{EOL}'

        async def _draw_full():
            title = f'Welcome to {bbs_name}'[:IW - 2].center(IW - 2)
            # Must be <= IW - 1 visible chars (the row template below adds
            # one leading literal space before it) -- found live: the
            # earlier "Up/Down" wording was exactly IW chars, one over
            # budget, which overflowed the box border by a column.
            hint_plain = 'Up/Dn Move   Enter Select   1-3 Direct'
            hint = f'{YE}Up/Dn{R} Move   {YE}Enter{R} Select   {YE}1-3{R} Direct'
            hint += ' ' * max(0, IW - 1 - len(hint_plain))
            lines = [
                '',
                f'{B}{CY}╔{"═" * IW}╗{R}',
                f'{B}{CY}║{R} {WH}{title}{R} {B}{CY}║{R}',
                f'{B}{CY}╠{"═" * IW}╣{R}',
                f'{B}{CY}║{R}{" " * IW}{B}{CY}║{R}',
                _item_row(0),
                _item_row(1),
                _item_row(2),
                f'{B}{CY}║{R}{" " * IW}{B}{CY}║{R}',
                f'{B}{CY}╠{"═" * IW}╣{R}',
                f'{B}{CY}║{R} {hint}{B}{CY}║{R}',
                f'{B}{CY}╚{"═" * IW}╝{R}',
                '',
            ]
            await self.write('\r\n'.join(lines) + '\r\n')

        async def _redraw_item(idx):
            await self.write(f'{_at(ITEM_ROW[idx])}{_item_row(idx)}')

        await _draw_full()

        result = None
        while result is None:
            key = await self.read_key_arrow()
            if key in ('UP', 'LEFT'):
                old, sel = sel, (sel - 1) % len(items)
                await _redraw_item(old)
                await _redraw_item(sel)
            elif key in ('DOWN', 'RIGHT'):
                old, sel = sel, (sel + 1) % len(items)
                await _redraw_item(old)
                await _redraw_item(sel)
            elif key == 'ENTER':
                result = items[sel][0]
            elif key in ('1', '2', '3'):
                result = key
            elif key == 'L':
                result = '1'
            elif key == 'N':
                result = '2'
            elif key in ('E', 'ESC', 'CTRL_C'):
                result = '3'

        # The caller (handle_login()/handle_registration()) writes its
        # own header from wherever the cursor happens to be -- the old
        # static read_line()-based menu naturally left it at a fresh
        # line below the box, but this menu's absolute cursor addressing
        # leaves it wherever the last redraw put it (mid-box). Without
        # this, the next screen's text overlaps the still-visible box
        # instead of appearing on a clean screen (confirmed live: "===
        # User Login ===" bled into the "New User Registration"/"Exit"
        # rows on a real Pi test).
        await self.write('\x1b[2J\x1b[H')
        return result

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
                choice = await self.read_line(menu)
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
                choice = await self.read_line(menu)
            else:
                # Interactive Up/Down + Enter lightbar, replacing the old
                # static numbered box -- direct 1/2/3 (and L/N/E) hotkeys
                # still work too, so the muscle-memory path never breaks.
                # A sysop can drop a full replacement at
                # data/mods/core/login_menu.py (defining an async
                # render_login_menu(session, bbs_name) -> str) to
                # override this without touching core code at all --
                # same idea as Synchronet's own login.js/logon.js in
                # mods/. See core/mods_override.py.
                from .mods_override import call_core_override
                choice = await call_core_override(
                    'login_menu', 'render_login_menu',
                    lambda: self._login_lightbar_menu(_bbs_name),
                    self, _bbs_name)

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
            # _show_ansi_screen has no PETSCII-aware rendering -- it writes
            # raw CP437/ANSI bytes directly to the socket, bypassing
            # write()'s petscii branch -- same limitation already guarded
            # against for the 'welcome' and 'goodbye' slots (see
            # login_screen() and the disconnect path below); this call
            # was simply missed. "Registration successful!" above already
            # went through write() correctly, so petscii users still get
            # a real confirmation, just not the sysop's customizable
            # newuser.ans banner (same tradeoff already accepted for
            # 'welcome'/'goodbye').
            if self.term_mode != 'petscii':
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
        """Handle incoming telnet commands.

        Real gap found in a security/performance audit: this used to be
        a pure per-call function with no memory between calls -- but its
        real caller, read_raw() (used by read_line()/read_key()/
        read_password() for one keystroke at a time), always hands it
        exactly 1 byte per call in practice: confirmed empirically that
        asyncio.StreamReader.read(1) never returns more than 1 byte even
        when more is already buffered on the socket. A multi-byte IAC
        sequence -- e.g. IAC SB NAWS <4 payload bytes> IAC SE, sent
        unprompted by most real telnet/SSH clients the instant a user
        resizes their terminal window, an entirely ordinary action --
        could therefore never be recognized as a single unit: only the
        bare leading IAC byte matched `byte == IAC` and got silently
        dropped; every OTHER byte of that same sequence (SB, the option
        byte, non-control payload bytes, the trailing SE) arrived in a
        FRESH call with no idea a sequence was already in progress, so
        each fell through as if it were ordinary typed input -- landing
        in read_line()/read_key()/read_password()'s buffers as literal
        garbage characters (e.g. a NAWS payload byte of 0x50 is the
        printable 'P'). self.telnet_command_buffer (declared in
        __init__, previously unused) now carries an in-progress, not-
        yet-complete sequence's raw bytes across calls; prepending it
        here and re-parsing that combined buffer from the start
        correctly resumes exactly where the previous call left off,
        regardless of how the sequence happens to be split across
        reads.
        """
        data = bytes(self.telnet_command_buffer) + bytes(data)
        self.telnet_command_buffer = bytearray()
        if not data:
            return b''

        result = bytearray()
        i = 0
        while i < len(data):
            byte = data[i:i+1]
            if byte == IAC:
                if i + 1 >= len(data):
                    self.telnet_command_buffer = bytearray(data[i:])
                    break
                command = data[i+1:i+2]

                if command in [WILL, WONT, DO, DONT]:
                    if i + 2 >= len(data):
                        self.telnet_command_buffer = bytearray(data[i:])
                        break
                    option = data[i+2:i+3]
                    i += 3
                    continue

                elif command == SB:
                    sb_start = i
                    i += 2  # past IAC SB
                    if i >= len(data):
                        self.telnet_command_buffer = bytearray(data[sb_start:])
                        break
                    option = data[i]
                    i += 1
                    # Collect payload bytes until IAC SE
                    payload = bytearray()
                    found_se = False
                    while i < len(data):
                        if data[i:i+1] == IAC and i + 1 < len(data) and data[i+1:i+2] == SE:
                            i += 2
                            found_se = True
                            break
                        payload.append(data[i])
                        i += 1
                    if not found_se:
                        # Ran out of data mid-payload -- resume from the
                        # very start of this SB sequence next time (the
                        # full IAC SB <option> prefix, not just the
                        # unconsumed payload tail), since a freshly
                        # re-entered outer loop needs that prefix to
                        # know it's still inside a subnegotiation rather
                        # than looking at ordinary bytes.
                        self.telnet_command_buffer = bytearray(data[sb_start:])
                        break
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

    async def read_raw(self, n=1, allow_afk=False):
        """Read raw bytes from the connection. Honors `self.idle_timeout`
        if set — disconnects sessions that sit idle waiting for a keystroke.
        IRC/MRC are web features and don't go through this path, so the
        idle timer doesn't affect them.

        allow_afk: opt-in for the AFK warning/screensaver (see
        _read_byte_maybe_spinning's own docstring for why this defaults
        to False here -- most callers of read_raw() are door games/
        bridges with their own poll loops that would misinterpret an
        AFK interruption; only read_line() passes True). May raise
        _AFKInterrupted (anetbbs.core.session) when True and the
        caller's own idle time crossed AFK_WARNING_SECONDS and the
        screensaver was then dismissed by a keystroke -- callers that
        pass allow_afk=True must be prepared to catch it.

        Raises CarrierLost when the transport disconnects or the idle
        timeout fires, so menu loops break out instead of spinning on
        a permanently-EOF stream."""
        try:
            timeout = getattr(self, 'idle_timeout', 0) or 0
            data = await self._read_byte_maybe_spinning(
                n, overall_timeout=timeout if timeout > 0 else None,
                allow_afk=allow_afk)
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
                    # ansi_to_petscii() translates ANSI color codes into
                    # real C64 color bytes rather than discarding them;
                    # non-color CSI (cursor moves etc.) is still dropped.
                    from ..features import petscii_codec
                    text = petscii_codec.encode(petscii_codec.ansi_to_petscii(text))
                else:
                    if self.term_mode == 'ascii':
                        text = _ANSI_ESC_RE.sub('', text)
                    text = text.encode(self.encoding, errors='replace')
            elif self.term_mode == 'petscii':
                from ..features import petscii_codec
                text = petscii_codec.encode(petscii_codec.ansi_to_petscii(text.decode('latin-1')))
            elif self.term_mode == 'ascii':
                text = _ANSI_ESC_RE_B.sub(b'', text)
            self.writer.write(text)
            await self.writer.drain()
        except (BrokenPipeError, ConnectionResetError,
                ConnectionAbortedError):
            pass
        except Exception as e:
            logger.debug("write failed: %s", e)

    async def read_line(self, prompt: str = "", max_len: int = 2048,
                        on_afk_redraw=None) -> str:
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

        on_afk_redraw: see read_key()'s own docstring for the full
        reasoning -- same optional full-screen-redraw hook for callers
        that own more visible state than just this prompt + the
        already-typed line (which gets redrawn regardless, see below).
        """
        if prompt:
            await self.write(prompt)

        line = bytearray()
        while True:
            try:
                char = await self.read_raw(1, allow_afk=True)
                if not char:
                    # read_raw() can legitimately return b'' when this
                    # byte was consumed entirely as telnet negotiation
                    # (see read_password()'s identical comment for the
                    # full reasoning) -- read_raw() itself already
                    # raises CarrierLost on a true disconnect, so just
                    # loop and read the next byte rather than treating
                    # an empty non-exception return as disconnection.
                    continue

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

            except _AFKInterrupted:
                # Same signal/reasoning as read_key's own handling --
                # the dismissing keystroke is consumed, not added to
                # the line. Prefer the caller's own full redraw if given
                # one; otherwise fall back to the prompt + whatever was
                # already typed (unlike read_key, this call owns a real
                # partial buffer that the screensaver's screen-clear
                # wiped visually, even though `line` itself was never
                # touched) so an AFK interruption mid-line doesn't look
                # like the user's typing vanished.
                if on_afk_redraw is not None:
                    try:
                        await on_afk_redraw()
                    except Exception:
                        pass
                # Always redraw the prompt + already-typed buffer too --
                # on_afk_redraw() (if given) only knows about whatever
                # static screen content the caller owns, never about
                # THIS call's own in-progress partial line.
                if prompt:
                    await self.write(prompt)
                if line:
                    try:
                        await self.write(bytes(line).decode(self.encoding))
                    except Exception:
                        pass
                continue
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
            #
            # Real vulnerability found in a security audit: title/body
            # can originate from a REMOTE FTN/QWK peer (e.g.
            # notify_reply.py builds title from msg.from_name, taken
            # verbatim from an inbound netmail/echomail packet) -- no
            # local account required to reach this. strip_untrusted_
            # escapes() (see anetbbs/core/text_safety.py) neutralizes
            # any ANSI/control-byte injection before it ever reaches
            # this user's real terminal.
            from .text_safety import strip_untrusted_escapes
            for n in unread_notifs:
                title = strip_untrusted_escapes(n.title)
                line = f'  \x1b[33m-\x1b[0m {title}'
                if n.body:
                    line += f' \x1b[36m({strip_untrusted_escapes(n.body)})\x1b[0m'
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
            from .text_safety import strip_untrusted_escapes
            for b in reversed(recents):  # oldest first
                ts = fmt_eastern(b.created_at, '%m/%d %H:%M')
                sender = b.sender.username if b.sender else 'sysop'
                await self.write(
                    f'\x1b[33m[{ts}] {strip_untrusted_escapes(sender)}:\x1b[0m '
                    f'{strip_untrusted_escapes(b.text)}\r\n')
            await self.write('\r\n')
        except Exception:
            # Never block login on broadcast surfacing failure.
            pass

    async def _launch_direct_door(self, slug: str) -> bool:
        """Resolve `slug` (from an rlogin xtrn= handshake, see
        rlogin_server.py / self._direct_door_slug) to a Game row and
        launch it via the same GameManager._launch() path the Game
        Center menu uses. Returns True once a game was actually found
        and handed off to _launch() -- regardless of how play itself
        goes, since _launch() already catches and reports its own
        errors -- so the caller knows to end the session afterward
        rather than fall through to the normal main menu. Returns
        False for an unknown/inactive/inaccessible slug so the caller
        can fall back to a normal interactive session instead of
        stranding an otherwise-validly-authenticated user with no way
        in -- ANetBBS is a full BBS, not just a game server, unlike
        the Synchronet game-server target this convention mirrors."""
        try:
            from ..features.bbs_ui import _app
            from ..models import Game
            with _app().app_context():
                g = Game.query.filter_by(slug=slug, is_active=True).first()
                if g is None:
                    await self.write(
                        f"\r\n\x1b[1;31mUnknown game code '{slug}'.\x1b[0m\r\n")
                    return False
                # Same default fallback GameManager.show_door_menu() uses
                # for a user with no access_level set, for consistency.
                user_access = (self.user or {}).get('access_level') or 10
                if (g.min_access_level or 0) > user_access:
                    await self.write(
                        "\r\n\x1b[1;31mYou don't have access to that "
                        "game.\x1b[0m\r\n")
                    return False
                game_dict = {
                    'id': g.id, 'name': g.name, 'game_type': g.game_type,
                    'web_game_module': g.web_game_module or '',
                }
        except Exception as exc:
            logger.error('Direct door lookup failed for slug=%r: %s', slug, exc)
            try:
                await self.write(
                    "\r\n\x1b[1;31mCould not launch that game.\x1b[0m\r\n")
            except Exception:
                pass
            return False

        await self.games._launch(game_dict)
        return True

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
        # AFK warning + matrix-rain screensaver before the idle timeout
        # above actually disconnects (Jerry's ask, mirroring a real
        # Mystic Pascal AFK script: warn with a countdown, then a
        # screensaver, then -- only if idle_timeout is also set and
        # nobody ever comes back -- the existing disconnect happens as
        # normal). Same off-by-default/opt-in/bullet-proofing pattern
        # as idle_timeout above; read directly from the environment
        # rather than adding a config.py Config attribute, matching
        # what idle_timeout's OWN runtime code path actually does (its
        # config.py declaration is a separate, unused-by-this-code
        # duplicate -- not a pattern worth repeating for a new setting).
        try:
            raw = os.environ.get('AFK_WARNING_SECONDS', '').strip()
            self.afk_warning_seconds = int(raw) if raw else 0
            if self.afk_warning_seconds < 0:
                self.afk_warning_seconds = 0
        except Exception:
            self.afk_warning_seconds = 0
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

            await self._maybe_send_steady_cursor()

            # Peer address, computed once and reused below for both the
            # multinode roster and UserSession presence -- real bug found
            # live (2026-08-28): this used to be computed AFTER
            # acquire_slot() below, which was called with
            # str(self._session_started_at) (a timestamp) in the "peer"
            # argument's place instead, since the real address wasn't
            # available yet at that point. That timestamp then propagated
            # into NodeActivity.peer via _open_node_activity() below,
            # showing e.g. "2026-08-28 10:44" in the Peer column of every
            # NodeSpy-style view (web panel, in-BBS Node Monitor,
            # anetbbs-monitor) instead of an IP:port.
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

            # Multinode slot acquisition — claim a node 1..BBS_NODES so
            # the user shows up on the multinode roster. If all nodes are
            # in use we politely turn the user away.
            #
            # Real gap found in a security/performance audit: this used
            # to be ONE try/except wrapping acquire_slot() AND
            # everything after it (broadcast, _open_node_activity) --
            # `except Exception: self._node_entry = None` at the bottom
            # meant an exception raised by broadcast() or
            # _open_node_activity() (both best-effort, not expected to
            # ever actually raise, but not guaranteed not to) would
            # CLOBBER an already-successfully-acquired self._node_entry
            # back to None. start()'s own finally: block only releases
            # the multinode slot `if self._node_entry is not None`, so
            # that reset permanently leaked the slot until process
            # restart (multinode.py's _NODES dict has no independent
            # stale-session backstop the way node_manager.py's _active
            # does). Narrowed to wrap ONLY the actual acquire_slot()
            # call -- the only step that can legitimately mean "we never
            # got a slot at all" -- so nothing after a successful
            # acquisition can un-acquire it.
            # Computed before the try: so it's always defined for the
            # "all nodes in use" message below even if the import or
            # acquire_slot() itself raises before reaching that point.
            max_nodes = max(1, min(100, int(os.environ.get('BBS_NODES', '8'))))
            try:
                from ..features.multinode import acquire_slot, broadcast
                self._node_entry = acquire_slot(self.user, proto, peer,
                                                max_nodes,
                                                session=self)
            except Exception:
                self._node_entry = None
            if self._node_entry is None:
                await self.write(
                    f"\r\n\x1b[1;31mAll {max_nodes} nodes are "
                    f"in use — try again later.\x1b[0m\r\n")
                return
            try:
                broadcast(self.user.get('username', '?'),
                          f'logged in on node {self._node_entry.slot} '
                          f'({proto})', kind='join')
            except Exception:
                pass
            # Open the NodeActivity row for sysop NodeSpy and start
            # the cross-process kick watchdog.
            try:
                self._open_node_activity(proto)
            except Exception:
                pass
            try:
                self._start_kick_watchdog()
            except Exception:
                pass
            try:
                self._start_presence_alert_watchdog()
            except Exception:
                pass

            # Real gap found in a security/performance audit:
            # _enforce_time_budget() -- the per-session/per-day
            # UserTimeBudget cutoff sysops can configure in
            # /admin/users/<id> -- was fully implemented but never
            # actually called anywhere, so a configured limit had zero
            # effect on a real session no matter what a sysop set it to.
            try:
                await self._enforce_time_budget()
            except Exception:
                pass

            # Cross-protocol presence: register this user in the SAME UserSession
            # table the web uses, so the web's "online" widget shows telnet/SSH/
            # rlogin users alongside web users. peer/proto already computed
            # above for the multinode slot -- reused here rather than
            # recomputed, so there's exactly one place that derives them.
            from .presence import SessionPresence
            presence = SessionPresence(self.user['id'], protocol=proto, peer=peer,
                                       username=self.user.get('username', ''))
            # Stored on self so menu_engine.py/games.py/mrc_chat.py can
            # update it after the initial set_page('main') below -- see
            # menu_engine.py's dispatch point for why this was needed.
            self.presence = presence

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
                    # Stashed for the activity-log drill-down (correlates
                    # every UserActivity event this session logs back to
                    # this CallerLog row) and for writing duration_seconds
                    # on the way out, in the finally: block below.
                    self._caller_log_id = _cl.id
                    self._proto = proto
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

            # Direct door launch -- rlogin game-server-style connection
            # (xtrn=<slug> in the handshake's terminal/speed field; see
            # rlogin_server.py and BBSSession.__init__'s direct_door_slug
            # docstring). Skips logon modules and the main menu entirely
            # and ends the session (hanging up the connection) once the
            # game exits -- matching Jerry's own Synchronet game server's
            # behavior for the identical convention, and the exact
            # convention ANetBBS's own outbound rlogin_bridge.py already
            # speaks when connecting OUT to other game servers.
            if self._direct_door_slug:
                if await self._launch_direct_door(self._direct_door_slug):
                    return
                # Unknown/inactive/inaccessible slug -- _launch_direct_door
                # already showed the user why, fall through to a normal
                # interactive session rather than stranding an otherwise-
                # validly-authenticated user with no way in.

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
            # Fix for a real bug found live: CallerLog.duration_seconds
            # was declared on the model and shown in two admin templates
            # but never written anywhere for ANY protocol -- every row
            # showed 0s. Covers every exit path uniformly since this is
            # the same finally: block presence.disconnect() already runs
            # from (explicit logoff, idle timeout, dropped carrier,
            # unhandled exception).
            if getattr(self, '_caller_log_id', None):
                try:
                    from ..models import CallerLog, db as _db
                    from ..features.bbs_ui import _app
                    with _app().app_context():
                        _cl = CallerLog.query.get(self._caller_log_id)
                        if _cl is not None:
                            _cl.duration_seconds = int(
                                (datetime.utcnow() - self._session_started_at)
                                .total_seconds())
                            _db.session.commit()
                except Exception:
                    pass
                self._log_activity('logout')
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
            # Real gap found in a security/performance audit
            # (2026-08-31): _start_presence_alert_watchdog()'s own task
            # (self._presence_alert_task) had NO cancellation anywhere
            # in this file -- unlike _hb_task/_kick_task right above it
            # and _budget_task below, which each got this exact fix
            # already for the same reason. Every session that reaches
            # a successful login starts this watchdog, so this was a
            # guaranteed, 100%-reproduction task leak on every single
            # session: its `while True: await asyncio.sleep(5); ...`
            # closure keeps the whole BBSSession object graph (reader,
            # writer, user dict) alive forever after logoff, the same
            # unbounded per-connection accumulation shape as the
            # v1.0.21 incident.
            try:
                pat = getattr(self, '_presence_alert_task', None)
                if pat and not pat.done():
                    pat.cancel()
            except Exception:
                pass
            # Real gap found in a security/performance audit: unlike
            # _hb_task/_kick_task right above, _enforce_time_budget()'s
            # own watchdog task (self._budget_task, only created when a
            # sysop has configured a UserTimeBudget for this user) was
            # never cancelled anywhere -- a normal logout left it alive,
            # sleeping for however much of the budget window remained,
            # holding a live reference to this now-dead session
            # (including its reader/writer) until it finally woke up
            # and called self.writer.close() on an already-torn-down
            # connection. Same orphaned-background-task leak class as
            # the v1.0.21 incident, just via asyncio tasks instead of a
            # cached object.
            try:
                bt = getattr(self, '_budget_task', None)
                if bt and not bt.done():
                    bt.cancel()
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
