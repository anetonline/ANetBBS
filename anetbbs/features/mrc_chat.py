"""
Telnet/SSH/rlogin MRC chat — terminal client for the ANetBBS MRC bridge.

Architecture: connects to the LOCAL mrc/bridge/ via WebSocket (JSON).
The bridge holds one TCP+trust connection to the upstream hub and fans every
message to all attached clients (web + terminal). Reusing the bridge means:
  - one BBS identity on the hub (trust state persists)
  - web and terminal users on this BBS share rooms
  - no reconnect storms when terminal users join/leave

Bridge JSON API:
  inbound  → {"type": "join_room"|"send_message"|"direct_message"|
                       "server_cmd"|"leave_room"|"ping", ...}
              join_room's "ip" field carries this caller's real remote
              address (see _caller_ip()) -- the bridge has no way to
              observe it itself for this client, since the WebSocket
              connection to the bridge always originates from
              localhost. Needed so the bridge can forward the
              reference client's own USERIP: packet on join; omitting
              it silently means the hub never learns which real caller
              a given handle is connecting from.
  outbound → {"type": "chat"|"system"|"private"|"action"|"error"|...,
               "user": ..., "bbs": ..., "room": ..., "body": ...}

Display layout (80×24 default):
  row 1         : status bar  [#room]  topic  · PAUSED+N · mentions · latency
  rows 2..N-1   : chat area (direct-draw, cleared+redrawn from _display_lines deque)
  row N         : input line  "> ..."  (only redrawn by _draw_input_line; never
                  touched by _redraw_chat_area so SSH client echo is preserved)

Scrollback:
  _scroll_offset > 0  = paused; _emit increments offset so the view stays locked.
  Up/Down arrows scroll 1 line; /scroll [n] scrolls n lines; /scroll 0 = live view.
  Sending a message snaps view to live so the user sees their own message.
"""
import asyncio
import json
import logging
import re
import time
from collections import deque
from datetime import datetime, timedelta

from .base_chat import BaseChatSystem
from ..core.protocols import SessionProtocol


MAX_OUTGOING_CHARS = 140        # MRC hub hard limit
# Literal prefix the bridge's _normalize_server_cmd/_handle_server_cmd
# path sends verbatim as Field7 for a broadcast ("BROADCAST {text}") --
# fixed, not session-dependent, unlike handle/dm/action overhead which
# the bridge computes and pushes to the client (see _chat_wire_cap etc.
# below). Must match mrc/bridge/main.py's own "BROADCAST " literal.
_BROADCAST_PREFIX = 'BROADCAST '
SCROLLBACK_LINES   = 500        # local ring buffer
PING_INTERVAL      = 60         # seconds between WS pings
AWAY_AFTER         = 600        # seconds idle before IAMHERE AWAY
TICKER_TICK_SECONDS = 1.5       # ticker redraw/advance interval
# Real bug found live: a message needing 2+ wire chunks (a decorated
# display handle's prefix/suffix overhead can eat enough of the
# 140-char budget to force a split well under what looks like a "long"
# message) got sent with zero delay between chunks -- the bridge's
# default per-connection rate limiter (message_rate_seconds=0.5s)
# always rejected the 2nd+ chunk with "Rate limit: please slow down.",
# since two sends 0ms apart can never clear a 0.5s minimum gap. This
# is intentionally a bit above the bridge's own default so a normal,
# unconfigured install never hits this from its own splitting.
WIRE_CHUNK_DELAY = 0.6

# Static fallback content for the scrolling ticker (see _ticker_items) --
# always in rotation alongside any live hub-pushed BANNER:/STATS: text,
# so the ticker is never empty on a quiet install with no live banners.
TICKER_TIPS = (
    'Tip: /helpserver shows the full command list.',
    'Tip: Tab-complete a nick while typing.',
    'Tip: /mentions shows who mentioned you.',
    'Tip: [<] [>] arrows cycle your outgoing text color.',
    'Tip: /r replies to the last direct message you received.',
)

logger = logging.getLogger(__name__)

# ── ANSI helpers ────────────────────────────────────────────────────────────

# Also matches a bare BEL (\x07, prepended ahead of mention/DM alerts --
# see _handle_event) -- zero-width on a real terminal (it just rings the
# bell) but was being counted as one visible column by _visible_len,
# throwing the sidebar/status-bar/ticker border one column out of
# alignment on exactly the rows that happened to bell. Reported live on
# the Pi as one specific row still misaligned after the other border
# fixes, on a message that happened to mention the caller's own handle.
_ANSI_SEQ_RE = re.compile(r'\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[78])|\x07')

# Pipe-color → ANSI SGR (Synchronet/MRC |NN convention)
_PIPE_COLORS = {
    '00': '30', '01': '34', '02': '32', '03': '36',
    '04': '31', '05': '35', '06': '33', '07': '37',
    # Bright (08-15) are bold + base color -- bare aixterm 90-97 isn't
    # recognized by MagiTerm/NetRunner/PuTTY (silently no color shown);
    # SyncTerm supports both, which is why this only showed up there.
    '08': '1;30', '09': '1;34', '10': '1;32', '11': '1;36',
    '12': '1;31', '13': '1;35', '14': '1;33', '15': '1;37',
}
_PIPE_RE = re.compile(r'\|(\d{2})')

# ← → arrow keys cycle through these pipe codes for outgoing text color
_COLOR_SEQ = ['07','15','14','13','12','11','10','09',
              '08','06','05','04','03','02','01','00']

# /set palette -- a light, cosmetic-only recolor of the chrome elements
# that are drawn from one centralized place each (status-bar room tag +
# clock, sidebar header/nicks, ticker line), mirroring the scope of the
# web client's 5 CSS themes (mrc/index.html's theme-* classes) without
# attempting their broader page-wide effect -- there's no CSS cascade in
# a terminal, so recoloring every individual hardcoded escape sequence
# scattered across chat-message rendering (CTCP formatting, /set output,
# per-message-type colors, etc.) would be a much larger, riskier rewrite
# than what "light picker" was scoped for. 'default' preserves this
# file's original hardcoded colors byte-for-byte.
_TERM_PALETTES = {
    'default': {'accent': '36', 'accent_b': '1;96', 'dim': '2;36'},
    'green':   {'accent': '32', 'accent_b': '1;32', 'dim': '2;32'},
    'amber':   {'accent': '33', 'accent_b': '1;33', 'dim': '2;33'},
    'cyan':    {'accent': '36', 'accent_b': '1;36', 'dim': '2;36'},
    'mono':    {'accent': '37', 'accent_b': '1;37', 'dim': '2;37'},
    # Named after (and loosely color-inspired by) pn-mrc137-alpha.zip's
    # bundled Mystic BBS MRC themes -- not a port of that package's
    # actual .ans art (a fixed-position-overlay rendering model with no
    # equivalent here), just a matching set of chrome color identities
    # so /set palette bitchx etc. lands in the same spirit as the
    # same-named Mystic theme. See docs/27-mrc-chat.md.
    'original': {'accent': '34', 'accent_b': '1;34', 'dim': '2;34'},
    'minimal':  {'accent': '37', 'accent_b': '2;37', 'dim': '2;30'},
    'bitchx':   {'accent': '32', 'accent_b': '1;32', 'dim': '2;32'},
    '2leet4u':  {'accent': '35', 'accent_b': '1;35', 'dim': '2;35'},
    'least':    {'accent': '37', 'accent_b': '37',   'dim': '2;37'},
}


def _pipe_to_ansi(s: str) -> str:
    if not s:
        return ''
    def _sub(m):
        return f'\x1b[{_PIPE_COLORS.get(m.group(1), "37")}m'
    return _PIPE_RE.sub(_sub, s) + '\x1b[0m'


def _strip_pipe(s: str) -> str:
    return _PIPE_RE.sub('', s)


def _visible_len(text: str) -> int:
    n, pos = 0, 0
    while pos < len(text):
        m = _ANSI_SEQ_RE.match(text, pos)
        if m:
            pos = m.end()
            continue
        n += 1
        pos += 1
    return n


def _truncate_visible(text: str, max_v: int) -> str:
    """Truncate text to max_v visible characters; always appends ANSI reset."""
    if max_v <= 0:
        return '\x1b[0m'
    out, vis, pos = [], 0, 0
    while pos < len(text):
        m = _ANSI_SEQ_RE.match(text, pos)
        if m:
            out.append(m.group(0)); pos = m.end(); continue
        if vis >= max_v:
            break
        out.append(text[pos]); vis += 1; pos += 1
    return ''.join(out) + '\x1b[0m'


def _word_wrap(text: str, width: int, indent: str = '') -> list:
    """Return list of display lines, each ≤ width visible chars.
    Continuation lines are prefixed with *indent* (already counted in width)."""
    if width <= 0:
        return [text]
    if _visible_len(text) <= width:
        return [text]

    tokens = re.split(r'(\s+)', text)
    lines, cur_parts, cur_vis = [], [], 0
    indent_vis = _visible_len(indent)

    def flush():
        if cur_parts:
            lines.append(''.join(cur_parts))

    for tok in tokens:
        if not tok:
            continue
        tok_vis = _visible_len(tok)
        if tok.isspace():
            if not cur_parts:
                continue
            if cur_vis + tok_vis <= width:
                cur_parts.append(tok); cur_vis += tok_vis
            else:
                flush(); cur_parts = [indent]; cur_vis = indent_vis
            continue
        if cur_vis + tok_vis <= width:
            cur_parts.append(tok); cur_vis += tok_vis
        else:
            flush()
            if indent_vis + tok_vis > width:
                # hard-cut oversized word
                cur_parts = [indent + _truncate_visible(tok, width - indent_vis)]
                lines.append(''.join(cur_parts))
                cur_parts = []; cur_vis = indent_vis
            else:
                cur_parts = [indent, tok]; cur_vis = indent_vis + tok_vis
    flush()
    return lines if lines else [text]


def _split_for_wire(text: str, cap: int = MAX_OUTGOING_CHARS,
                    repeat_prefix: str = '') -> list:
    """Split text into wire-safe chunks (≤ cap chars each, INCLUDING
    `repeat_prefix` on every chunk).

    `repeat_prefix`, if given, is something that must be present at the
    start of every chunk on the wire -- e.g. the sender's active color
    pipe-code for a room-chat message. Real bug this closes: each chunk
    is sent as its own fully independent MRC message (a separate
    send_message call, not a client-side word-wrap of one received
    message -- see _chat_loop), so a color code set once at the start
    of the ORIGINAL long line only ever survived into the first chunk.
    Every later chunk arrived with no color of its own and rendered in
    whatever default/leftover color happened to be active on each
    recipient's client -- reported live as "loses its color" partway
    through a long message. Passing the same prefix in here budgets
    room for it up front and re-applies it to every chunk, not just
    the first.
    """
    text = (text or '').rstrip('\r\n')
    if not text:
        return []
    budget = max(1, cap - len(repeat_prefix))
    if len(text) <= budget:
        return [repeat_prefix + text]
    inner = max(1, budget - 8)          # reserve "(99/99) " tag
    words = text.split(' ')
    chunks, cur = [], ''
    for w in words:
        while len(w) > inner:
            if cur:
                chunks.append(cur); cur = ''
            chunks.append(w[:inner]); w = w[inner:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= inner:
            cur += ' ' + w
        else:
            chunks.append(cur); cur = w
    if cur:
        chunks.append(cur)
    if len(chunks) <= 1:
        return [repeat_prefix + c for c in chunks]
    t = len(chunks)
    return [f'{repeat_prefix}({i+1}/{t}) {c}' for i, c in enumerate(chunks)]


_TZ_OFFSET_RE = re.compile(r'^([+-]?)(\d{1,2})(?::?(\d{2}))?$')

# Common named-zone shortcuts for /set tz -- fixed offsets, not DST-aware
# (this feature is deliberately a plain UTC-offset, per the locked-in
# design decision; a caller observing daylight time picks the *DT name,
# not the *ST one, same as anyone reading a timezone abbreviation table).
# Requested directly (a raw "+/-5" offset was reported as "confusing"
# live on the Pi) -- North America is the primary set given this is a
# US-hosted BBS network, plus a handful of the other most common ones.
_TZ_ALIASES = {
    'UTC': 0, 'GMT': 0, 'Z': 0,
    'EST': -300, 'EDT': -240,
    'CST': -360, 'CDT': -300,
    'MST': -420, 'MDT': -360,
    'PST': -480, 'PDT': -420,
    'AKST': -540, 'AKDT': -480,
    'HST': -600,
    'AST': -240, 'ADT': -180,
    'BST': 60, 'CET': 60, 'CEST': 120,
    'EET': 120, 'EEST': 180,
    'IST': 330, 'JST': 540,
    'AWST': 480, 'ACST': 570, 'ACDT': 630,
    'AEST': 600, 'AEDT': 660,
    'NZST': 720, 'NZDT': 780,
}


def _parse_tz_offset(value: str):
    """Parse a /set tz value -- a named zone ('EST', 'PDT', 'UTC', ...,
    see _TZ_ALIASES) or a raw offset ('-5', '+05:30', '0530') -- into
    minutes from UTC, or None if unparseable/out of range."""
    v = (value or '').strip()
    if not v:
        return None
    alias = _TZ_ALIASES.get(v.upper())
    if alias is not None:
        return alias
    m = _TZ_OFFSET_RE.match(v)
    if not m:
        return None
    sign = -1 if m.group(1) == '-' else 1
    hours = int(m.group(2))
    minutes = int(m.group(3)) if m.group(3) else 0
    if hours > 14 or minutes > 59:
        return None
    total = sign * (hours * 60 + minutes)
    return total if -720 <= total <= 840 else None


# ── MRCChat class ───────────────────────────────────────────────────────────

class MRCChat(BaseChatSystem):

    DEFAULT_BRIDGE_URL = 'ws://127.0.0.1:8080/ws'
    DEFAULT_ROOM       = 'lobby'

    def __init__(self, session: SessionProtocol):
        super().__init__(session)
        self._ws               = None
        self._aiohttp_session  = None
        self._recv_task        = None
        self._ping_task        = None
        self._connected        = False
        self._handle           = ''
        self._room             = self.DEFAULT_ROOM
        self._topic            = ''
        self._mention_count    = 0
        self._mention_log      = deque(maxlen=50)
        self._known_users      = set()
        self._last_dm_from     = ''  # for /r (reply-to-last-DM), see _handle_event

        # Input-history recall on Ctrl+Up/Down (locked-in keybinding --
        # plain Up/Down stay bound to chat-scroll, this file's existing,
        # already-shipped convention). _history_pos is None when not
        # currently navigating (i.e. sitting on a fresh, unsent line);
        # _history_draft holds whatever the user had typed before the
        # first Ctrl+Up, restored on Ctrl+Down past the newest entry.
        self._input_history    = deque(maxlen=50)
        self._history_pos      = None
        self._history_draft    = ''

        # /set palette -- see _TERM_PALETTES. Local-only (like /set
        # clock), not persisted via set_prefs: purely a per-session
        # display preference, nothing another client needs to see.
        self._palette_name    = 'default'
        # Wire chars the bridge prepends before the typed message (styled
        # display handle for room chat, DM wrapper prefix for /t) -- sent by
        # the bridge on 'joined' (see mrc/bridge/main.py
        # _session_display_handle_wire_len/_session_dm_overhead). The bridge
        # hard-truncates the final packet at 140 chars *after* prepending
        # this, so outgoing chunks must be capped at 140 minus this to avoid
        # the tail silently getting cut off server-side. Matches the web
        # client's handleOverheadChars/dmOverheadChars (mrc/index.html).
        self._handle_overhead  = 0
        self._dm_overhead      = 0
        # Same idea, for /me: the bridge wraps action text in its own
        # fixed-color "|15* |13{nick} ...|07" (NOT the user's style --
        # see mrc/bridge/main.py's _session_action_overhead), so this
        # also has to be reserved from the 140-char budget. Previously
        # unaccounted for entirely -- /me always budgeted against the
        # full 140 with zero reservation, so a long action's tail got
        # silently cut off server-side with no warning.
        self._action_overhead  = 0
        self._scrollback       = deque(maxlen=SCROLLBACK_LINES)
        self._display_lines    = deque(maxlen=SCROLLBACK_LINES)  # rendered lines for screen
        self._split_screen     = True
        self._input_lock       = asyncio.Lock()
        self._input_buf        = []
        self._color_idx        = 0          # index into _COLOR_SEQ; '07' = default
        self._latency_ms       = None
        self._last_input_time  = time.time()
        self._is_away          = False
        self._scroll_offset    = 0   # 0 = latest; >0 = lines scrolled up

        # Terminal geometry — overwritten by _enter_split_screen
        self._term_lines   = 24
        self._term_columns = 80
        self._status_row   = 1
        self._scroll_top   = 2
        self._scroll_bottom = 23
        self._input_row    = 24

        # Nick-list sidebar (see _enter_split_screen/_sidebar_lines) --
        # only enabled on wide-enough terminals (matches the >=100-col
        # threshold this file already uses for the CPR terminal-size
        # probe). _chat_width is what _emit()'s word-wrap and
        # _redraw_chat_area actually target; it's the full terminal
        # width when no sidebar, or narrowed to leave room for it.
        self._sidebar_enabled = False
        self._sidebar_width   = 20
        self._chat_width      = 80

        # Scrolling ticker/banner (see _draw_ticker_line/_ticker_loop) --
        # a second fixed row, above the chat scroll region, rotating
        # through static tips + live hub-pushed BANNER: text + STATS:
        # text (both previously discarded/inert client-side -- see the
        # Phase A bridge notes on why STATS is opaque text, not
        # structured fields). Toggle wired to a real persisted pref in
        # a later phase; defaults on for now.
        self._show_ticker      = True
        self._ticker_row       = None   # set by _enter_split_screen
        self._ticker_task      = None
        self._ticker_pool      = deque(maxlen=20)
        self._ticker_idx       = 0
        self._ticker_scroll_pos = 0
        self._ticker_dwell     = 0

        # Twit/ignore list + broadcast shield (see mrc/bridge/main.py's
        # set_prefs/prefs_updated + _session_prefs) -- initialized from
        # the 'joined' event's prefs field, kept in sync via
        # prefs_updated after any /twit or future /set change.
        # Filtered-but-counted, not silently invisible -- matches the
        # reference client's model.
        self._twit_list             = set()
        self._twit_filter_enabled   = True
        self._broadcast_shield      = False
        self._twit_blocked_count    = 0
        self._shield_blocked_count  = 0
        # Room a fresh connect should land in, and clock rendering format --
        # both purely-local rendering hints synced from the bridge profile
        # (see mrc/bridge/main.py's _session_prefs), same pattern as
        # tz_offset below.
        self._default_room          = ''
        self._clock_format          = '24'

        # Remaining Phase A profile fields, surfaced via /set (see
        # _handle_slash's 'set' branch) -- local mirrors for /set list
        # display, kept in sync the same way as twit/shield above.
        self._enter_msg_tpl = ''
        self._leave_msg_tpl = ''
        self._quit_msg      = ''

        # Minutes offset from UTC for the clock widget + message
        # timestamps, settable via /set tz. All server-side timestamps in
        # this file are computed in UTC (see _local_now()) rather than
        # datetime.now(), which silently used the *server's* system
        # timezone -- correct on nobody's terminal except a caller who
        # happens to share the server's TZ (found live: server on UTC,
        # sysop on US Central, clock off by 5-6 hours). Defaults to 0
        # (UTC) until the caller sets their own offset; persisted via
        # set_prefs like the other /set fields above.
        self._tz_offset_minutes = 0

        # Full nick-style dict (prefix/suffix/colors), see mrc/bridge/
        # main.py's _session_style. Previously the terminal only ever
        # extracted typing_color out of this and discarded the rest --
        # /set prefix|suffix|color (below) needed somewhere to read
        # current values back from for /set list.
        self._style = {}

    # ── Menu entry point ────────────────────────────────────────────────────

    async def show_menu(self):
        # Called from ChatManager.show_menu() with no Flask app context
        # active (unlike _chat_flags() a few lines above it in chat.py,
        # which correctly scopes its own DB read in one) -- current_app
        # access here always raised RuntimeError, silently swallowed by
        # the bare except below, so this NEVER actually read
        # MRC_BRIDGE_PORT/MRC_BRIDGE_WS_URL from real config on any
        # install: every terminal MRC session silently fell back to the
        # hardcoded DEFAULT_BRIDGE_URL (port 8080) regardless of the
        # real bridge port (WEB_PORT+1, 5001 by default) -- a permanent,
        # silent connection failure for anyone actually using terminal
        # MRC, root-caused against a live sysop report of "connects on
        # web, not on terminal."
        from .bbs_ui import _app
        bridge_url = self.DEFAULT_BRIDGE_URL
        try:
            with _app().app_context():
                from flask import current_app
                url = current_app.config.get('MRC_BRIDGE_WS_URL')
                if url:
                    bridge_url = url
                else:
                    host   = current_app.config.get('MRC_BRIDGE_HOST', 'localhost')
                    port   = current_app.config.get('MRC_BRIDGE_PORT', 8080)
                    path   = current_app.config.get('MRC_BRIDGE_WS_PATH', '/ws')
                    scheme = 'wss' if current_app.config.get('MRC_BRIDGE_USE_SSL', False) else 'ws'
                    bridge_url = f'{scheme}://{host}:{port}{path.lstrip("/") and "/" + path.lstrip("/")}'
        except Exception:
            logger.warning('MRC terminal: could not resolve bridge URL from '
                           'config, falling back to %s', bridge_url, exc_info=True)

        username = (self.session.user.get('username')
                    if isinstance(self.session.user, dict)
                    else getattr(self.session.user, 'username', None)) or 'guest'
        handle = re.sub(r'\s+', '_', username)[:32]

        await self._connect_and_chat(bridge_url, handle, self.DEFAULT_ROOM)

    # ── Connect & chat ───────────────────────────────────────────────────────

    async def _connect_and_chat(self, bridge_url, handle, room):
        try:
            import aiohttp
        except ImportError:
            await self.session.write(
                '\r\n\x1b[31maiohttp not installed - cannot reach MRC bridge.\x1b[0m\r\n')
            return

        self._handle = handle
        self._room   = (room or self.DEFAULT_ROOM).strip().lstrip('#').lower()

        await self.session.write(
            f'\r\n\x1b[36mConnecting to MRC bridge at {bridge_url} ...\x1b[0m\r\n')

        self._aiohttp_session = aiohttp.ClientSession()
        try:
            try:
                self._ws = await asyncio.wait_for(
                    self._aiohttp_session.ws_connect(bridge_url), timeout=10)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                # This message only ever reached the user's own terminal
                # screen -- gone the instant the menu redraws, and
                # invisible to the sysop entirely. Log it too so a
                # persistent connect failure (wrong port, bridge down)
                # shows up in the journal instead of only ever being
                # described secondhand as "an error that flashed by."
                logger.warning('MRC terminal: bridge connect to %s failed: %s',
                               bridge_url, exc)
                await self.session.write(
                    f'\x1b[31mBridge connect failed: {exc}\x1b[0m\r\n'
                    'Is the anetbbs-mrc-bridge service running?\r\n')
                return

            self._connected = True
            self._last_input_time = time.time()
            self._is_away = False

            await self._send_json({
                'type': 'join_room',
                'handle': self._handle,
                'room': self._room,
                'ip': self._caller_ip(),
            })

            await self._enter_split_screen()
            await self._emit(
                f'\x1b[32mJoined #{self._room} as {self._handle}.\x1b[0m  '
                'Type /helpserver for commands, /quit to leave.')
            await self._emit(
                '\x1b[33mTip:\x1b[0m use [<] [>] arrow keys to change outgoing text color.')
            # Matches the reference client's own permanent, non-blocking
            # connect notice ("Use /identify password for MRC Trust",
            # helper_protocol.c) -- purely informational; a registered
            # handle's trust status lapses after a stretch of weeks on
            # the real network, so it's worth surfacing even though
            # chat is never gated on it.
            await self._emit(
                '\x1b[33mTip:\x1b[0m /identify <pass> if your handle is registered (MRC Trust).')

            self._recv_task = asyncio.create_task(self._recv_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())
            self._ticker_task = asyncio.create_task(self._ticker_loop())
            await self._chat_loop()
        finally:
            await self._exit_split_screen()
            await self._disconnect()

    # ── Keepalive ping loop ──────────────────────────────────────────────────

    async def _ping_loop(self):
        """Send a WS ping every PING_INTERVAL seconds to keep the connection
        alive through NAT/firewalls. Also manages IAMHERE AWAY tracking:
        after AWAY_AFTER seconds of no user input, tell the bridge the user
        is away (matching anetmrc behavior)."""
        try:
            while self._connected:
                await asyncio.sleep(PING_INTERVAL)
                if not self._connected:
                    break
                # WebSocket-level ping. Real bug found live: this sent
                # 'msgext' as the timestamp field, but the bridge's pong
                # handler (mrc/bridge/main.py) only ever echoes back
                # whatever key the web client actually uses -- 't' (see
                # client.js's own ping: `{type:'ping', t: Date.now()}`).
                # With the wrong key, the bridge's reply carried `t: null`,
                # the pong handler below never found a usable timestamp,
                # and self._latency_ms stayed None for the entire session
                # -- the status bar's own latency widget (see
                # _draw_status_line) silently never rendered. 't' matches
                # the web client's own wire convention exactly.
                await self._send_json({'type': 'ping', 't': time.time()})
                # IAMHERE AWAY / ACTIVE tracking
                idle = time.time() - self._last_input_time
                if idle >= AWAY_AFTER and not self._is_away:
                    self._is_away = True
                    await self._send_json({
                        'type': 'server_cmd', 'command': 'STATUS AFK'})
                    await self._emit('\x1b[2;37m(you are now marked away)\x1b[0m')
                elif idle < AWAY_AFTER and self._is_away:
                    self._is_away = False
                    await self._send_json({
                        'type': 'server_cmd', 'command': 'STATUS AFK'})
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug('MRC ping loop error', exc_info=True)

    async def _ticker_loop(self):
        """Advances and redraws the ticker row every TICKER_TICK_SECONDS.
        See _advance_ticker for the dwell/scroll state machine."""
        try:
            while self._connected:
                await asyncio.sleep(TICKER_TICK_SECONDS)
                if not self._connected:
                    break
                if not self._show_ticker or not self._ticker_row:
                    continue
                self._advance_ticker()
                async with self._input_lock:
                    await self._draw_ticker_line()
                    await self.session.write(f'\x1b[{self._input_row};3H')
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug('MRC ticker loop error', exc_info=True)

    # ── Split-screen helpers ─────────────────────────────────────────────────

    async def _enter_split_screen(self):
        ws = getattr(self.session, 'window_size', None) or (80, 24)
        try:
            cols, lines = int(ws[0]), int(ws[1])
        except (TypeError, ValueError):
            cols, lines = 80, 24

        # If session reports a suspiciously small size (NAWS not negotiated yet,
        # or telnet path that skips NAWS), actively query the terminal using the
        # ANSI CPR trick: move cursor to 999,999 then request position — the
        # terminal clamps to its actual size and reports back ESC[rows;colsR.
        if cols < 100:
            try:
                await self.session.write('\x1b[s\x1b[999;999H\x1b[6n\x1b[u')
                resp = b''
                deadline = asyncio.get_event_loop().time() + 1.5
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        chunk = await asyncio.wait_for(
                            self.session.read_raw(32), timeout=0.2)
                        if chunk:
                            resp += chunk
                        if b'R' in resp:
                            break
                    except Exception:
                        break
                import re as _re
                m = _re.search(rb'\x1b\[(\d+);(\d+)R', resp)
                if m:
                    lines = max(lines, int(m.group(1)))
                    cols  = max(cols,  int(m.group(2)))
            except Exception:
                pass

        self._term_columns  = max(64, min(240, cols))
        self._term_lines    = max(10, min(120, lines))
        self._status_row    = 1

        # Ticker gets its own fixed row directly below the status bar,
        # shrinking the chat scroll region by one row -- same technique
        # as the status bar itself (a plain redrawn row, not a second
        # DECSTBM region; a terminal only gets one scroll region).
        if self._show_ticker:
            self._ticker_row = 2
            self._scroll_top = 3
        else:
            self._ticker_row = None
            self._scroll_top = 2
        self._scroll_bottom = self._term_lines - 1
        self._input_row     = self._term_lines

        # Sidebar only on wide-enough terminals -- same >=100-col
        # threshold this function already treats as "wide" above.
        # Below that, a fixed-width nick column would eat too much of
        # an 80-col chat line to be worth it.
        self._sidebar_enabled = self._term_columns >= 100
        self._chat_width = (
            self._term_columns - self._sidebar_width - 1
            if self._sidebar_enabled else self._term_columns)

        await self.session.write(
            '\x1b[r'                                              # reset DECSTBM
            '\x1b[2J\x1b[H'                                       # clear screen
            f'\x1b[{self._scroll_top};{self._scroll_bottom}r'    # set scroll region
        )
        await self._draw_status_line()
        await self._draw_ticker_line()
        await self._draw_input_line()

    async def _exit_split_screen(self):
        try:
            await self.session.write(
                '\x1b[r'                              # reset scroll region to full screen
                '\x1b[?25h'                           # ensure cursor is visible
                '\x1b[1;1H\x1b[2K'                   # clear status bar row 1
                f'\x1b[{self._input_row};1H\x1b[2K'  # clear input row
                '\x1b[2J\x1b[H'                       # clear full screen, cursor home
                '\x1b[0m\r\n'                         # reset attributes
            )
        except Exception:
            pass

    async def _draw_status_line(self):
        if not self._split_screen:
            return
        room_s = f'\x1b[{self._pal("accent_b")}m[#{self._room}]\x1b[0m'
        topic_s = f' \x1b[1;36m{self._topic}\x1b[0m' if self._topic else ''

        right_bits = []
        if self._scroll_offset:
            right_bits.append(f'\x1b[1;37;43m PAUSED+{self._scroll_offset} \x1b[0m')
        if self._mention_count:
            # Explicit fg/bg (not reverse-video) -- matches the PAUSED badge's
            # approach above. The old '1;7;91' (bold+reverse+bright-red-fg)
            # relied on reverse-video swapping to produce a visible
            # background, which several terminals render as illegibly dark
            # red-on-red -- reported as invisible "unless you highlight it".
            right_bits.append(f'\x1b[1;37;41m !{self._mention_count} \x1b[0m')
        # Character count: show remaining chars out of the wire limit for
        # whatever's currently typed (plain chat / DM / /me each have a
        # different cap since the bridge prepends a different wrapper
        # before its hard 140-char cutoff -- see _chat_wire_cap/
        # _dm_wire_cap/_action_wire_cap).
        typed = len(self._input_buf)
        if typed > 0:
            buf_text = ''.join(self._input_buf)
            low = buf_text.lstrip()
            lower = low.lower()
            if any(lower.startswith(f'/{p} ') for p in
                   ('msg', 't', 'tell', 'dm', 'pm', 'whisper', 'w')):
                # Only the message portion (after "/cmd target ") counts
                # against the DM wire cap -- matches what the bridge
                # actually receives as the DM payload.
                parts = low.split(' ', 2)
                msg_part = parts[2] if len(parts) > 2 else ''
                remaining = self._dm_wire_cap() - len(msg_part)
            elif lower.startswith('/me '):
                remaining = self._action_wire_cap() - len(low[4:])
            elif lower.startswith('/'):
                remaining = MAX_OUTGOING_CHARS - typed
            else:
                remaining = self._chat_wire_cap() - typed
            if remaining < 0:
                cc = f'\x1b[1;91m{remaining}\x1b[0m'   # red: over limit
            elif remaining <= 15:
                cc = f'\x1b[1;93m{remaining}\x1b[0m'   # yellow: close
            else:
                cc = f'\x1b[1;36m{remaining}\x1b[0m'     # cyan: fine
            right_bits.append(cc)
        # Replaces the status bar's old clock widget (Jerry: per-message
        # timestamps already show the time on every line, so a second
        # clock at the top was redundant -- latency is the thing that
        # was actually missing). _format_clock() itself, and the
        # clockformat/tz settings, are unaffected -- still used for the
        # per-message timestamps. Only _show_clock (which had exactly
        # one effect, this row) and its now-purposeless /set clock
        # on|off toggle and _clock_loop() background refresher are gone.
        if self._latency_ms is not None:
            right_bits.append(f'\x1b[2;37m{self._latency_ms}ms\x1b[0m')
        if self._is_away:
            right_bits.append('\x1b[1;33mAWAY\x1b[0m')
        right = '  '.join(right_bits)

        # When the sidebar is up, this bar was previously drawn full
        # terminal-width -- blue background running straight across
        # where every row below it has a '│' border + nick column,
        # with no matching border of its own. Reported live on the Pi
        # as the status row being "the one spot" the border looked
        # broken at. Pad/measure against chat_width instead so the
        # bar's own content stops at the same column as the border,
        # then draw a matching border + blank nick-column-width gap.
        width = self._chat_width if self._sidebar_enabled else self._term_columns

        left_v  = _visible_len(room_s) + _visible_len(topic_s)
        right_v = _visible_len(right)
        gap = max(0, width - left_v - right_v)

        overflow = (left_v + right_v) - width
        if overflow > 0 and self._topic:
            cut = max(1, len(self._topic) - overflow - 1)
            topic_s = f' \x1b[1;36m{self._topic[:cut]}>\x1b[0m'
            gap = 0

        if self._sidebar_enabled:
            # Border color matches the literal '\x1b[2;37m│' every chat
            # row draws in _redraw_chat_area (not palette-driven -- that
            # border was never part of /set palette's scope either) so
            # this row's border lines up in color as well as column.
            tail = (
                '\x1b[0m\x1b[2;37m│\x1b[0m'
                + (' ' * self._sidebar_width)
                + '\x1b[0m\x1b[K'
            )
        else:
            # room_s, topic_s, and right all contain \x1b[0m resets which kill
            # the background color.  Re-assert \x1b[44m before the final
            # \x1b[K so those cells get blue background.
            tail = '\x1b[44m\x1b[K'

        # room_s, topic_s, and right all contain \x1b[0m resets which kill the
        # background color.  Re-assert \x1b[44m before the gap so those cells
        # get blue background.
        await self.session.write(
            '\x1b[1;1H'
            '\x1b[44m'
            + room_s + topic_s
            + '\x1b[44m'        # restore blue bg after \x1b[0m inside room_s/topic_s
            + ' ' * gap
            + right
            + tail
            + '\x1b[0m'
        )

    def _ticker_items(self) -> list:
        """Live pool (hub-pushed BANNER:/STATS: text, see _add_ticker_text)
        plus the static TICKER_TIPS fallback -- always non-empty, so the
        ticker still has something to show on a quiet install with no
        live banners yet."""
        return list(self._ticker_pool) + list(TICKER_TIPS)

    def _add_ticker_text(self, text: str):
        text = _strip_pipe(_ANSI_SEQ_RE.sub('', text or '')).strip()
        if not text:
            return
        # Skip an exact immediate repeat (e.g. an unchanged STATS reply
        # every refresh interval) rather than letting the pool fill up
        # with duplicates of the same line -- it can still reappear
        # later if the pool rotates past other content and back.
        if self._ticker_pool and self._ticker_pool[-1] == text:
            return
        self._ticker_pool.append(text)

    def _advance_ticker(self):
        """Advance ticker state by one tick -- called right before each
        redraw. Short items (fit within the terminal width) just dwell
        for a few ticks before rotating to the next item; long items
        scroll a few characters per tick until they've fully passed,
        then rotate. Not async: pure state mutation, no I/O."""
        items = self._ticker_items()
        if not items:
            return
        cur = items[self._ticker_idx % len(items)]
        width = max(10, self._term_columns)
        if len(cur) <= width:
            self._ticker_dwell += 1
            if self._ticker_dwell >= 4:  # ~4 ticks * TICKER_TICK_SECONDS dwell
                self._ticker_dwell = 0
                self._ticker_scroll_pos = 0
                self._ticker_idx = (self._ticker_idx + 1) % len(items)
        else:
            self._ticker_scroll_pos += 4
            if self._ticker_scroll_pos >= len(cur) + 8:
                self._ticker_scroll_pos = 0
                self._ticker_dwell = 0
                self._ticker_idx = (self._ticker_idx + 1) % len(items)

    async def _draw_ticker_line(self):
        if not self._split_screen or not self._ticker_row:
            return
        items = self._ticker_items()
        # Same border-alignment fix as _draw_status_line: this row was
        # previously scrolled/measured against the full terminal width
        # with no border of its own, so it had nothing lining up with
        # the '|' every chat/status row draws when the sidebar is up --
        # "border still has one spot" reported live on the Pi, on this
        # exact row (the "Explore the many new commands..." tip line).
        width = self._chat_width if self._sidebar_enabled else self._term_columns
        if not items:
            text = ''
        else:
            cur = items[self._ticker_idx % len(items)]
            if len(cur) <= width:
                text = cur
            else:
                # 8-space gap before the same text loops back around,
                # so a long banner reads as a continuous scroll rather
                # than an abrupt jump-cut back to its own start.
                padded  = cur + (' ' * 8)
                doubled = padded * 2
                pos = self._ticker_scroll_pos % len(padded)
                text = doubled[pos:pos + width]

        if self._sidebar_enabled:
            pad = max(0, width - _visible_len(text))
            tail = (
                (' ' * pad)
                + '\x1b[0m\x1b[2;37m│\x1b[0m'
                + (' ' * self._sidebar_width)
            )
        else:
            tail = ''

        await self.session.write(
            f'\x1b[{self._ticker_row};1H\x1b[2K'
            f'\x1b[{self._pal("dim")}m{text}\x1b[0m{tail}')

    async def _draw_input_line(self):
        if not self._split_screen:
            return
        text = ''.join(self._input_buf)
        # Mask password fields.  For /register mask only the password word
        # and leave the email visible; for other secure commands mask all.
        low = text.lower()
        if low.startswith('/register '):
            pfx_len = len('/register ')
            rest = text[pfx_len:]
            sp = rest.find(' ')
            if sp >= 0:
                text = '/register ' + '*' * min(sp, 20) + rest[sp:]
            else:
                text = '/register ' + '*' * min(len(rest), 20)
        elif self._should_mask(text):
            for pfx in ('/identify ', '/update password ', '/trust identify '):
                if low.startswith(pfx):
                    text = pfx + '*' * (len(text) - len(pfx))
                    break

        color_a = self._current_color_ansi()
        prompt = '\x1b[1;96m> \x1b[0m'
        prompt_v = 2
        max_text = max(8, self._term_columns - prompt_v - 1)
        if len(text) > max_text:
            text = '<' + text[-(max_text - 1):]

        await self.session.write(
            f'\x1b[{self._input_row};1H'
            '\x1b[2K'
            + prompt + color_a + text + '\x1b[0m'
        )

    # ── Emit — direct-draw scroll region ────────────────────────────────────

    def _scroll_rows(self) -> int:
        return max(1, self._scroll_bottom - self._scroll_top + 1)

    async def _scroll_chat(self, delta: int):
        """Scroll the chat view by delta lines (+ve = up, -ve = down / towards latest)."""
        scroll_rows = self._scroll_rows()
        total = len(self._display_lines)
        max_off = max(0, total - scroll_rows)
        self._scroll_offset = max(0, min(max_off, self._scroll_offset + delta))
        await self._redraw_chat_area()

    async def _emit(self, text: str, extra_indent: str = ''):
        """Append text to the chat history and redraw the entire scroll region.

        Approach: maintain _display_lines (rendered display strings).  For each
        emit, take the last N lines (N = scroll region height) and draw them
        row-by-row with explicit cursor positioning + line-clear.  No DECSTBM
        scroll dependency — every row is cleared before writing, so no bleed
        from previous content is possible.

        `extra_indent` is additional continuation-line indent (visible chars,
        as a plain space string) beyond the auto-added "HH:MM " timestamp --
        for callers whose *text* itself has its own leading column structure
        (e.g. /mentions' time/room/from columns) that plain word-wrap has no
        way to know about on its own. Without it, a wrapped continuation line
        only aligns under the "HH:MM " timestamp, not under wherever the
        caller's own content actually starts.
        """
        self._scrollback.append(text)

        if not self._split_screen:
            await self.session.write(text + '\r\n')
            return

        ts     = self._format_clock(self._local_now()) + ' '
        ts_len = len(ts)                     # 6 for 24h "HH:MM ", 5-6 for 12h "H:MMa "/"HH:MMa "
        # extra_indent goes on BOTH the first-line prefix and the
        # continuation indent -- not prepended to *text* itself, since
        # _word_wrap() deliberately drops leading whitespace on a fresh
        # line (it's what keeps a normal wrapped sentence from picking up
        # a stray leading space), which would silently eat a manually
        # prepended indent otherwise.
        pfx0   = f'\x1b[2;37m{ts}\x1b[0m' + extra_indent  # first line
        indent = ' ' * ts_len + extra_indent  # alignment for continuation lines
        max_w  = max(20, self._chat_width - ts_len - len(extra_indent))

        new_lines = 0
        for i, wline in enumerate(_word_wrap(text, max_w, indent=indent)):
            pfx = pfx0 if i == 0 else indent
            self._display_lines.append(pfx + wline)
            new_lines += 1

        # When the user has scrolled up, advance the offset by however many
        # new lines just arrived so the historical view stays locked in place.
        if self._scroll_offset > 0:
            scroll_rows = self._scroll_rows()
            max_off = max(0, len(self._display_lines) - scroll_rows)
            self._scroll_offset = min(max_off, self._scroll_offset + new_lines)

        await self._redraw_chat_area()

    def _sidebar_lines(self, n_rows: int) -> list:
        """Pre-formatted, width-bounded strings for the nick-list
        sidebar -- a header line, then one nick per row, with a final
        '+N more' line if the roster doesn't fit. Always returns
        exactly n_rows entries (blank-padded) so callers can index by
        row directly against the chat scroll region's rows."""
        if n_rows <= 0:
            return []
        users = sorted(self._known_users, key=str.lower)
        lines = [_truncate_visible(
            f'\x1b[{self._pal("accent_b")}mUsers ({len(users)})\x1b[0m', self._sidebar_width)]
        avail = n_rows - 1
        if avail > 0:
            if len(users) <= avail:
                for u in users:
                    lines.append(_truncate_visible(
                        f'\x1b[{self._pal("accent")}m{u}\x1b[0m', self._sidebar_width))
            else:
                shown = users[:max(0, avail - 1)]
                for u in shown:
                    lines.append(_truncate_visible(
                        f'\x1b[{self._pal("accent")}m{u}\x1b[0m', self._sidebar_width))
                remaining = len(users) - len(shown)
                lines.append(_truncate_visible(
                    f'\x1b[2m+{remaining} more\x1b[0m', self._sidebar_width))
        while len(lines) < n_rows:
            lines.append('')
        return lines[:n_rows]

    async def _redraw_chat_area(self):
        """Redraw the scroll region from _display_lines and restore status bar.

        The input row is intentionally NOT cleared or redrawn here.  Clearing it
        on every incoming message erased what the SSH client had locally echoed,
        making typed text invisible.  The input row is only redrawn by
        _draw_input_line(), which is called from _read_chat_line() on each
        keystroke and from _enter_split_screen() on entry.
        """
        scroll_rows = self._scroll_rows()
        all_lines   = list(self._display_lines)
        total       = len(all_lines)

        if self._scroll_offset > 0:
            # Paused: show a historical window ending before the offset
            end   = max(0, total - self._scroll_offset)
            start = max(0, end - scroll_rows)
            visible = all_lines[start:end]
        else:
            visible = all_lines[-scroll_rows:]

        pad = scroll_rows - len(visible)

        # Sidebar rows are computed once per redraw and merged onto the
        # SAME row writes as the chat text below -- DECSTBM only
        # constrains vertical scrolling, there's no separate
        # left/right scroll-region concept, so the sidebar has to ride
        # along on each chat row's own \x1b[2K-clear-then-write rather
        # than being its own independently redrawn region.
        sidebar = self._sidebar_lines(scroll_rows) if self._sidebar_enabled else None

        out = []
        for idx in range(scroll_rows):
            row = self._scroll_top + idx
            out.append(f'\x1b[{row};1H\x1b[2K')
            line = visible[idx - pad] if idx >= pad else ''
            if sidebar is not None:
                # Stored lines are word-wrapped against _chat_width at emit
                # time, but that width can change afterward (sidebar
                # toggled on mid-session, terminal resize) without
                # re-wrapping already-buffered lines -- an over-width line
                # here would push the border past its column, and on a
                # real terminal can even auto-wrap onto the next physical
                # row and corrupt that row's redraw. Truncate defensively
                # so the border always lands in the same column regardless
                # of what width a given line was originally wrapped for.
                if line and _visible_len(line) > self._chat_width:
                    line = _truncate_visible(line, self._chat_width)
                fill = max(0, self._chat_width - _visible_len(line))
                out.append(line + (' ' * fill))
                out.append('\x1b[2;37m│\x1b[0m ')
                out.append(sidebar[idx])
            elif line:
                out.append(line)
        # After drawing chat rows, position cursor at input row so the
        # status-bar write (which moves cursor to row 1) is the last
        # cursor move before we hand control back.
        out.append(f'\x1b[{self._input_row};1H')

        async with self._input_lock:
            await self.session.write(''.join(out))
            await self._draw_status_line()
            # Restore cursor to input row col 3 (just after the "> " prompt)
            # so the terminal's own echo (or our next _draw_input_line call)
            # continues from the correct position.
            await self.session.write(f'\x1b[{self._input_row};3H')

    # ── Receive loop ─────────────────────────────────────────────────────────

    async def _recv_loop(self):
        import aiohttp
        try:
            while self._connected and self._ws is not None:
                msg = await self._ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    await self._handle_event(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                  aiohttp.WSMsgType.CLOSING,
                                  aiohttp.WSMsgType.CLOSE,
                                  aiohttp.WSMsgType.ERROR):
                    await self._emit(
                        '\x1b[33m*** Bridge closed the connection.\x1b[0m')
                    self._connected = False
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('MRC recv loop crashed')
            self._connected = False

    def _local_now(self) -> datetime:
        """Current time adjusted by the caller's /set tz offset. Base is
        UTC (not datetime.now()) so the offset is meaningful regardless
        of what timezone the server's own system clock happens to be
        set to -- see _tz_offset_minutes for the bug this replaced."""
        return datetime.utcnow() + timedelta(minutes=self._tz_offset_minutes)

    def _format_clock(self, dt: datetime) -> str:
        """Render a timestamp per /set clockformat (12|24, default 24).
        12-hour form drops leading zero and trailing :00 seconds noise
        the same way the reference client's CLOCKFORMAT option does --
        `HH:MMa`, e.g. '2:07p' -- since every clock/timestamp display in
        this file already assumed bare 24h HH:MM before this setting
        existed, this is the single seam all of them route through now."""
        if self._clock_format == '12':
            return dt.strftime('%I:%M%p').lstrip('0').lower()
        return dt.strftime('%H:%M')

    def _pal(self, key: str) -> str:
        return _TERM_PALETTES.get(self._palette_name, _TERM_PALETTES['default'])[key]

    def _format_tz_offset(self) -> str:
        sign = '-' if self._tz_offset_minutes < 0 else '+'
        h, m = divmod(abs(self._tz_offset_minutes), 60)
        return f'UTC{sign}{h:02d}:{m:02d}'

    def _apply_style(self, style):
        """Sync local style state (prefix/suffix/colors) from a
        'joined' or 'style_updated' event's style field (see
        mrc/bridge/main.py's _session_style). Not async: pure state
        mutation, no I/O."""
        if not isinstance(style, dict):
            return
        self._style = style
        tc = style.get('typing_color')
        if tc in _COLOR_SEQ:
            self._color_idx = _COLOR_SEQ.index(tc)

    def _style_payload(self, **overrides) -> dict:
        """A full set_style request built from the last-known style
        (self._style, kept in sync by _apply_style) with specific
        fields overridden. ALWAYS use this to send set_style, never a
        bare partial dict -- see _cycle_color's comment for why: the
        bridge hard-defaults prefix/suffix to '' when the field is
        simply absent from the request, unlike every other style field
        (which correctly falls back to the existing session value)."""
        st = self._style or {}
        base = st.get('color', '07')
        payload = {
            'type':         'set_style',
            'prefix':       st.get('prefix', ''),
            'suffix':       st.get('suffix', ''),
            'color':        base,
            'prefix_color': st.get('prefix_color', base),
            'handle_color': st.get('handle_color', base),
            'suffix_color': st.get('suffix_color', base),
            'typing_color': st.get('typing_color', '10'),
        }
        payload.update(overrides)
        return payload

    async def _apply_prefs(self, prefs, is_initial_join=False):
        """Sync local state from a 'joined' or 'prefs_updated' event's
        prefs field (see mrc/bridge/main.py's _session_prefs).

        Async (unlike a plain state-sync would need to be) because
        ticker_enabled needs special handling: _enter_split_screen()
        already ran once by the time 'joined' arrives (it fires before
        the bridge round-trip completes, see _connect_and_chat), using
        the hardcoded self._show_ticker default -- so a persisted
        "ticker off" preference wouldn't actually take effect until
        this re-runs the same split-screen layout negotiation with the
        correct value and repaints the chat history into the
        (possibly resized) scroll region.

        `is_initial_join` gates the default_room auto-/join below: it
        must only fire once, right after the very first connect of the
        session (which always lands in DEFAULT_ROOM='lobby' before this
        runs -- see _connect_and_chat), never on a later prefs_updated
        (a sysop changing /set defaultroom mid-session should not be
        unexpectedly teleported to it -- matches the reference client's
        own DEFAULTROOM semantics of "room auto-joined at login").
        """
        if not isinstance(prefs, dict):
            return
        twit_list = prefs.get('twit_list')
        if isinstance(twit_list, list):
            self._twit_list = {str(t).strip().lower() for t in twit_list if str(t).strip()}
        twit_filter = prefs.get('twit_filter_enabled')
        if isinstance(twit_filter, bool):
            self._twit_filter_enabled = twit_filter
        shield = prefs.get('broadcast_shield')
        if isinstance(shield, bool):
            self._broadcast_shield = shield
        for key, attr in (('enter_msg_tpl', '_enter_msg_tpl'),
                          ('leave_msg_tpl', '_leave_msg_tpl'),
                          ('quit_msg', '_quit_msg')):
            val = prefs.get(key)
            if isinstance(val, str):
                setattr(self, attr, val)

        default_room = prefs.get('default_room')
        if isinstance(default_room, str):
            self._default_room = default_room.strip().lstrip('#').lower()

        clock_format = prefs.get('clock_format')
        if clock_format in ('12', '24'):
            self._clock_format = clock_format

        tz = prefs.get('tz_offset')
        if isinstance(tz, (int, float)):
            self._tz_offset_minutes = int(tz)

        ticker = prefs.get('ticker_enabled')
        if isinstance(ticker, bool) and ticker != self._show_ticker and self._split_screen:
            self._show_ticker = ticker
            # NOT wrapped in _input_lock here: _redraw_chat_area()
            # already acquires it internally, and asyncio.Lock is NOT
            # reentrant in this codebase's own established experience
            # (a prior real deadlock came from exactly this mistake) --
            # double-acquiring here would hang the whole session.
            # _enter_split_screen() itself never touches the lock.
            await self._enter_split_screen()
            await self._redraw_chat_area()

        if is_initial_join and self._default_room and self._default_room != self._room:
            new_room = self._default_room
            await self._send_json({
                'type': 'server_cmd',
                'command': f'JOIN {new_room}',
            })
            self._room = new_room
            await self._emit(f'\x1b[1;36mJoining your default room #{new_room}...\x1b[0m')

    async def _handle_event(self, data: dict):
        evt  = data.get('type', '')

        # ── pong: WS-level keepalive only, NOT a latency source ──
        # A prior version of this handler computed self._latency_ms from
        # this round-trip -- but 'ping'/'pong' here are purely local:
        # this WebSocket connects the terminal to the bridge daemon on
        # the SAME machine (mrc/bridge/main.py's handle_websocket just
        # echoes 't' straight back, see its own docstring), not to the
        # real upstream MRC hub. That measured near-zero loopback time
        # and displayed it as if it were real network latency -- exactly
        # backwards from what a status-bar latency widget is for (see
        # the 'latency' branch below for the real measurement). Ping
        # itself is still sent every PING_INTERVAL purely to keep the
        # WebSocket alive through NAT/firewalls; its reply is just
        # discarded now.
        if evt == 'pong':
            return

        # ── latency: real round-trip to the upstream MRC hub ──
        # Pushed by the bridge (mrc/bridge/main.py's _broadcast_latency)
        # whenever LatencyTracker observes the hub echo a packet back --
        # a genuine measurement of the actual MRC connection, unlike the
        # discarded local pong above.
        if evt == 'latency':
            try:
                ms = data.get('ms')
                if ms is not None:
                    self._latency_ms = max(0, int(ms))
                    async with self._input_lock:
                        await self._draw_status_line()
                        await self._draw_input_line()
            except (TypeError, ValueError):
                pass
            return

        # ── strip noise / extract actionable server state ──
        body_raw = data.get('body') or data.get('message') or ''
        if isinstance(body_raw, str):
            head = body_raw.lstrip().upper()

            if head.startswith('ROOMTOPIC:'):
                payload = body_raw.split(':', 1)[1] if ':' in body_raw else ''
                if ':' in payload:
                    room, _, topic = payload.partition(':')
                    new_topic = topic.strip()
                    # The hub can re-send ROOMTOPIC: without the topic
                    # actually changing (periodic refresh, alongside an
                    # unrelated userlist update, etc.) -- only announce
                    # a genuine change.
                    changed = new_topic != self._topic
                    self._topic = new_topic
                    if changed and room.strip().lower() == self._room.lower():
                        await self._emit(
                            f'\x1b[1;96m── Topic: {_pipe_to_ansi(self._topic)}\x1b[0m')
                return

            if head.startswith('USERROOM:'):
                room = body_raw.split(':', 1)[1].strip()
                if room and room.lower() != self._room.lower():
                    self._room = room
                    await self._emit(f'\x1b[1;36m*** Now in #{room}\x1b[0m')
                return

            if head.startswith('USERIN:'):
                raw_nick = body_raw.split(':', 1)[1].strip()
                nick = re.split(r'[\s@]', raw_nick, 1)[0]
                if nick:
                    self._known_users.add(nick)
                return

            if head.startswith('USEROUT:'):
                raw_nick = body_raw.split(':', 1)[1].strip()
                nick = re.split(r'[\s@]', raw_nick, 1)[0]
                if nick:
                    self._known_users.discard(nick)
                return

            if head.startswith('USERLIST:'):
                # Per the MRC protocol spec (USERLIST transaction): the
                # reply is a COMMA-separated list with no spaces --
                # "{user1},{user2},..." -- not whitespace-separated. This
                # was parsed with raw_list.split() (whitespace) before,
                # which never once correctly split a real hub response:
                # with no whitespace in the payload at all, the entire
                # comma-joined string became a single bogus "nick" and no
                # individual user ever made it into _known_users. This is
                # why the WHOON-triggers-a-USERLIST-refresh fix alone
                # didn't fix tab-complete -- the refresh was arriving
                # correctly, the parser just couldn't read it. The web
                # client has always split on ',' correctly (mrc/index.html
                # tryParseUserListFromServerMessage) -- matched that here.
                # Real bug found live: this only ever ADDED entries to
                # _known_users, never removed any -- so if a single
                # user's own USEROUT: event was ever missed (dropped
                # frame, ordering hiccup, or a leave reason the hub
                # just doesn't send USEROUT for), that stale name stuck
                # around in the userlist FOREVER, since even a fresh,
                # completely correct USERLIST: bulk refresh could never
                # clean it up -- purely additive, no way to shrink.
                # USERLIST: is the hub's own authoritative full-room
                # snapshot, so it must REPLACE the known set, not just
                # union into whatever was already there.
                raw_list = body_raw.split(':', 1)[1].strip()
                fresh_users = set()
                for entry in raw_list.split(','):
                    entry = entry.strip()
                    nick = re.split(r'[@]', entry, 1)[0]
                    if nick:
                        fresh_users.add(nick)
                self._known_users = fresh_users
                return

            if head.startswith('USERNICK:'):
                # Per the actual protocol spec, this carries exactly ONE
                # nick value (Request: SERVER~~~CLIENT~~~USERNICK:nick~),
                # not an "old new" pair -- real bug found auditing against
                # the spec: raw.split(None, 1) on a single-token value
                # always produced parts=[nick] (no space to split on), so
                # new_nick was unconditionally '' and never re-added,
                # while old_nick (actually just the SAME nick) got
                # unconditionally DISCARDED from the roster every time --
                # a slow leak that silently shrank tab-completion/mention
                # coverage with every USERNICK broadcast, never restoring
                # what it removed. There's no "old" value to reconcile
                # against on the wire; just record the announced nick as
                # known (USERLIST's periodic full-roster refresh already
                # corrects any staleness this leaves behind).
                raw = body_raw.split(':', 1)[1].strip()
                nick = re.split(r'[\s@]', raw, 1)[0] if raw else ''
                if nick:
                    self._known_users.add(nick)
                return

            if head.startswith('BANNER:'):
                # Previously fully discarded, no ticker existed yet to
                # feed. Now captured into the ticker pool (see
                # _add_ticker_text) instead of shown inline -- matches
                # its old fully-silent inline behavior, just usefully
                # captured now rather than thrown away.
                self._add_ticker_text(body_raw.split(':', 1)[1] if ':' in body_raw else '')
                return

            if head.startswith('STATS:'):
                # Originally left showing inline as ordinary text (see
                # git history) since it predated the ticker and there
                # was nowhere else to put it. Now that the ticker pool
                # captures it (_add_ticker_text), the inline copy is
                # just noise -- reported live on the Pi as a raw
                # "STATS:175 15 39 2 170 34.0" line popping up mid-chat.
                # Silenced the same way BANNER: already is, above.
                self._add_ticker_text(body_raw.split(':', 1)[1] if ':' in body_raw else '')
                return

            if head.startswith(('USERIP:', 'TYPING:', 'CAPABILITIES:',
                                  'NEWROOM:', 'NEWTOPIC:', 'KEEPALIVE:')):
                return

        if evt == 'error':
            await self._emit(
                f'\x1b[31m*** Error: {data.get("message", "")}\x1b[0m')
            return

        if evt == 'joined':
            # Silent -- _connect_and_chat already prints its own "Joined
            # #room as handle" greeting optimistically before this arrives.
            # Just absorb the wire-overhead figures for outgoing length caps.
            ho = data.get('handle_overhead')
            do = data.get('dm_overhead')
            ao = data.get('action_overhead')
            if isinstance(ho, int):
                self._handle_overhead = ho
            if isinstance(do, int):
                self._dm_overhead = do
            if isinstance(ao, int):
                self._action_overhead = ao
            # Restore the sysop's saved outgoing text color (persisted
            # server-side via set_style, same field the web client's
            # typing-color dropdown reads/writes -- see _cycle_color()).
            # Without this the arrow-key color always reset to default
            # ('07') every reconnect even though the handle prefix/suffix/
            # color style loaded correctly, since those live in a
            # different part of the same 'joined' payload this handler
            # was already ignoring wholesale before now.
            self._apply_style(data.get('style'))
            await self._apply_prefs(data.get('prefs'), is_initial_join=True)
            return

        if evt == 'style_updated':
            self._apply_style(data.get('style'))
            await self._emit('\x1b[1;36m(style updated)\x1b[0m')
            return

        if evt == 'prefs_updated':
            await self._apply_prefs(data.get('prefs'))
            await self._emit('\x1b[1;36m(preferences updated)\x1b[0m')
            return

        if evt == 'system':
            body = data.get('body', '')
            await self._emit(_pipe_to_ansi(body) if body else '')
            return

        if evt in ('chatters', 'rooms'):
            items = data.get('items') or []
            label = 'Online' if evt == 'chatters' else 'Rooms'
            if evt == 'chatters':
                for it in items:
                    nick = re.split(r'[\s@]', str(it).strip(), 1)[0]
                    if nick:
                        self._known_users.add(nick)
            await self._emit(
                f'\x1b[1;36m{label}:\x1b[0m {", ".join(items) or "(none)"}')
            return

        if evt in ('mrc_message', 'message', 'notice', 'banner',
                   'whoon_line', 'rooms_line', 'motd', 'motd_line',
                   'server_text', 'info'):
            body = (data.get('body') or data.get('message')
                    or data.get('text') or '')
            # Track sender for tab-complete (bridge sends from_user, not user)
            uname = data.get('from_user') or data.get('user') or ''
            if uname and uname not in ('?', 'SERVER', 'CLIENT', 'NOTME'):
                self._known_users.add(uname)

            # Twit/ignore filter -- dropped silently but counted (see
            # __init__), matches the reference client's model. Checked
            # for every message type in this bucket (not just plain
            # chat) since a twitted user's CTCP/notice/etc. traffic is
            # just as unwanted.
            if self._twit_filter_enabled and uname and uname.lower() in self._twit_list:
                self._twit_blocked_count += 1
                return

            # Broadcast shield -- a true network-wide broadcast has no
            # specific to_user AND no specific to_room (both blank on
            # the wire, forwarded verbatim by the bridge in the
            # mrc_message payload) -- matches the reference client's
            # own definition of a broadcast.
            if evt == 'mrc_message' and self._broadcast_shield:
                to_user = data.get('to_user') or ''
                to_room = data.get('to_room') or ''
                if not to_user and not to_room:
                    self._shield_blocked_count += 1
                    return

            # Format CTCP lines
            plain = _strip_pipe(_ANSI_SEQ_RE.sub('', body))
            if plain.startswith('[CTCP] '):
                pieces = plain[7:].split(None, 3)
                if len(pieces) >= 3:
                    sender, _, ctcp_cmd = pieces[0], pieces[1], pieces[2]
                    args = pieces[3] if len(pieces) > 3 else ''
                    await self._emit(
                        f'\x1b[1;30m[\x1b[1;33mCTCP\x1b[1;30m]\x1b[0m '
                        f'\x1b[1m{sender}\x1b[0m -> \x1b[33m{ctcp_cmd.upper()}\x1b[0m'
                        + (f' \x1b[2m{args}\x1b[0m' if args else ''))
                return
            if plain.startswith('[CTCP-REPLY] '):
                rest = plain[13:]
                await self._emit(
                    f'\x07\x1b[1;30m[\x1b[1;33mCTCP-REPLY\x1b[1;30m]\x1b[0m '
                    f'\x1b[1;96m{rest}\x1b[0m')
                return
            if body:
                # NOTE: this used to also try to "fish a nick out of the
                # formatted body" here with a regex, as a tab-complete
                # roster source. Removed 2026-07-04: it's an anchored
                # match against arbitrary free-form text (chat lines,
                # WHOON dumps, MOTD, banners, ...), so it both MISSES real
                # users (anything not shaped like "Nick: text" or
                # "[Nick] text" -- e.g. every line of a WHOON roster dump,
                # which starts with "*.:") and MATCHES false positives
                # (the first word of an ordinary sentence looks exactly
                # like a "nick" to this regex). Both reference
                # implementations (the web MRC client's
                # tryParseUserListFromServerMessage, and the anetmrc_v1.3.9
                # C client's mrc_send_userlist) deliberately never scrape
                # chat/WHOON body text for the roster -- they only trust
                # the structured USERLIST:/CHATTERS: control messages
                # (parsed below via the USERLIST:/USERIN:/USERNICK:
                # prefix checks earlier in this function, and via
                # 'chatters'/'rooms' events). Matched that model instead;
                # see _handle_server_cmd's WHOON handling in
                # mrc/bridge/main.py, which now also pushes a fresh
                # structured USERLIST whenever a client runs /who, so the
                # roster stays current without needing this fallback.
                # Mention detection: real chat traffic arrives as
                # 'mrc_message' (mrc/bridge/main.py), NOT as 'chat'/'action'/
                # 'private' -- those typed-event branches below never fire
                # against the actual bridge, which is why /mentions always
                # showed 0. The bridge embeds a literal '/DirectMsg' marker
                # in the formatted body for PMs (see mrc/bridge/main.py
                # _dm_wrapper_prefix) -- same marker the web client checks
                # (mrc/index.html dmToMe) -- rather than a distinct event
                # type. A message from yourself, or from SERVER, never
                # counts, matching the web client's isFromMe/isServer guards.
                is_dm = evt == 'mrc_message' and '/DirectMsg' in plain
                is_server = uname.upper() == 'SERVER'
                is_from_me = bool(uname) and bool(self._handle) \
                    and uname.lower() == self._handle.lower()
                mentioned = (evt == 'mrc_message' and not is_server
                            and not is_from_me
                            and (self._was_mentioned(plain) or is_dm))
                # Track the most recent inbound DM's sender for /r
                # (reply-to-last-DM) -- never from yourself/SERVER, same
                # guards as the mention log above.
                if is_dm and not is_server and not is_from_me and uname:
                    self._last_dm_from = uname
                if mentioned:
                    self._mention_count += 1
                    self._mention_log.append({
                        'time': self._format_clock(self._local_now()),
                        'room': f"#{data.get('from_room')}" if data.get('from_room') else '',
                        'from': f"{uname}@{data.get('from_site', '')}" if uname else 'someone',
                        'body': ('[DM] ' if is_dm else '') + plain[:200],
                    })
                    await self._emit(
                        '\x07' + self._highlight_mentions(_pipe_to_ansi(body)))
                else:
                    await self._emit(_pipe_to_ansi(body))
            return

        # ── Typed events: chat / action / private ──
        user      = data.get('user', '?')
        bbs       = data.get('bbs', '?')
        body      = data.get('body', '') or data.get('message', '')
        room      = data.get('room', '')
        plain_body = _strip_pipe(body or '')

        if user and user not in ('?', 'SERVER', 'CLIENT', 'NOTME'):
            self._known_users.add(user)

        if evt == 'private':
            self._mention_count += 1
            self._mention_log.append({
                'time': self._format_clock(self._local_now()),
                'room': '(PM)',
                'from': f'{user}@{bbs}',
                'body': plain_body[:200],
            })
            rendered = self._highlight_mentions(_pipe_to_ansi(body))
            await self._emit(
                f'\x07\x1b[1;30m[\x1b[1;33mPM\x1b[1;30m]\x1b[0m '
                f'\x1b[1;93m{user}@{bbs}\x1b[0m: {rendered}')
            return

        if evt == 'action':
            mentioned = self._was_mentioned(plain_body)
            if mentioned:
                self._mention_count += 1
                self._mention_log.append({
                    'time': self._format_clock(self._local_now()),
                    'room': f'#{room}' if room else '',
                    'from': f'{user}@{bbs}',
                    'body': f'* {plain_body[:200]}',
                })
            bell = '\x07' if mentioned else ''
            await self._emit(
                f'{bell}\x1b[35m* \x1b[1m{user}\x1b[22m@\x1b[2m{bbs}\x1b[0m '
                f'\x1b[35m{self._highlight_mentions(_pipe_to_ansi(body))}\x1b[0m')
            return

        if evt == 'chat':
            mentioned = self._was_mentioned(plain_body)
            if mentioned:
                self._mention_count += 1
                self._mention_log.append({
                    'time': self._format_clock(self._local_now()),
                    'room': f'#{room}' if room else '',
                    'from': f'{user}@{bbs}',
                    'body': plain_body[:200],
                })
            bell = '\x07' if mentioned else ''
            rendered = self._highlight_mentions(_pipe_to_ansi(body))
            # Format: |08[|11Nick|08]|07@bbs text  (anetmrc style with bbs suffix)
            await self._emit(
                f'{bell}'
                f'\x1b[1;30m[\x1b[1;36m{user}\x1b[1;30m]\x1b[2;37m@{bbs}\x1b[0m '
                f'{rendered}')
            return

        # Unknown event with body — show it
        if body:
            await self._emit(
                f'\x1b[1;30m[{evt}]\x1b[0m {_pipe_to_ansi(body)}')

    # ── Input: char-by-char with split-screen ────────────────────────────────

    async def _chat_loop(self):
        while self._connected:
            try:
                line = await self._read_chat_line()
            except Exception:
                break
            if line is None:
                break
            line = (line or '').strip()
            if not line:
                continue

            # Record every submitted line (commands and chat alike -- the
            # reference client's history recall doesn't distinguish) for
            # Ctrl+Up/Down recall. Skip an exact repeat of the last entry
            # so repeatedly hitting enter on the same line doesn't pad
            # the history with duplicates. Recall state resets on submit
            # regardless of what was being navigated.
            if not self._input_history or self._input_history[-1] != line:
                self._input_history.append(line)
            self._history_pos = None
            self._history_draft = ''

            # Any keypress resets AFK timer and clears away state locally.
            # Do NOT send STATUS AFK here — sending two rate-limited packets
            # back-to-back (this + the message) trips the bridge rate limiter.
            # The hub auto-clears AFK when the user sends a message; for an
            # explicit hub-side clear use /back.
            self._last_input_time = time.time()
            self._is_away = False

            if line.startswith('/'):
                if not await self._handle_slash(line):
                    break
            else:
                # Sending a message snaps view back to live (bottom) so the
                # user can see their own message echoed back from the server.
                if self._scroll_offset:
                    self._scroll_offset = 0
                    await self._redraw_chat_area()
                for i, chunk in enumerate(_split_for_wire(
                        line, cap=self._chat_wire_cap(),
                        repeat_prefix=self._current_color_pipe())):
                    if i:
                        await asyncio.sleep(WIRE_CHUNK_DELAY)
                    await self._send_json({
                        'type': 'send_message',
                        'room': self._room,
                        'message': chunk,
                    })

    async def _read_chat_line(self) -> str:
        reader = getattr(self.session, 'reader', None)
        if reader is None or not hasattr(reader, 'read'):
            self._split_screen = False
            return await self.session.read_line('')

        self._input_buf = []
        await self._draw_input_line()

        try:
            while True:
                ch = await reader.read(1)
                if not ch:
                    return ''

                if ch == b'\x1b':
                    seq = await self._read_escape_seq(reader)
                    if seq.endswith(b'A') and b';5' in seq:   # Ctrl+Up: older input-history entry
                        await self._history_recall(older=True)
                    elif seq.endswith(b'B') and b';5' in seq:  # Ctrl+Down: newer input-history entry
                        await self._history_recall(older=False)
                    elif seq in (b'[D', b'OD'):    # ← left arrow: cycle color left
                        await self._cycle_color(-1)
                    elif seq in (b'[C', b'OC'):  # → right arrow: cycle color right
                        await self._cycle_color(+1)
                    elif seq in (b'[A', b'OA'):  # ↑ up arrow: scroll up 1 line
                        await self._scroll_chat(1)
                    elif seq in (b'[B', b'OB'):  # ↓ down arrow: scroll down 1 line
                        await self._scroll_chat(-1)
                    # Other seqs (PgUp=b'[5', PgDn=b'[6' partial) swallowed
                    continue

                if ch in (b'\r', b'\n'):
                    line = ''.join(self._input_buf)
                    self._input_buf = []
                    await self._draw_input_line()
                    return line

                if ch in (b'\x08', b'\x7f'):
                    if self._input_buf:
                        self._input_buf.pop()
                        await self._draw_input_line()
                        await self._draw_status_line()
                    continue

                if ch == b'\x15':   # Ctrl-U: kill line
                    self._input_buf = []
                    await self._draw_input_line()
                    await self._draw_status_line()
                    continue

                if ch == b'\t':
                    await self._tab_complete()
                    continue

                if ch < b' ':
                    continue

                try:
                    c = ch.decode('utf-8', errors='replace')
                except Exception:
                    c = '?'

                async with self._input_lock:
                    self._input_buf.append(c)
                    await self._draw_input_line()
                    await self._draw_status_line()

        except (ConnectionError, OSError):
            return ''

    async def _read_escape_seq(self, reader, timeout=0.1) -> bytes:
        """Read the bytes after ESC.  Returns the sequence without the leading ESC.

        Handles:
          ESC [ A          → b'[A'      (cursor keys, single letter)
          ESC O A          → b'OA'      (alternate cursor keys)
          ESC [ 5 ~        → b'[5'      (PgUp — trailing ~ consumed and discarded)
          ESC [ 1 ; 5 A    → b'[1;5A'   (modified cursor, e.g. Ctrl+Up — kept
                                          in full so callers can tell modified
                                          arrows apart from plain ones and
                                          from each other; see Ctrl+Up/Down
                                          input-history recall in
                                          _read_chat_line)
        """
        try:
            second = await asyncio.wait_for(reader.read(1), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            return b''
        if not second:
            return b''
        if second not in (b'[', b'O'):
            return second
        try:
            third = await asyncio.wait_for(reader.read(1), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            return second
        if not third:
            return second
        seq = second + third
        # If third is a digit, this is a multi-byte sequence like
        # ESC [ 5 ~ (PgUp/PgDn/Del/...) or ESC [ 1 ; 5 A (modified cursor
        # key). Drain remaining bytes (up to 6) until we hit a byte that's
        # neither a digit nor ';'; that terminating byte tells them apart:
        # '~' means the legacy PgUp/PgDn-style form (return just the
        # original 2 bytes, matching prior behavior/existing callers), a
        # letter means a modified cursor key (return the full sequence so
        # the modifier is not lost).
        if third and third[0:1].isdigit():
            collected = b''
            for _ in range(6):
                try:
                    extra = await asyncio.wait_for(reader.read(1), timeout=timeout)
                except (asyncio.TimeoutError, Exception):
                    break
                if not extra:
                    break
                collected += extra
                if extra[0:1] not in b'0123456789;':
                    break
            if collected[-1:].isalpha():
                seq = second + third + collected
        return seq

    _TAB_MATCH_DISPLAY_CAP = 12  # don't flood the screen with every candidate

    async def _tab_complete(self):
        # NOTE: deliberately NOT wrapped in self._input_lock. _emit() (used
        # below for the no-match/multi-match feedback) calls
        # _redraw_chat_area() when in split-screen mode, which itself
        # acquires self._input_lock -- asyncio.Lock is not reentrant, so
        # holding the lock across an _emit() call here deadlocks the whole
        # session solid on the very next Tab press. Hit exactly this in
        # production (2026-07-04) from an earlier version of this function
        # that wrapped the entire body in the lock; the unit tests didn't
        # catch it because they all use _split_screen = False, which makes
        # _emit() take an early-return path that never touches the lock at
        # all -- see test_mrc_terminal_mentions.py's
        # TabCompleteDeadlockRegressionTests for a split_screen=True test
        # that actually exercises this path with a timeout.
        # No other coroutine mutates _input_buf concurrently with this one
        # (see the reader loop -- everything that touches it runs
        # sequentially from the same character-read loop), so this doesn't
        # need its own locking to be correct.
        cur = ''.join(self._input_buf)
        for sep in range(len(cur) - 1, -1, -1):
            if cur[sep].isspace():
                start = sep + 1; break
        else:
            start = 0
        prefix = cur[start:]
        if not prefix or not self._known_users:
            return
        pl = prefix.lower()
        matches = sorted(u for u in self._known_users if u.lower().startswith(pl))
        if not matches:
            await self._emit(
                f'\x1b[2;90mTab: no match for "{prefix}"\x1b[0m')
            return
        common = matches[0]
        for m in matches[1:]:
            lim = min(len(common), len(m))
            i = 0
            while i < lim and common[i].lower() == m[i].lower():
                i += 1
            common = common[:i]
        if len(matches) == 1:
            full   = matches[0]
            suffix = ': ' if start == 0 else ' '
            # Replace the typed prefix (possibly wrong case) with canonical case
            del self._input_buf[start:]
            for c in full + suffix:
                self._input_buf.append(c)
            await self._draw_input_line()
            return
        if len(common) > len(prefix):
            # Partial completion — replace typed prefix with canonical common prefix
            del self._input_buf[start:]
            for c in common:
                self._input_buf.append(c)
            await self._draw_input_line()
            return
        # Multiple candidates, none of which share more in common than
        # what's already typed -- list them so the user can pick, but
        # cap it. A busy room can have dozens of names sharing a short
        # prefix; dumping every single one is unreadable and doesn't
        # actually help narrow anything down.
        shown = matches[:self._TAB_MATCH_DISPLAY_CAP]
        more  = len(matches) - len(shown)
        tail  = f'  \x1b[2m(+{more} more, keep typing to narrow)\x1b[0m' if more else ''
        await self._emit(f'\x1b[2;96mTab: {", ".join(shown)}\x1b[0m{tail}')

    async def _cycle_color(self, delta: int):
        self._color_idx = (self._color_idx + delta) % len(_COLOR_SEQ)
        await self._draw_input_line()
        code = _COLOR_SEQ[self._color_idx]
        ansi = self._current_color_ansi()
        await self._emit(f'{ansi}>> text color\x1b[0m |{code}')
        # Persist to the profile (same 'typing_color' field the web
        # client's style dropdown reads/writes) so it's restored on the
        # next 'joined' event instead of resetting to default every
        # reconnect. Must go through _style_payload(), NOT a bare
        # {'typing_color': code} dict: mrc/bridge/main.py's
        # _handle_set_style falls back to the existing session value
        # for every OTHER field except prefix/suffix, which it hard-
        # defaults to '' when absent from the request -- sending only
        # typing_color here used to silently wipe any prefix/suffix
        # decoration the user had set via the web style panel, every
        # single time they cycled their outgoing color with the arrows.
        await self._send_json(self._style_payload(typing_color=code))

    async def _history_recall(self, older: bool):
        """Ctrl+Up/Down input-history recall. Plain Up/Down stay bound to
        chat-scroll (this file's existing convention) -- Ctrl+Up/Down was
        chosen instead of reusing them so the two don't collide.
        _history_pos is None while sitting on a fresh, not-yet-navigated
        line; the first Ctrl+Up stashes that line as _history_draft and
        jumps to the newest history entry, and stepping past the newest
        entry with Ctrl+Down restores the stashed draft."""
        if not self._input_history:
            return
        n = len(self._input_history)
        if self._history_pos is None:
            if not older:
                return
            self._history_draft = ''.join(self._input_buf)
            self._history_pos = n - 1
        elif older:
            if self._history_pos > 0:
                self._history_pos -= 1
        else:
            if self._history_pos < n - 1:
                self._history_pos += 1
            else:
                self._history_pos = None
                self._input_buf = list(self._history_draft)
                await self._draw_input_line()
                return
        self._input_buf = list(self._input_history[self._history_pos])
        await self._draw_input_line()

    def _current_color_ansi(self) -> str:
        return f'\x1b[{_PIPE_COLORS.get(_COLOR_SEQ[self._color_idx], "37")}m'

    def _current_color_pipe(self) -> str:
        return f'|{_COLOR_SEQ[self._color_idx]}'

    def _chat_wire_cap(self) -> int:
        """Max chars per outgoing room-chat chunk, leaving room for the
        display handle the bridge prepends before its hard 140-char cutoff."""
        return max(10, MAX_OUTGOING_CHARS - self._handle_overhead)

    def _dm_wire_cap(self) -> int:
        """Same as _chat_wire_cap() but for /t DMs (different wrapper prefix)."""
        return max(10, MAX_OUTGOING_CHARS - self._dm_overhead)

    def _action_wire_cap(self) -> int:
        """Same as _chat_wire_cap() but for /me actions (different, fixed-
        color wrapper prefix -- see _session_action_overhead in the bridge)."""
        return max(10, MAX_OUTGOING_CHARS - self._action_overhead)

    def _highlight_mentions(self, text: str) -> str:
        if not self._handle or not text:
            return text
        try:
            pat = re.compile(
                r'(?<!\w)(@?' + re.escape(self._handle) + r')(?!\w)',
                re.IGNORECASE)
            # Explicit fg/bg, not reverse-video -- same visibility fix as the
            # status-bar mention badge (_draw_status_line).
            return pat.sub(r'\x1b[1;37;41m\1\x1b[0m', text)
        except re.error:
            return text

    def _was_mentioned(self, text: str) -> bool:
        if not self._handle or not text:
            return False
        try:
            return bool(re.search(
                r'(?<!\w)@?' + re.escape(self._handle) + r'(?!\w)',
                text, re.IGNORECASE))
        except re.error:
            return False

    @staticmethod
    def _should_mask(buf: str) -> bool:
        if not buf:
            return False
        low = buf.lower()
        for pfx in ('/identify ', '/register ', '/update password ',
                    '/trust identify '):
            if low.startswith(pfx) and len(buf) > len(pfx):
                if pfx == '/register ':
                    return ' ' not in buf[len(pfx):]
                return True
        return False

    # ── Disconnect ───────────────────────────────────────────────────────────

    async def _disconnect(self):
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
        self._ping_task = None
        if self._ticker_task and not self._ticker_task.done():
            self._ticker_task.cancel()
        self._ticker_task = None
        if self._connected:
            try:
                await self._send_json({'type': 'leave_room'})
            except Exception:
                pass
            self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
        self._recv_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._aiohttp_session is not None:
            try:
                await self._aiohttp_session.close()
            except Exception:
                pass
            self._aiohttp_session = None

    def _caller_ip(self) -> str:
        """Real remote IP of the connecting telnet/SSH/rlogin caller,
        same extraction pattern anetbbs/core/session.py's own login flow
        already uses (writer.get_extra_info('peername') -> 'ip:port').

        Real bug this closes: the bridge's join_room handler never had
        ANY way to learn a terminal caller's real IP on its own -- this
        door's WebSocket connection to the bridge always dials
        ws://127.0.0.1:8080 (see this module's own DEFAULT_BRIDGE_URL),
        so from the bridge's side every terminal user looks identical
        (localhost). Without this, the reference client's own USERIP:
        packet (sent unconditionally on every join, right alongside its
        "/identify for MRC Trust" tip) could never be sent for terminal
        callers at all -- if the hub uses USERIP to recognize a
        returning already-identified connection, that would force a
        fresh /identify on literally every join, which is exactly what
        got reported live.
        """
        try:
            addr = self.session.writer.get_extra_info('peername')
        except Exception:
            return ''
        if not addr:
            return ''
        # Matches session.py's own defensive handling of this exact
        # call: normally a (host, port[, ...]) tuple for TCP, but
        # custom writer wrappers (ssh/rlogin) aren't guaranteed to
        # follow that -- guard rather than assume. USERIP wants the
        # host alone, not the port.
        try:
            return str(addr[0]) if isinstance(addr, tuple) else str(addr)
        except Exception:
            return ''

    async def _send_json(self, obj: dict):
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_str(json.dumps(obj))
        except Exception:
            self._connected = False

    async def _send_dm(self, target: str, body: str):
        """Send a direct message and locally echo it. Shared by /msg
        (and its aliases) and /r (reply-to-last-DM)."""
        for i, chunk in enumerate(_split_for_wire(body, cap=self._dm_wire_cap())):
            if i:
                await asyncio.sleep(WIRE_CHUNK_DELAY)
            await self._send_json({
                'type': 'direct_message',
                'to_user': target,
                'message': chunk,
            })
        # Local echo so sender sees their own DM in the chat + scrollback
        await self._emit(
            f'\x1b[1;35m[DM \x1b[0m\x1b[1;95m-> {target}\x1b[0m\x1b[1;35m] '
            f'\x1b[0m{body}\x1b[0m'
        )

    async def _download_chat_log(self):
        """/dlchatlog -- send the session's scrollback as a text file via
        whatever ZMODEM-family protocol is available, mirroring
        BBSMenuUI._ebook_download's tempfile-then-send_file pattern
        (anetbbs/features/bbs_ui.py). Exits split-screen for the
        transfer (a raw file-transfer handshake sharing the screen with
        an active DECSTBM scroll region + status/ticker/input rows is
        asking for visual corruption) and re-enters it afterward,
        repainting the chat history the same way _apply_prefs already
        does after a live ticker-toggle relayout."""
        import os
        import tempfile
        from .xfer import send_file, available_protocols

        protocols = available_protocols()
        if not protocols:
            await self._emit(
                '\x1b[33mNo file-transfer protocol available on this server '
                '(sysop needs to install lrzsz).\x1b[0m')
            return

        lines = [_strip_pipe(_ANSI_SEQ_RE.sub('', ln)) for ln in self._scrollback]
        content = '\r\n'.join(lines) + '\r\n'

        fd, path = tempfile.mkstemp(suffix='.txt', prefix='mrc_chatlog_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
            was_split = self._split_screen
            if was_split:
                await self._exit_split_screen()
            await self.session.write(
                f'\r\nStarting {protocols[0]} download: mrc_chatlog.txt\r\n')
            await send_file(self.session, path, protocol=protocols[0])
            if was_split:
                await self._enter_split_screen()
                await self._redraw_chat_area()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # ── Slash commands ───────────────────────────────────────────────────────

    async def _handle_slash(self, line: str) -> bool:
        parts = line.split(' ', 1)
        cmd  = parts[0].lower().lstrip('/')
        rest = parts[1].strip() if len(parts) > 1 else ''

        if cmd in ('quit', 'q', 'exit', 'leave'):
            await self._emit('\x1b[36mLeaving MRC...\x1b[0m')
            return False

        # '/help' asks the MRC hub for its own server-side help (was
        # '/helpserver'); '/helpserver' now shows this client's local
        # command reference (was '/help') -- swapped per sysop request
        # 2026-07-03. '/h' and '/?' stay bound to the local list, matching
        # their conventional meaning as a quick client-side reference.
        if cmd == 'help':
            await self._send_json({'type': 'server_cmd', 'command': 'HELPSERVER'})
            return True

        if cmd in ('helpserver', 'h', '?'):
            for ln in (
                '',
                '\x1b[1mMessaging\x1b[0m',
                '  /msg user text      private message  (aliases: /t /pm /dm /tell /w)',
                '  /r text             reply to your last received DM',
                '  /me action          emote (*action*)',
                '  /broadcast text     broadcast to all rooms (sysop, alias /b)',
                '  /ctcp user CMD      CTCP query (VERSION/TIME/PING/CLIENTINFO)',
                '',
                '\x1b[1mRooms\x1b[0m',
                '  /join room          switch room',
                '  /list  /rooms       list all rooms',
                '  /topic [text]       view or set topic',
                '  /topics             all room topics',
                '  /channel            show current room',
                '  /roompass <pw>      room password',
                '  /roomconfig [p v]   sysop room flags',
                '',
                '\x1b[1mPeople\x1b[0m',
                '  /who  /whoon        who is in this room',
                '  /chatters           who is online across all rooms',
                '  /users /userlist    user listing',
                '  /bbses /connected   connected BBSes',
                '  /lastseen [user]    last-seen lookup',
                '  /afk [msg]          mark away   /back  return',
                '',
                '\x1b[1mAuth (MRC Trust)\x1b[0m',
                '  /identify [pw]      log in (masked prompt if no arg)',
                '  /register pw [em]   register your handle',
                '  /update param val   change account field',
                '  /trust [INFO|...]    trust-list management',
                '',
                '\x1b[1mInfo\x1b[0m',
                '  /motd  /banners  /info [n]  /time  /version  /stats',
                '  /changelog  /routing  /quickstats  /help',
                '  /last [n]           recent chat history',
                '',
                '\x1b[1mClient\x1b[0m',
                '  [<]/[>] arrows       cycle outgoing text color',
                '  [up]/[dn] arrows     scroll chat 1 line',
                '  Ctrl+[up]/[dn]       recall previous/next sent line',
                '  Tab                 nick autocomplete',
                '  /mentions           show & clear mention log',
                '  /welcome            re-show the connect tips/status',
                '  /changes            recent client changes',
                '  /scroll [n]         scroll up n lines (default 5)',
                '  /scroll down [n]    scroll down n lines',
                '  /scroll 0           scroll to bottom (live view)',
                '  /clear              clear chat area (alias /cls)',
                '  /twit add|del|list|clear [user]   ignore-list management',
                '  /shield [on|off]    broadcast shield (blocks /broadcast)',
                '  /set [field value]  nick style, enter/leave/quit msgs, ticker, clockformat, tz',
                '  /set list           show current /set values',
                '  /dlchatlog          download this session\'s scrollback as text',
                '  /termsize w,h       report terminal size to server',
                '  /raw text           raw server command',
                '  /quit               leave MRC (alias /q)',
                '',
            ):
                await self._emit(ln)
            return True

        if cmd in ('msg', 't', 'tell', 'dm', 'pm', 'whisper', 'w'):
            parts2 = rest.split(' ', 1)
            if len(parts2) < 2 or not parts2[0]:
                await self._emit(f'Usage: /{cmd} user text')
                return True
            target, body = parts2
            await self._send_dm(target, body)
            return True

        if cmd == 'r':
            if not rest:
                await self._emit('Usage: /r <text>  (reply to last DM)')
                return True
            if not self._last_dm_from:
                await self._emit('\x1b[33mNo DM to reply to yet.\x1b[0m')
                return True
            await self._send_dm(self._last_dm_from, rest)
            return True

        if cmd in ('dlchatlog', 'dlchat', 'transcript'):
            if not self._scrollback:
                await self._emit('\x1b[33mNothing to download yet.\x1b[0m')
                return True
            await self._download_chat_log()
            return True

        if cmd == 'me':
            if not rest:
                await self._emit('Usage: /me <action>')
                return True
            # cap=self._action_wire_cap(), not the bare 140 -- the bridge
            # replaces this "* " marker with its own fixed-color wrapper
            # ("|15* |13{nick} ...|07") before this ever hits the wire,
            # which costs more than 2 chars. Uncapped, any action within
            # ~12+len(nick) chars of the limit had its tail silently
            # dropped server-side with no warning.
            for i, chunk in enumerate(_split_for_wire(rest, cap=self._action_wire_cap())):
                if i:
                    await asyncio.sleep(WIRE_CHUNK_DELAY)
                await self._send_json({
                    'type': 'send_message',
                    'room': self._room,
                    'message': f'* {chunk}',
                })
            return True

        if cmd in ('broadcast', 'b'):
            if not rest:
                await self._emit('Usage: /broadcast text  (sysop only)')
                return True
            if self._broadcast_shield:
                await self._emit(
                    '\x1b[33mBroadcast shield is on -- /shield off to send one.\x1b[0m')
                return True
            # cap accounts for the literal "BROADCAST " prefix the bridge
            # sends verbatim as part of Field7 -- same silent-truncation
            # risk as /me above if left unaccounted for.
            for i, chunk in enumerate(_split_for_wire(
                    rest, cap=MAX_OUTGOING_CHARS - len(_BROADCAST_PREFIX))):
                if i:
                    await asyncio.sleep(WIRE_CHUNK_DELAY)
                await self._send_json({
                    'type': 'server_cmd',
                    'command': f'{_BROADCAST_PREFIX}{chunk}',
                })
            return True

        if cmd == 'twit':
            sub_parts = rest.split(None, 1)
            sub = sub_parts[0].lower() if sub_parts else ''
            arg = sub_parts[1].strip() if len(sub_parts) > 1 else ''

            if sub in ('list', ''):
                users = sorted(self._twit_list)
                await self._emit(
                    f'\x1b[1;36mTwit list ({len(users)}):\x1b[0m '
                    + (', '.join(users) if users else '(empty)')
                    + f'  \x1b[2m[{self._twit_blocked_count} blocked this session]\x1b[0m')
                return True

            if sub == 'clear':
                await self._send_json({'type': 'set_prefs', 'twit_list': []})
                return True

            if sub in ('add', 'del', 'remove') and arg:
                target = arg.strip().lower()
                if target.upper() in ('SERVER', 'CLIENT', 'NOTME'):
                    await self._emit('\x1b[33mCannot twit a reserved name.\x1b[0m')
                    return True
                new_list = set(self._twit_list)
                if sub == 'add':
                    new_list.add(target)
                else:
                    new_list.discard(target)
                await self._send_json({
                    'type': 'set_prefs', 'twit_list': sorted(new_list)})
                return True

            await self._emit('Usage: /twit add|del|list|clear [user]')
            return True

        if cmd == 'shield':
            arg = (rest or '').strip().lower()
            if arg in ('on', 'off'):
                await self._send_json({
                    'type': 'set_prefs', 'broadcast_shield': arg == 'on'})
            else:
                state = 'on' if self._broadcast_shield else 'off'
                await self._emit(
                    f'\x1b[1;36mBroadcast shield:\x1b[0m {state}  '
                    f'\x1b[2m[{self._shield_blocked_count} blocked this session]\x1b[0m'
                    '  (usage: /shield on|off)')
            return True

        if cmd == 'set':
            parts2 = rest.split(None, 1)
            field = parts2[0].lower() if parts2 else ''
            value = parts2[1].strip() if len(parts2) > 1 else ''

            if field in ('', 'help'):
                for ln in (
                    '',
                    '\x1b[1m/set <field> <value>\x1b[0m   (also: /set list)',
                    '  prefix <text>       nick prefix decoration (max 16 chars)',
                    '  suffix <text>       nick suffix decoration (max 16 chars)',
                    '  color <00-15>       nick color (pipe code)',
                    '  entermsg <text>     shown on chat entry; {handle} placeholder',
                    '  leavemsg <text>     shown on chat leave; {handle} placeholder',
                    '  quitmsg <text>      appended to your leave message on /quit',
                    '  ticker <on|off>     scrolling ticker/banner (saved)',
                    '  clockformat <12|24> clock/timestamp hour format (saved)',
                    '  tz <zone|offset>    timestamp timezone: EST/CDT/PST/UTC/etc, or -5, +5:30',
                    '  twitfilter <on|off> master twit-list enforcement (saved)',
                    '  defaultroom <room>  room to auto-join on your next connect (saved)',
                    '  palette <name>      chrome color scheme (local only, not saved): '
                        + ', '.join(sorted(_TERM_PALETTES)),
                    '',
                ):
                    await self._emit(ln)
                return True

            if field == 'list':
                st = self._style
                for ln in (
                    f'\x1b[1;36mprefix:\x1b[0m {st.get("prefix") or "(none)"}',
                    f'\x1b[1;36msuffix:\x1b[0m {st.get("suffix") or "(none)"}',
                    f'\x1b[1;36mcolor:\x1b[0m {st.get("color") or "07"}',
                    f'\x1b[1;36mentermsg:\x1b[0m {self._enter_msg_tpl or "(default)"}',
                    f'\x1b[1;36mleavemsg:\x1b[0m {self._leave_msg_tpl or "(default)"}',
                    f'\x1b[1;36mquitmsg:\x1b[0m {self._quit_msg or "(none)"}',
                    f'\x1b[1;36mticker:\x1b[0m {"on" if self._show_ticker else "off"}',
                    f'\x1b[1;36mclockformat:\x1b[0m {self._clock_format}',
                    f'\x1b[1;36mtz:\x1b[0m {self._format_tz_offset()}',
                    f'\x1b[1;36mtwitfilter:\x1b[0m {"on" if self._twit_filter_enabled else "off"}',
                    f'\x1b[1;36mdefaultroom:\x1b[0m {self._default_room or "(none -- lands in lobby)"}',
                    f'\x1b[1;36mpalette:\x1b[0m {self._palette_name}',
                ):
                    await self._emit(ln)
                return True

            if field in ('prefix', 'suffix'):
                if not value:
                    await self._emit(f'Usage: /set {field} <text>')
                    return True
                await self._send_json(self._style_payload(**{field: value[:16]}))
                return True

            if field == 'color':
                if not value.isdigit() or not (0 <= int(value) <= 15):
                    await self._emit('Usage: /set color 00-15')
                    return True
                cc = value.zfill(2)
                await self._send_json(self._style_payload(
                    color=cc, prefix_color=cc, handle_color=cc, suffix_color=cc))
                return True

            if field in ('entermsg', 'leavemsg', 'quitmsg'):
                prefs_key = {'entermsg': 'enter_msg_tpl',
                            'leavemsg': 'leave_msg_tpl',
                            'quitmsg':  'quit_msg'}[field]
                await self._send_json({'type': 'set_prefs', prefs_key: value[:200]})
                return True

            if field == 'ticker':
                if value.lower() not in ('on', 'off'):
                    await self._emit('Usage: /set ticker on|off')
                    return True
                await self._send_json({
                    'type': 'set_prefs', 'ticker_enabled': value.lower() == 'on'})
                return True

            if field == 'clockformat':
                if value not in ('12', '24'):
                    await self._emit('Usage: /set clockformat 12|24')
                    return True
                await self._send_json({'type': 'set_prefs', 'clock_format': value})
                return True

            if field == 'tz':
                offset = _parse_tz_offset(value)
                if offset is None:
                    await self._emit(
                        'Usage: /set tz <zone|offset>  e.g. EST, CDT, PST, UTC, or -5, +5:30')
                    return True
                await self._send_json({'type': 'set_prefs', 'tz_offset': offset})
                return True

            if field == 'twitfilter':
                if value.lower() not in ('on', 'off'):
                    await self._emit('Usage: /set twitfilter on|off')
                    return True
                await self._send_json({
                    'type': 'set_prefs', 'twit_filter_enabled': value.lower() == 'on'})
                return True

            if field == 'defaultroom':
                if not value:
                    await self._emit('Usage: /set defaultroom <room>  (takes effect on your next connect)')
                    return True
                new_default = value.strip().lstrip('#').lower()
                await self._send_json({'type': 'set_prefs', 'default_room': new_default})
                return True

            if field == 'palette':
                name = value.lower()
                if not name:
                    names = ', '.join(sorted(_TERM_PALETTES))
                    await self._emit(f'Usage: /set palette <name>  ({names})')
                    await self._emit(
                        '\x1b[2m(original/minimal/bitchx/2leet4u/least are '
                        "inspired by StackFault's Mystic MRC themes, "
                        'bottomlessabyss.net)\x1b[0m')
                    return True
                if name not in _TERM_PALETTES:
                    await self._emit(
                        f'\x1b[33mUnknown palette:\x1b[0m {name}. '
                        f'Try: {", ".join(sorted(_TERM_PALETTES))}')
                    return True
                self._palette_name = name
                # _emit() below already cascades into a full
                # _redraw_chat_area() -> _draw_status_line(), recoloring
                # the room tag/clock/sidebar -- only the ticker line
                # needs an explicit redraw here since it's driven by its
                # own periodic loop, not touched by that cascade.
                await self._draw_ticker_line()
                await self._emit(f'\x1b[1;36mPalette:\x1b[0m {name}')
                return True

            await self._emit(f'\x1b[33mUnknown /set field:\x1b[0m {field}. Try /set help.')
            return True

        if cmd == 'join':
            if not rest:
                await self._emit('Usage: /join <room>')
                return True
            new_room = rest.strip().lstrip('#').lower()
            await self._send_json({
                'type': 'server_cmd',
                'command': f'JOIN {new_room}',
            })
            self._room = new_room
            await self._emit(f'\x1b[1;36mJoining #{new_room}...\x1b[0m')
            return True

        if cmd in ('list', 'rooms'):
            await self._send_json({'type': 'server_cmd', 'command': 'ROOMS'})
            return True

        if cmd == 'topics':
            await self._send_json({'type': 'server_cmd', 'command': 'TOPICS'})
            return True

        if cmd == 'channel':
            await self._send_json({'type': 'server_cmd', 'command': 'CHANNEL'})
            return True

        if cmd == 'info':
            await self._send_json({
                'type': 'server_cmd',
                'command': f'INFO {rest}' if rest else 'INFO',
            })
            return True

        for simple in ('users', 'userlist', 'bbses', 'connected', 'time',
                       'version', 'stats', 'changelog', 'routing',
                       'quickstats'):
            if cmd == simple:
                await self._send_json({
                    'type': 'server_cmd',
                    'command': simple.upper(),
                })
                return True

        if cmd in ('who', 'whoon'):
            await self._send_json({'type': 'server_cmd', 'command': 'WHOON'})
            return True

        if cmd == 'chatters':
            await self._send_json({'type': 'server_cmd', 'command': 'CHATTERS'})
            return True

        if cmd == 'motd':
            await self._send_json({'type': 'server_cmd', 'command': 'MOTD'})
            return True

        if cmd == 'banners':
            await self._send_json({'type': 'server_cmd', 'command': 'BANNERS'})
            return True

        if cmd == 'lastseen':
            await self._send_json({
                'type': 'server_cmd',
                'command': f'LASTSEEN {rest}' if rest else 'LASTSEEN',
            })
            return True

        if cmd == 'last':
            await self._send_json({
                'type': 'server_cmd',
                'command': f'LAST {rest}' if rest else 'LAST',
            })
            return True

        if cmd == 'afk':
            cmd_str = f'AFK {rest}' if rest else 'AFK'
            await self._send_json({'type': 'server_cmd', 'command': cmd_str})
            self._is_away = True
            await self._emit(
                f'\x1b[1;36m(afk{": " + rest if rest else ""})\x1b[0m')
            return True

        if cmd == 'back':
            await self._send_json({'type': 'server_cmd', 'command': 'BACK'})
            self._is_away = False
            self._last_input_time = time.time()
            await self._emit('\x1b[1;36m(back)\x1b[0m')
            return True

        if cmd == 'status':
            if not rest:
                await self._emit(
                    'Usage: /status <param> [value]   e.g. /status AFK gone fishing')
                return True
            await self._send_json({'type': 'server_cmd', 'command': f'STATUS {rest}'})
            return True

        if cmd == 'topic':
            await self._send_json({
                'type': 'server_cmd',
                'command': f'TOPIC {rest}' if rest else 'TOPIC',
            })
            return True

        if cmd == 'roompass':
            if not rest:
                await self._emit('Usage: /roompass <password>')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'ROOMPASS {rest}'})
            return True

        if cmd == 'roomconfig':
            await self._send_json({
                'type': 'server_cmd',
                'command': f'ROOMCONFIG {rest}' if rest else 'ROOMCONFIG',
            })
            return True

        if cmd == 'termsize':
            if not rest:
                await self._emit(
                    f'Usage: /termsize w,h  '
                    f'(currently {self._term_columns},{self._term_lines})')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'TERMSIZE {rest}'})
            return True

        if cmd == 'identify':
            password = rest
            if not password:
                read_pw = getattr(self.session, 'read_password', None)
                if callable(read_pw):
                    try:
                        password = await read_pw('MRC password: ')
                    except Exception:
                        password = await self.session.read_line('MRC password: ')
                else:
                    await self._emit(
                        '\x1b[33m(password will echo - type it and press Enter)\x1b[0m')
                    password = await self.session.read_line('MRC password: ')
                password = (password or '').strip()
            if not password:
                await self._emit('No password provided.')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'IDENTIFY {password}'})
            await self._emit('\x1b[1;36m(identify sent)\x1b[0m')
            return True

        if cmd == 'register':
            if not rest:
                await self._emit('Usage: /register <password> [email]')
                return True
            parts = rest.split(None, 1)
            pw    = parts[0][:20]
            email = parts[1] if len(parts) > 1 else ''
            cmd_str = f'REGISTER {pw}' + (f' {email}' if email else '')
            await self._send_json({'type': 'server_cmd', 'command': cmd_str})
            return True

        if cmd == 'update':
            if not rest:
                await self._emit('Usage: /update <param> <value>')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'UPDATE {rest}'})
            return True

        if cmd == 'trust':
            sub = rest or 'INFO'
            await self._send_json({
                'type': 'server_cmd', 'command': f'TRUST {sub}'})
            return True

        if cmd == 'ctcp':
            parts2 = rest.split(None, 1)
            if len(parts2) < 2:
                await self._emit(
                    'Usage: /ctcp <target> <cmd>   (VERSION/TIME/PING/CLIENTINFO)')
                return True
            await self._send_json({
                'type': 'server_cmd',
                'command': f'CTCP {parts2[0]} {parts2[1].upper()}',
            })
            return True

        if cmd == 'raw':
            if rest:
                await self._send_json({'type': 'server_cmd', 'command': rest})
            return True

        # ── Client-side commands ──

        if cmd == 'mentions':
            n   = self._mention_count
            log = list(self._mention_log)
            if not log:
                await self._emit('\x1b[1;36mMentions:\x1b[0m 0')
                self._mention_count = 0
                return True
            await self._emit(
                f'\x1b[1;36mMentions:\x1b[0m {n} unread '
                f'({len(log)} in log)')
            # Two lines per mention (short metadata header, then the message
            # body on its own indented, wrap-aware line) instead of one long
            # "time  room  from  body" line -- a single long line put the
            # body so far right that a normal 80-col terminal had almost no
            # room left for it, and _emit()'s wrap indent had no way to know
            # about the room/from columns baked into the line, so wrapped
            # continuation lines landed under the timestamp instead of under
            # the body text, looking misaligned/ragged.
            for m in log:
                room_bit = f' \x1b[1;36m{m["room"]}\x1b[0m' if m['room'] else ''
                await self._emit(
                    f'  \x1b[2m{m["time"]}\x1b[0m{room_bit}  '
                    f'\x1b[1m{m["from"]}\x1b[0m')
                body = m['body']
                if len(body) > 300:
                    body = body[:297] + '...'
                await self._emit(body, extra_indent='    ')
            await self._emit('\x1b[2m(cleared)\x1b[0m')
            self._mention_count = 0
            self._mention_log.clear()
            return True

        if cmd == 'welcome':
            await self._emit('')
            await self._emit(
                f'\x1b[32mYou are {self._handle} in #{self._room}.\x1b[0m')
            await self._emit(
                '\x1b[33mTip:\x1b[0m use [<] [>] arrow keys to change outgoing text color.')
            await self._emit(
                '\x1b[33mTip:\x1b[0m /identify <pass> if your handle is registered (MRC Trust).')
            await self._emit(
                '\x1b[33mTip:\x1b[0m /helpserver lists every command; /set help lists preferences.')
            if not self._twit_filter_enabled:
                twit_state = 'off'
            elif self._twit_list:
                twit_state = 'on'
            else:
                twit_state = 'on (empty list)'
            shield_state = 'on' if self._broadcast_shield else 'off'
            await self._emit(
                f'\x1b[2mTwit filter: {twit_state}   Broadcast shield: {shield_state}   '
                f'Terminal: {self._chat_width} cols\x1b[0m')
            return True

        if cmd == 'changes':
            for ln in (
                '',
                '\x1b[1mRecent MRC client changes\x1b[0m',
                '  - Status bar: clock replaced with real ping/latency to the MRC',
                '    hub itself (a prior build measured local loopback time instead)',
                '  - /set defaultroom, /set twitfilter, /set clockformat',
                '  - /welcome, /changes, /q, /b, /cls command aliases',
                '  - Nick-list sidebar, status bar (room/topic/mentions/latency)',
                '  - Scrolling ticker/banner fed by hub BANNER:/STATS: text',
                '  - /set (prefix/suffix/color/entermsg/leavemsg/quitmsg/ticker/tz/palette)',
                '  - /twit and /shield ignore/broadcast-shield lists',
                '  - /bbses and /info <n> BBS directory lookup',
                '  - Tab-complete on usernames seen in chat or via /who',
                '',
            ):
                await self._emit(ln)
            return True

        if cmd == 'scroll':
            arg = (rest or '').strip().lower()
            if arg in ('0', 'bottom', 'end', 'latest', 'live'):
                self._scroll_offset = 0
                await self._redraw_chat_area()
            elif arg.startswith('down'):
                n_str = arg[4:].strip()
                n = int(n_str) if n_str.strip().isdigit() else 5
                await self._scroll_chat(-n)
            else:
                # /scroll [n]  or  /scroll up [n]
                n_str = arg.replace('up', '').strip()
                n = int(n_str) if n_str.strip().isdigit() else 5
                await self._scroll_chat(n)
            return True

        if cmd in ('clear', 'cls'):
            self._display_lines.clear()
            self._scroll_offset = 0
            if self._split_screen:
                await self._redraw_chat_area()
                await self._draw_input_line()
            else:
                await self.session.write('\x1b[2J\x1b[H')
            return True

        if cmd in ('tab-users', 'tabusers'):
            users = sorted(self._known_users, key=str.lower)
            await self._emit(
                f'\x1b[1;36mTab pool ({len(users)}):\x1b[0m '
                + (', '.join(users) if users else '(empty)'))
            return True

        await self._emit(
            f'\x1b[33mUnknown command:\x1b[0m /{cmd}. Try /helpserver.')
        return True
