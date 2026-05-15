"""
Telnet/SSH/rlogin MRC chat menu — terminal-side counterpart to the existing
web MRC bridge at mrc/bridge/.

Architecture: the terminal client connects to the **same** local MRC bridge
that web users hit via /ws. The bridge holds a single TCP+trust connection
to the upstream hub (default mrc.bottomlessabyss.net) and fans out every
message to all attached websockets. Reusing the bridge means:
  - one BBS-level identity on the hub (trust state stays put)
  - web + terminal users on this BBS see each other's messages
  - no reconnect storms when terminal users come and go

Bridge wire format (JSON over websocket, see mrc/bridge/main.py):
    inbound  → {"type": "join_room"|"send_message"|"direct_message"|
                        "server_cmd"|"leave_room"|"ping"|"set_style", ...}
    outbound → {"type": "chat"|"system"|"private"|"action"|"error"|...,
                 "user": ..., "bbs": ..., "room": ..., "body": ...}

Slash commands mirror anetmrc helper_protocol.c: /msg /me /join /list
/who /trust /quit /help /raw.
"""
import asyncio
import json
import logging
import re
from collections import deque
from datetime import datetime

from .base_chat import BaseChatSystem
from ..core.protocols import SessionProtocol


# Outbound message hard cap — matches the MRC convention used by the
# bottomlessabyss.net hub. Anything longer is split into multiple frames
# with an "(2/3)"-style continuation tag so threads stay readable.
MAX_OUTGOING_CHARS = 140
SCROLLBACK_LINES = 500


# ──── Split-screen UI ────────────────────────────────────────────────
# We use DECSTBM (\x1b[<top>;<bottom>r) to reserve the bottom row for the
# input line so incoming messages never interleave with what the user is
# typing. Default geometry is 80×24 — sufficient for any classic BBS
# terminal. Sysop can override via TERM_LINES env / per-session NAWS.
TERM_LINES_DEFAULT = 24
# ASCII-only prompt so dumb terminals don't miscount columns. The colored
# swatch is built on the fly and reset (\x1b[0m) BEFORE the prompt so the
# prompt itself uses default attributes.
INPUT_PROMPT = '\x1b[0m\x1b[1;36m> \x1b[0m'

CSI = '\x1b['
SAVE_CURSOR = '\x1b7'      # DECSC
RESTORE_CURSOR = '\x1b8'   # DECRC


def _ansi_set_scroll_region(top: int, bottom: int) -> str:
    return f'{CSI}{top};{bottom}r'


def _ansi_reset_scroll_region() -> str:
    return f'{CSI}r'


def _ansi_cup(row: int, col: int = 1) -> str:
    return f'{CSI}{row};{col}H'


def _ansi_clear_line() -> str:
    """Clear the ENTIRE current line regardless of cursor column.
    `\\x1b[2K` is the standard for that; `\\x1b[K` (clear-to-EOL) was
    leaving artifacts on terminals where multi-byte chars confused the
    cursor-column tracking."""
    return f'{CSI}2K\r'


# Match ANSI SGR / cursor / clear / scroll escape sequences so we can count
# *visible* characters when truncating a line to fit the terminal width.
_ANSI_SEQ_RE = re.compile(r'\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[78])')


def _truncate_visible(text: str, max_visible: int) -> str:
    """Truncate `text` so its visible character count is <= max_visible.
    ANSI escape sequences embedded in the line don't count toward the
    visible length — we keep them all in case they're styling info."""
    if max_visible <= 0:
        return ''
    out = []
    visible = 0
    pos = 0
    while pos < len(text):
        m = _ANSI_SEQ_RE.match(text, pos)
        if m:
            out.append(m.group(0))
            pos = m.end()
            continue
        ch = text[pos]
        if visible >= max_visible:
            break
        out.append(ch)
        visible += 1
        pos += 1
    # Always reset SGR at the end so a truncated line doesn't bleed style
    # into the prompt below.
    return ''.join(out) + '\x1b[0m'


def _visible_len(text: str) -> int:
    """Count visible (printable) characters in `text`, skipping ANSI escapes."""
    n = 0
    pos = 0
    while pos < len(text):
        m = _ANSI_SEQ_RE.match(text, pos)
        if m:
            pos = m.end()
            continue
        n += 1
        pos += 1
    return n


def _word_wrap_ansi(text: str, width: int, indent: str = '') -> str:
    """Word-wrap an ANSI-bearing line at visible-width `width`.

    Inserts \r\n between words when adding the next word would overflow.
    Continuation lines start with `indent` (a fixed prefix string — typically
    blank padding so wrapped tails align under the original message body).
    Words longer than `width` are hard-broken (mid-word) as a last resort.
    """
    if width <= 0 or _visible_len(text) <= width:
        return text
    # Tokenise into runs of (whitespace) | (word with embedded ANSI).
    # Whitespace counts toward visible length; ANSI sequences inside a word
    # don't.
    tokens = re.split(r'(\s+)', text)
    lines = []
    cur_parts = []
    cur_visible = 0
    indent_len = len(indent)

    def _flush():
        if cur_parts:
            lines.append(''.join(cur_parts))

    for tok in tokens:
        if not tok:
            continue
        tok_visible = _visible_len(tok)
        if tok.isspace():
            # Don't start a wrapped line with leading whitespace.
            if not cur_parts:
                continue
            if cur_visible + tok_visible <= width:
                cur_parts.append(tok)
                cur_visible += tok_visible
            else:
                _flush()
                cur_parts = []
                cur_visible = indent_len
            continue
        # Word
        if cur_visible + tok_visible <= width:
            cur_parts.append(tok)
            cur_visible += tok_visible
        else:
            # Wrap before this word.
            _flush()
            # Hard-break a single oversized word.
            if indent_len + tok_visible > width:
                cur_parts = [indent + _truncate_visible(tok, width - indent_len)]
                lines.append(''.join(cur_parts))
                cur_parts = []
                cur_visible = indent_len
            else:
                cur_parts = [indent, tok]
                cur_visible = indent_len + tok_visible
    _flush()
    return '\r\n'.join(lines)


logger = logging.getLogger(__name__)


# Pipe-color → ANSI translator (Synchronet/MRC convention used by the
# bottomlessabyss.net hub). |NN where NN is 00-15 = foreground color.
_PIPE_COLORS = {
    '00': '30', '01': '34', '02': '32', '03': '36',
    '04': '31', '05': '35', '06': '33', '07': '37',
    '08': '90', '09': '94', '10': '92', '11': '96',
    '12': '91', '13': '95', '14': '93', '15': '97',
}
_PIPE_RE = re.compile(r'\|(\d{2})')

# ←/→ arrow keys cycle through this list to set the user's outgoing text
# color. Each entry is the Synchronet/MRC pipe-code (00..15). The pipe code
# is prepended to outgoing chat messages so other clients see colored text.
# The local input echo also uses the matching ANSI SGR for live preview.
_PIPE_COLOR_SEQUENCE = [
    '07',  # 7 = light grey (default — first because it's the BBS norm)
    '15', '14', '13', '12', '11', '10', '09',
    '08', '06', '05', '04', '03', '02', '01', '00',
]


def _pipe_to_ansi(s: str) -> str:
    """Translate Synchronet-style |NN pipe codes to ANSI SGR."""
    if not s:
        return ''
    def sub(m):
        return f'\x1b[{_PIPE_COLORS.get(m.group(1), "37")}m'
    return _PIPE_RE.sub(sub, s) + '\x1b[0m'


def _split_for_wire(text: str, cap: int = MAX_OUTGOING_CHARS):
    """Split *text* into chunks no longer than *cap* characters.

    If a single chunk fits, returns [text]. Otherwise splits on word
    boundaries when possible, hard-cuts only when a single word is
    longer than the cap, and tags every chunk with "(N/T)" so the
    receiving end can stitch them back. The "(N/T)" tag is included
    in the cap so the wire frame still fits."""
    text = (text or '').rstrip('\r\n')
    if len(text) <= cap:
        return [text] if text else []

    # First, naive split on word boundaries
    words = text.split(' ')
    chunks = []
    cur = ''
    # Reserve room for the tag — worst case "(99/99) " = 8 chars.
    inner_cap = cap - 8
    for w in words:
        # Hard-cut a word that's longer than the cap on its own
        while len(w) > inner_cap:
            if cur:
                chunks.append(cur)
                cur = ''
            chunks.append(w[:inner_cap])
            w = w[inner_cap:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= inner_cap:
            cur += ' ' + w
        else:
            chunks.append(cur)
            cur = w
    if cur:
        chunks.append(cur)

    if len(chunks) <= 1:
        return chunks
    total = len(chunks)
    return [f'({i+1}/{total}) {c}' for i, c in enumerate(chunks)]


class MRCChat(BaseChatSystem):

    DEFAULT_BRIDGE_URL = 'ws://127.0.0.1:8080/ws'
    DEFAULT_ROOM = 'lobby'

    def __init__(self, session: SessionProtocol):
        super().__init__(session)
        self._ws = None
        self._aiohttp_session = None
        self._recv_task = None
        self._connected = False
        self._handle = ''
        self._room = self.DEFAULT_ROOM
        self._topic = ''                # cached current room topic
        self._mention_count = 0         # bumped each time we're @-pinged
        # Detailed mention log: each entry is a dict
        #   {'time': 'HH:MM', 'room': '#lobby', 'from': 'Codefenix',
        #    'body': '@StingRay you around?'}
        # Capped at MENTION_LOG_MAX so we don't grow forever; /mentions
        # prints the list and clears it (matches anetmrc + web behavior).
        self._mention_log = deque(maxlen=50)
        # Set of nicks we've seen — fed by inbound chat events and the
        # /who, /chatters, /users responses. Used for Tab-complete.
        self._known_users = set()
        # Local scrollback for /scroll. Each entry is the rendered ANSI line
        # (already includes pipe→ANSI translation). Capped at SCROLLBACK_LINES.
        self._scrollback = deque(maxlen=SCROLLBACK_LINES)
        # Split-screen state. With term_lines=24:
        #   rows 1..22  = scrollable chat region
        #   row 23      = static separator line (─────)
        #   row 24      = static input line (> ...)
        # Separator and input MUST be different rows or they fight.
        self._term_lines = TERM_LINES_DEFAULT
        self._term_columns = 80
        # Initial layout values; overwritten by _enter_split_screen with
        # the live NAWS-negotiated terminal size. anetmrc-style:
        #   row 1                    = status bar
        #   rows 2..(N-1)            = chat scroll region (DECSTBM)
        #   row N                    = input line
        self._status_row = 1
        self._scroll_top = 2
        self._scroll_bottom = self._term_lines - 1
        self._input_row = self._term_lines
        self._latency_ms = None                      # populated by PONG handler
        self._input_buf = []                         # what user is typing
        self._input_lock = asyncio.Lock()
        self._split_screen = True                    # opt-out via /split off
        # User text-color cycling (←/→ arrow keys). Indexes into
        # _PIPE_COLOR_SEQUENCE which mirrors Synchronet's |07-style codes.
        self._color_idx = 7                          # 07 = white/grey default

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    async def show_menu(self):
        """Connect straight to MRC chat — no submenu. Users can switch
        rooms with /join from inside the chat session."""
        from flask import current_app
        bridge_url = self.DEFAULT_BRIDGE_URL
        try:
            url = current_app.config.get('MRC_BRIDGE_WS_URL')
            if url:
                bridge_url = url
            else:
                host = current_app.config.get('MRC_BRIDGE_HOST', 'localhost')
                port = current_app.config.get('MRC_BRIDGE_PORT', 8080)
                path = current_app.config.get('MRC_BRIDGE_WS_PATH', '/ws')
                scheme = ('wss' if current_app.config.get(
                    'MRC_BRIDGE_USE_SSL', False) else 'ws')
                if not path.startswith('/'):
                    path = '/' + path
                bridge_url = f'{scheme}://{host}:{port}{path}'
        except Exception:
            pass

        username = (self.session.user.get('username')
                    if isinstance(self.session.user, dict)
                    else getattr(self.session.user, 'username', None)) or 'guest'

        # MRC handles can't have spaces; underscore-collapse.
        suggested_handle = re.sub(r'\s+', '_', username)[:32]

        await self._connect_and_chat(
            bridge_url, suggested_handle, self.DEFAULT_ROOM)

    # ------------------------------------------------------------------
    # Connect + chat loop
    # ------------------------------------------------------------------

    async def _connect_and_chat(self, bridge_url, handle, room):
        try:
            import aiohttp
        except ImportError:
            await self.session.write(
                '\r\n\x1b[31maiohttp not installed — cannot reach the MRC '
                'bridge. Install it (pip install aiohttp).\x1b[0m\r\n')
            return

        self._handle = handle
        self._room = (room or self.DEFAULT_ROOM).strip().lstrip('#').lower()

        await self.session.write(
            f'\r\n\x1b[36mConnecting to MRC bridge at {bridge_url} ...\x1b[0m\r\n')

        self._aiohttp_session = aiohttp.ClientSession()
        try:
            try:
                self._ws = await asyncio.wait_for(
                    self._aiohttp_session.ws_connect(bridge_url),
                    timeout=10)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                await self.session.write(
                    f'\x1b[31mBridge connect failed: {exc}\x1b[0m\r\n'
                    'Is the anetbbs-mrc-bridge service running?\r\n')
                return

            self._connected = True
            await self._send_json({
                'type': 'join_room',
                'handle': self._handle,
                'room': self._room,
            })

            await self._enter_split_screen()
            await self._emit(
                f'\x1b[32mJoined #{self._room} as {self._handle}.\x1b[0m  '
                'Type /help for commands, /quit to leave.')
            await self._emit(
                '\x1b[33mTip:\x1b[0m type /identify (no password) for a '
                'masked password prompt.')

            self._recv_task = asyncio.create_task(self._recv_loop())
            await self._chat_loop()
        finally:
            await self._exit_split_screen()
            await self._disconnect()

    # ------------------------------------------------------------------
    # Split-screen helpers (DECSTBM-based)
    # ------------------------------------------------------------------

    async def _enter_split_screen(self):
        """anetmrc-style layout:

            row 1                    = status bar (room | topic | mentions | latency)
            rows 2..(N-1)            = chat scroll region (DECSTBM)
            row N                    = input line

        Detected dimensions come from the session's NAWS-negotiated
        window_size. Falls back to 80×24.
        """
        ws = getattr(self.session, 'window_size', None) or (80, 24)
        try:
            cols, lines = int(ws[0]), int(ws[1])
        except (TypeError, ValueError):
            cols, lines = 80, 24
        self._term_columns = max(40, min(240, cols))
        self._term_lines = max(10, min(120, lines))
        self._status_row = 1
        self._scroll_top = 2
        self._scroll_bottom = self._term_lines - 1
        self._input_row = self._term_lines

        # Reset any prior scroll region, fully clear screen, set new region.
        await self.session.write(
            '\x1b[r'                                            # reset DECSTBM
            '\x1b[H'                                            # cursor home
            '\x1b[2J'                                           # clear entire screen
            f'\x1b[{self._scroll_top};{self._scroll_bottom}r'   # scroll region rows 2..N-1
            f'\x1b[{self._scroll_bottom};1H'                    # cursor to bottom of region
        )
        await self._draw_status_line()
        await self._draw_input_line()

    async def _exit_split_screen(self):
        try:
            await self.session.write(
                '\x1b[r'                                        # reset scroll region
                f'\x1b[{self._term_lines};1H'                   # cursor to last row
                '\x1b[0m\r\n')
        except Exception:
            pass

    async def _draw_status_line(self):
        """Repaint the stationary status bar at row 1."""
        if not self._split_screen:
            return
        room = f'\x1b[1;36m[#{self._room}]\x1b[0m'
        topic = (f' \x1b[36m{self._topic}\x1b[0m' if self._topic else '')
        right_bits = []
        if self._mention_count:
            right_bits.append(f'\x1b[1;7;91m !{self._mention_count} \x1b[0m')
        latency_ms = getattr(self, '_latency_ms', None)
        if latency_ms is not None:
            right_bits.append(f'\x1b[2;37m{latency_ms}ms\x1b[0m')
        right = '  '.join(right_bits)

        left_visible = _visible_len(room) + _visible_len(topic)
        right_visible = _visible_len(right)
        gap = max(1, self._term_columns - left_visible - right_visible - 1)
        # Truncate topic if even with min gap we'd overflow.
        overflow = (left_visible + right_visible + 1) - self._term_columns
        if overflow > 0 and self._topic:
            new_topic = self._topic[:max(1, len(self._topic) - overflow - 1)] + '…'
            topic = f' \x1b[36m{new_topic}\x1b[0m'
            gap = 1

        # Write status row WITHOUT touching cursor save/restore — the
        # caller (_emit) will explicitly cup() to wherever it needs to be
        # next. This avoids cursor-position bugs from interleaved writes.
        await self.session.write(
            f'\x1b[1;1H'                            # to row 1, col 1
            f'\x1b[2K'                              # clear it
            f'\x1b[44m'                             # blue bg
            f'{room}{topic}'
            + ' ' * gap
            + f'{right}'
            f'\x1b[0m'                              # reset
        )

    async def _draw_input_line(self):
        """Repaint the input row (last row). Status bar at row 1 carries
        room/topic/mentions/latency, so this row only needs the prompt
        arrow and the user's typed buffer.

        The buffer is slide-windowed so it never wraps past the terminal
        edge — wrap remnants would survive _ansi_clear_line()."""
        if not self._split_screen:
            return
        text = ''.join(self._input_buf)
        if self._should_mask(text):
            for prefix in ('/identify ', '/register ', '/update password ',
                            '/trust identify '):
                if text.lower().startswith(prefix):
                    text = prefix + ('*' * (len(text) - len(prefix)))
                    break
        color_ansi = self._current_color_ansi()

        prompt = '\x1b[1;36m> \x1b[0m'
        prompt_visible_len = 2
        max_text = max(8, self._term_columns - prompt_visible_len - 1)
        if len(text) > max_text:
            text = '…' + text[-(max_text - 1):]

        await self.session.write(
            f'\x1b[{self._input_row};1H'
            f'\x1b[2K'
            + prompt
            + color_ansi + text + '\x1b[0m'
        )

    async def _disconnect(self):
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

    async def _send_json(self, obj: dict):
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_str(json.dumps(obj))
        except Exception:
            self._connected = False

    async def _emit(self, line: str):
        """Write a rendered line to both the terminal and the scrollback.

        Inline-redraw model (no DECSTBM scroll regions — too unreliable
        across BBS terminals). For each incoming line we:
          1. CR + clear current line  ← wipes any partial input prompt
          2. Write the chat line + CRLF
          3. Re-draw the input prompt + user's typed buffer
        Net effect: chat scrolls naturally; the prompt always appears
        below the most recent line. No fixed bottom row, no split.
        """
        self._scrollback.append(line)
        if not self._split_screen:
            await self.session.write(line + '\r\n')
            return

        # Build the timestamp prefix that anchors the left margin (matches
        # the format the reference anetmrc / NA-MRC clients use).
        ts = datetime.now().strftime('%H:%M ')
        ts_visible_len = len(ts)
        prefix = f'\x1b[2;37m{ts}\x1b[0m'      # dim grey
        indent = ' ' * ts_visible_len          # continuation lines align
        wrapped = _word_wrap_ansi(
            line, max(20, self._term_columns - ts_visible_len), indent=indent)
        out_text = prefix + wrapped

        async with self._input_lock:
            # Position cursor at the bottom row of the scroll region
            # (rows 2..N-1). Writing line + CRLF there scrolls within the
            # region only; status (row 1) and input (row N) are outside
            # the region and stay put.
            await self.session.write(
                f'\x1b[{self._scroll_bottom};1H'
                f'\x1b[2K'                                  # clear bottom row
                + out_text
                + '\r\n'
            )
            # Some terminals don't fully honour DECSTBM and let the CRLF
            # leak past the scroll region, leaving stranded prompts at
            # rows N..end-of-screen. Park cursor on the input row and
            # erase-to-end-of-screen — _draw_input_line below will paint
            # the prompt back on row N.
            await self.session.write(
                f'\x1b[{self._input_row};1H'
                '\x1b[J'                                    # erase from cursor to end
            )
            # Status may need updating (mention count bumped), and the
            # input row needs repainting + cursor parking back on input.
            await self._draw_status_line()
            await self._draw_input_line()

    def _highlight_mentions(self, text: str) -> str:
        """If our handle appears in `text`, wrap it in a brightred/inverse
        SGR so the user spots being @-mentioned. Matches `StingRay` AND
        `@StingRay` AND `[ANET]StingRay` (any non-word delimiter is fine).
        Case-insensitive. Returns the highlighted text — caller decides
        whether to bump the mention counter / bell."""
        if not self._handle or not text:
            return text
        try:
            pattern = re.compile(
                r'(?<!\w)(@?' + re.escape(self._handle) + r')(?!\w)',
                re.IGNORECASE)
            return pattern.sub(r'\x1b[1;7;91m\1\x1b[0m', text)
        except re.error:
            return text

    def _was_mentioned(self, text: str) -> bool:
        """True if our handle appears in *text* as a standalone word."""
        if not self._handle or not text:
            return False
        try:
            return bool(re.search(
                r'(?<!\w)@?' + re.escape(self._handle) + r'(?!\w)',
                text, re.IGNORECASE))
        except re.error:
            return False

    # ------------------------------------------------------------------
    # Receive loop — bridge events → terminal output
    # ------------------------------------------------------------------

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
                    await self.session.write(
                        '\r\n\x1b[33m*** Bridge closed the connection.\x1b[0m\r\n')
                    self._connected = False
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('MRC recv loop crashed')
            self._connected = False

    async def _handle_event(self, data: dict):
        evt = data.get('type', '')

        if evt == 'pong':
            # Bridge passes through the float epoch we put in the IMALIVE's
            # msgext; round-trip = now - that.
            try:
                t0 = float(data.get('msgext') or data.get('echo') or 0)
                if t0 > 0:
                    import time as _t
                    self._latency_ms = max(0, int((_t.time() - t0) * 1000))
                    await self._draw_status_line()
            except (TypeError, ValueError):
                pass
            return

        # Process protocol lines that carry actionable state (topic, room
        # confirmation), then suppress the raw form. Everything else in
        # NOISE_PREFIXES is dropped silently — use slash commands for
        # on-demand queries (/who → USERLIST, /banners → BANNER, /motd, ...)
        body_for_filter = (data.get('body') or data.get('message') or '')
        if isinstance(body_for_filter, str):
            stripped = body_for_filter.lstrip()
            head = stripped.upper()

            # ROOMTOPIC:<room>:<topic>   — store + announce the topic change
            if head.startswith('ROOMTOPIC:'):
                payload = stripped.split(':', 1)[1] if ':' in stripped else ''
                if ':' in payload:
                    room, _, topic = payload.partition(':')
                    self._topic = topic
                    if room.strip().lower() == self._room.lower():
                        await self._emit(
                            f'\x1b[1;7;36m── Topic for #{room}: '
                            f'{_pipe_to_ansi(topic)}\x1b[0m')
                return

            # USERROOM:<room>  — bridge confirms the room we're in
            if head.startswith('USERROOM:'):
                room = stripped.split(':', 1)[1].strip()
                if room and room.lower() != self._room.lower():
                    self._room = room
                    await self._emit(
                        f'\x1b[36m*** Now in #{room}\x1b[0m')
                return

            NOISE_PREFIXES = (
                'USERLIST:', 'USEROUT:', 'USERIN:',
                'USERNICK:', 'USERIP:',
                'BANNER:',          # /banners delivers these on demand
                'TYPING:',          # web sidebar typing indicator
                'CAPABILITIES:',    # bridge handshake echo
                'NEWROOM:',         # bridge translates JOIN → NEWROOM
                'NEWTOPIC:',        # bridge translates TOPIC → NEWTOPIC
                'KEEPALIVE:',
            )
            if head.startswith(NOISE_PREFIXES):
                return

        if evt == 'error':
            await self._emit(
                f'\x1b[31m*** Error: {data.get("message", "")}\x1b[0m')
            return

        if evt == 'system':
            await self._emit(_pipe_to_ansi(data.get('body', '')))
            return

        if evt == 'chatters' or evt == 'rooms':
            items = data.get('items') or []
            label = 'Online' if evt == 'chatters' else 'Rooms'
            if evt == 'chatters':
                # Each item may be 'nick' or 'nick@bbs' or 'nick (idle ...)'.
                # Pull the nick token off the front for tab-complete.
                for it in items:
                    nick = re.split(r'[\s@]', str(it).strip(), 1)[0]
                    if nick:
                        self._known_users.add(nick)
            await self._emit(
                f'\x1b[36m{label}:\x1b[0m {", ".join(items) or "(none)"}')
            return

        # Catch-all server-text events.
        if evt in ('mrc_message', 'message', 'notice', 'banner',
                   'whoon_line', 'rooms_line', 'motd', 'motd_line',
                   'server_text', 'info'):
            body = data.get('body', '') or data.get('message', '') or data.get('text', '')
            # CTCP — bridge relays these as plain mrc_message lines with
            # `[CTCP] sender target cmd` / `[CTCP-REPLY] target body`.
            # Format them so the user sees what's a query vs a reply,
            # mirroring anetmrc's display:
            #   |08[|14CTCP|08]|07 sender queried CMD
            #   |08[|14CTCP-REPLY|08]|07 from sender: payload
            plain = _PIPE_RE.sub('', body or '')
            if plain.startswith('[CTCP] '):
                payload = plain[len('[CTCP] '):].strip()
                # `sender target cmd [args]`
                pieces = payload.split(None, 3)
                if len(pieces) >= 3:
                    sender, target, ctcp_cmd = pieces[0], pieces[1], pieces[2]
                    args = pieces[3] if len(pieces) > 3 else ''
                    await self._emit(
                        f'\x1b[90m[\x1b[93mCTCP\x1b[90m]\x1b[0m '
                        f'\x1b[1m{sender}\x1b[0m queried '
                        f'\x1b[36m{target}\x1b[0m for '
                        f'\x1b[33m{ctcp_cmd.upper()}\x1b[0m'
                        + (f' \x1b[2m{args}\x1b[0m' if args else ''))
                    return
            if plain.startswith('[CTCP-REPLY] '):
                payload = plain[len('[CTCP-REPLY] '):].strip()
                # `target rest...` — target is who the reply is FOR
                pieces = payload.split(None, 1)
                if pieces:
                    rest_text = pieces[1] if len(pieces) > 1 else ''
                    await self._emit(
                        f'\x07\x1b[90m[\x1b[93mCTCP-REPLY\x1b[90m]\x1b[0m '
                        f'\x1b[1;36m{rest_text}\x1b[0m')
                    return
            # If a user field is present, track it; also fish a leading
            # `<nick>` / `[nick]` / `@nick` / `&nick<...` pattern out of
            # the formatted body so tab-complete works for messages that
            # arrive via the bridge's pre-formatted catch-all path.
            uname = data.get('user') or ''
            if uname and uname not in ('?', 'SERVER', 'CLIENT', 'NOTME'):
                self._known_users.add(uname)
            if body:
                # Strip pipe codes AND ANSI escapes so we can spot a
                # leading <nick>/[nick]/(nick)/&nick token regardless of
                # how the bridge styled the line.
                plain = _ANSI_SEQ_RE.sub('', _PIPE_RE.sub('', body))
                m = re.match(
                    r'^\s*[<\[\(&@]?\s*([A-Za-z][\w\-\.]{1,29})',
                    plain)
                if m:
                    nick = m.group(1)
                    if nick.upper() not in (
                            'PM', 'NOTICE', 'JOIN', 'PART', 'TIMEOUT',
                            'JOINING', 'TOPIC', 'TOPIC FOR', 'BROADCAST',
                            'WELCOME', 'STATUS'):
                        self._known_users.add(nick)
                await self._emit(_pipe_to_ansi(body))
            return

        user = data.get('user', '?')
        bbs = data.get('bbs', '?')
        body = data.get('body', '') or data.get('message', '')
        room = data.get('room', '')

        # Strip pipe codes for the mention check so |07StingRay still pings.
        plain_body = _PIPE_RE.sub('', body or '')

        # Track the sender for tab-completion (skips system pseudo-users).
        if user and user not in ('?', 'SERVER', 'CLIENT', 'NOTME'):
            self._known_users.add(user)

        # PMs always count as a "mention" — they're directed at us.
        if evt == 'private':
            self._mention_count += 1
            self._mention_log.append({
                'time': datetime.utcnow().strftime('%H:%M'),
                'room': '(PM)',
                'from': f'{user}@{bbs}',
                'body': plain_body[:200],
            })
            rendered = self._highlight_mentions(_pipe_to_ansi(body))
            await self._emit(
                f'\x07\x1b[33m[PM] <{user}@{bbs}>\x1b[0m {rendered}')
            return
        if evt == 'action':
            mentioned = self._was_mentioned(plain_body)
            if mentioned:
                self._mention_count += 1
                self._mention_log.append({
                    'time': datetime.utcnow().strftime('%H:%M'),
                    'room': f'#{room}' if room else '',
                    'from': f'{user}@{bbs}',
                    'body': '* ' + plain_body[:200],
                })
            bell = '\x07' if mentioned else ''
            await self._emit(
                f'{bell}\x1b[35m* {user}@{bbs} '
                f'{self._highlight_mentions(body)}\x1b[0m')
            return
        if evt == 'chat':
            mentioned = self._was_mentioned(plain_body)
            if mentioned:
                self._mention_count += 1
                self._mention_log.append({
                    'time': datetime.utcnow().strftime('%H:%M'),
                    'room': f'#{room}' if room else '',
                    'from': f'{user}@{bbs}',
                    'body': plain_body[:200],
                })
            bell = '\x07' if mentioned else ''
            room_label = f'#{room}' if room else ''
            rendered = self._highlight_mentions(_pipe_to_ansi(body))
            await self._emit(
                f'{bell}\x1b[36m{room_label}\x1b[0m '
                f'\x1b[1m<{user}@{bbs}>\x1b[0m {rendered}')
            return

        # Truly unknown event — debug fallback.
        if body:
            await self._emit(
                f'\x1b[90m[{evt}]\x1b[0m {_pipe_to_ansi(body)}')

    # ------------------------------------------------------------------
    # Main input loop + slash commands
    # ------------------------------------------------------------------

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
            if line.startswith('/'):
                if not await self._handle_slash(line):
                    break
            else:
                # Plain text → send to the current room, capped + auto-split.
                # Prepend the current pipe-color so other clients see colored
                # text. ←/→ arrow keys change the color.
                colored = self._current_color_pipe() + line
                for chunk in _split_for_wire(colored):
                    await self._send_json({
                        'type': 'send_message',
                        'room': self._room,
                        'message': chunk,
                    })

    async def _tab_complete(self):
        """Tab-complete the trailing word against `_known_users`.

        - 1 match → replace prefix with full nick. Append ': ' if it's at
          the start of the line (you're addressing them), else a space.
        - >1 match → emit the candidates on a notice line; if they share a
          longer common prefix than what's typed, extend to that.
        - 0 matches → no-op.
        """
        cur = ''.join(self._input_buf)
        # Find the start of the trailing word (after the last whitespace).
        for sep_idx in range(len(cur) - 1, -1, -1):
            if cur[sep_idx].isspace():
                start = sep_idx + 1
                break
        else:
            start = 0
        prefix = cur[start:]
        if not prefix or not self._known_users:
            return
        prefix_l = prefix.lower()
        matches = sorted(
            u for u in self._known_users if u.lower().startswith(prefix_l))
        if not matches:
            return
        # Common prefix across all matches (case-preserving from first match).
        common = matches[0]
        for m in matches[1:]:
            limit = min(len(common), len(m))
            i = 0
            while i < limit and common[i].lower() == m[i].lower():
                i += 1
            common = common[:i]

        if len(matches) == 1:
            full = matches[0]
            suffix = ': ' if start == 0 else ' '
            tail = full[len(prefix):] + suffix
            for c in tail:
                self._input_buf.append(c)
            await self._draw_input_line()
            return

        # Multiple — extend to common prefix if longer than what was typed.
        if len(common) > len(prefix):
            tail = common[len(prefix):]
            for c in tail:
                self._input_buf.append(c)
            await self._draw_input_line()
            return

        # Already at common-prefix max — show options inline.
        await self._emit(
            f'\x1b[2;36mTab: {", ".join(matches)}\x1b[0m')

    async def _read_chat_line(self) -> str:
        """Char-by-char input on the bottom-row input line. Coordinates
        with _emit() via self._input_lock so inbound writes don't shred
        the cursor position. Handles password masking + ←/→ color cycling.

        Arrow keys arrive as ESC [ A/B/C/D (3 bytes). We swallow Up/Down
        but use Left/Right to cycle the user's outgoing text color
        through _PIPE_COLOR_SEQUENCE."""
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

                # Escape sequence (arrow keys are ESC [ A/B/C/D)
                if ch == b'\x1b':
                    seq = await self._read_escape_seq(reader)
                    if seq in (b'[D', b'OD'):     # Left
                        await self._cycle_color(-1)
                    elif seq in (b'[C', b'OC'):   # Right
                        await self._cycle_color(+1)
                    # Up/Down + other ESC seqs swallowed (no history yet)
                    continue

                # Enter
                if ch in (b'\r', b'\n'):
                    line = ''.join(self._input_buf)
                    self._input_buf = []
                    await self._draw_input_line()
                    return line
                # Backspace / DEL
                if ch in (b'\x08', b'\x7f'):
                    if self._input_buf:
                        self._input_buf.pop()
                        await self._draw_input_line()
                    continue
                # Ctrl-U → kill line
                if ch == b'\x15':
                    self._input_buf = []
                    await self._draw_input_line()
                    continue
                # Tab → autocomplete the trailing word against known users.
                if ch == b'\t':
                    await self._tab_complete()
                    continue
                # Other control chars dropped
                if ch < b' ':
                    continue

                try:
                    c = ch.decode('utf-8', errors='replace')
                except Exception:
                    c = '?'

                async with self._input_lock:
                    self._input_buf.append(c)
                    # Always redraw via _draw_input_line so the slide-window
                    # protects against terminal wrap. Single-char echo would
                    # let long input wrap to a second screen row, which a
                    # later _emit() can't fully clean up — produces ghost
                    # duplicates of the typed text on the line above.
                    await self._draw_input_line()
        except (ConnectionError, OSError):
            return ''

    async def _read_escape_seq(self, reader, timeout=0.1) -> bytes:
        """Read up to 4 bytes of an ESC-prefixed sequence. Returns the
        bytes after the ESC (e.g. b'[D' for Left arrow). Empty bytes
        means a bare ESC — we ignore it."""
        try:
            second = await asyncio.wait_for(reader.read(1), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            return b''
        if not second:
            return b''
        if second not in (b'[', b'O'):
            return second  # something we don't care about
        try:
            third = await asyncio.wait_for(reader.read(1), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            return second
        return second + (third or b'')

    async def _cycle_color(self, delta: int):
        """Advance (or rewind) the outgoing-color index and re-paint the
        input line so the user sees the new color immediately."""
        n = len(_PIPE_COLOR_SEQUENCE)
        self._color_idx = (self._color_idx + delta) % n
        await self._draw_input_line()
        # Brief notice of which color is now selected
        code = _PIPE_COLOR_SEQUENCE[self._color_idx]
        ansi = self._current_color_ansi()
        await self._emit(f'{ansi}■\x1b[0m text color set to |{code}')

    def _current_color_ansi(self) -> str:
        code = _PIPE_COLOR_SEQUENCE[self._color_idx]
        return f'\x1b[{_PIPE_COLORS.get(code, "37")}m'

    def _current_color_pipe(self) -> str:
        return f'|{_PIPE_COLOR_SEQUENCE[self._color_idx]}'

    @staticmethod
    def _should_mask(buf: str) -> bool:
        """Return True iff the *most recently typed character* in `buf`
        should be echoed as `*`.

        Important: the trigger space itself is NOT masked. We mask only
        characters typed AFTER the prefix, so the user sees:
            /identify ******
        not
            /identify******
        Hence the strict `len(buf) > len(prefix)` check below.
        """
        if not buf:
            return False
        low = buf.lower()

        # /identify <pass> — mask only the chars typed after the space.
        prefix = '/identify '
        if low.startswith(prefix) and len(buf) > len(prefix):
            return True

        # /register <pass> [email] — mask the first token only; once the
        # next space is typed we stop masking so the email shows.
        prefix = '/register '
        if low.startswith(prefix) and len(buf) > len(prefix):
            after = buf[len(prefix):]
            return ' ' not in after

        # /update password <value> — mask the value only.
        prefix = '/update password '
        if low.startswith(prefix) and len(buf) > len(prefix):
            return True

        # /trust identify <pass> — long-form, kept for symmetry.
        prefix = '/trust identify '
        if low.startswith(prefix) and len(buf) > len(prefix):
            return True

        return False

    async def _handle_slash(self, line: str) -> bool:
        parts = line.split(' ', 1)
        cmd = parts[0].lower().lstrip('/')
        rest = parts[1].strip() if len(parts) > 1 else ''

        if cmd in ('quit', 'exit', 'leave'):
            await self.session.write('\r\nLeaving MRC chat...\r\n')
            return False

        if cmd in ('help', 'h', '?'):
            for ln in (
                '',
                '\x1b[1mMessaging\x1b[0m',
                '  /msg user text       private message  (aliases: /t /dm /pm /tell /whisper /w)',
                '  /me action           emote',
                '  /broadcast text      broadcast to every room (sysop privilege)',
                '  /ctcp user CMD       VERSION / TIME / PING / CLIENTINFO query',
                '',
                '\x1b[1mRooms\x1b[0m',
                '  /join room           switch room  /list, /rooms  list rooms',
                '  /topic [text]        view or set current room topic',
                '  /topics              all rooms with topics',
                '  /roompass <pass>     set / join with a room password',
                '  /channel             show your current room',
                '',
                '\x1b[1mPeople\x1b[0m',
                '  /who, /chatters      who is online',
                '  /users               who is online (compact)',
                '  /userlist            who, with their bbs',
                '  /bbses               BBSes connected to the hub',
                '  /lastseen [user]     when was someone last seen',
                '  /afk [msg]           mark yourself away  /back  return',
                '  /status param [val]  set server status flags (AFK/LASTSEEN)',
                '',
                '\x1b[1mAuth (MRC Trust)\x1b[0m',
                '  /identify [pass]     log in (prompts masked if no arg)',
                '  /register pass [em]  register your handle',
                '  /update param value  change a Trust account param',
                '  /trust [INFO|ADD|REM|LIST]  trust-list management',
                '',
                '\x1b[1mInfo\x1b[0m',
                '  /motd /banners /info /time /version /stats',
                '  /changelog /routing /quickstats /helpserver',
                '  /last [n]            recent activity',
                '',
                '\x1b[1mClient\x1b[0m',
                '  Tab                  autocomplete a username from current room',
                '  /mentions            show & clear unread-mention counter',
                '  /split on|off        split-screen toggle (default on)',
                '  /clear               clear the scroll region',
                '  /termsize w,h        send your terminal size to the server',
                '  /roomconfig p [v]    sysop room flags (TOPICLOCK/JOINPART/PASSWORD)',
                '  /raw text            send a raw server command (advanced)',
                '  /quit                leave',
                '',
            ):
                await self._emit(ln)
            return True

        if cmd in ('msg', 't', 'tell', 'dm', 'pm', 'whisper', 'w'):
            target_text = rest.split(' ', 1)
            if len(target_text) < 2 or not target_text[0]:
                await self._emit(f'Usage: /{cmd} user text')
                return True
            target, body = target_text
            for chunk in _split_for_wire(body):
                # Bridge expects `to_user` (mrc/bridge/main.py:1080), not `to`.
                # Our web/C clients use `to_user` — terminal had it wrong.
                await self._send_json({
                    'type': 'direct_message',
                    'to_user': target,
                    'message': chunk,
                })
            return True

        if cmd == 'me':
            if not rest:
                await self._emit('Usage: /me <action>')
                return True
            for chunk in _split_for_wire(rest):
                await self._send_json({
                    'type': 'send_message',
                    'room': self._room,
                    'message': f'\x01ACTION {chunk}\x01',
                })
            return True

        if cmd == 'broadcast':
            if not rest:
                await self._emit('Usage: /broadcast text  (sysop only)')
                return True
            for chunk in _split_for_wire(rest):
                await self._send_json({
                    'type': 'server_cmd',
                    'command': f'BROADCAST {chunk}',
                })
            return True

        if cmd == 'join':
            if not rest:
                await self.session.write('Usage: /join <room>\r\n')
                return True
            new_room = rest.strip().lstrip('#').lower()
            # IMPORTANT: room switching uses `server_cmd JOIN <room>` (which
            # the bridge translates to NEWROOM:). DO NOT re-send the
            # `join_room` envelope here — that's the bridge's initial
            # handshake-with-handle and re-sending it resets the session
            # state to `waiting_for_identify=True`, undoing the user's
            # /identify. The C client (anetmrc helper_protocol.c:2666)
            # also sends NEWROOM, not a fresh handshake.
            await self._send_json({
                'type': 'server_cmd',
                'command': f'JOIN {new_room}',
            })
            self._room = new_room
            await self.session.write(
                f'\x1b[36mJoining #{new_room}...\x1b[0m\r\n')
            return True

        if cmd in ('list', 'rooms'):
            await self._send_json({
                'type': 'server_cmd',
                'command': 'ROOMS',
            })
            return True

        if cmd == 'topics':
            await self._send_json({
                'type': 'server_cmd', 'command': 'TOPICS'})
            return True

        if cmd == 'channel':
            await self._send_json({
                'type': 'server_cmd', 'command': 'CHANNEL'})
            return True

        # /info <id> — fetch a connected BBS's INFO* details. The id comes
        # from the CONNECTED / /bbses listing.
        if cmd == 'info':
            cmd_str = f'INFO {rest}' if rest else 'INFO'
            await self._send_json({
                'type': 'server_cmd', 'command': cmd_str})
            return True

        # All the read-only info commands map to the same simple verb.
        for plain_cmd in ('users', 'userlist', 'bbses', 'time',
                           'version', 'stats', 'changelog', 'routing',
                           'quickstats', 'helpserver'):
            if cmd == plain_cmd:
                await self._send_json({
                    'type': 'server_cmd',
                    'command': plain_cmd.upper(),
                })
                return True

        if cmd == 'lastseen':
            cmd_str = f'LASTSEEN {rest}' if rest else 'LASTSEEN'
            await self._send_json({
                'type': 'server_cmd', 'command': cmd_str})
            return True

        if cmd == 'last':
            cmd_str = f'LAST {rest}' if rest else 'LAST'
            await self._send_json({
                'type': 'server_cmd', 'command': cmd_str})
            return True

        if cmd in ('who', 'whoon', 'chatters'):
            await self._send_json({
                'type': 'server_cmd',
                'command': 'WHOON',
            })
            return True

        if cmd == 'motd':
            await self._send_json({
                'type': 'server_cmd', 'command': 'MOTD'})
            return True

        # /afk [message]   — STATUS AFK [message]   (1.3 helper)
        # /back            — STATUS AFK            (no value clears it)
        # Per MRCDoc 1.26: STATUS sets server-side user state. The AFK
        # parameter shows in CHATTERS/WHOON output until cleared.
        if cmd == 'afk':
            cmd_str = f'STATUS AFK {rest}' if rest else 'STATUS AFK'
            await self._send_json({
                'type': 'server_cmd', 'command': cmd_str})
            await self.session.write(
                f'\x1b[36m(afk{": " + rest if rest else ""})\x1b[0m\r\n')
            return True
        if cmd == 'back':
            await self._send_json({
                'type': 'server_cmd', 'command': 'STATUS AFK'})
            await self.session.write('\x1b[36m(back)\x1b[0m\r\n')
            return True

        # /status <param> [value]  — generic STATUS pass-through.
        if cmd == 'status':
            if not rest:
                await self.session.write(
                    'Usage: /status <param> [value]\r\n'
                    '       /status AFK [message]   /status LASTSEEN ON|OFF\r\n')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'STATUS {rest}'})
            return True

        # /roomconfig <param> [value]  — set per-room flags (sysop / trusted).
        if cmd == 'roomconfig':
            cmd_str = f'ROOMCONFIG {rest}' if rest else 'ROOMCONFIG'
            await self._send_json({
                'type': 'server_cmd', 'command': cmd_str})
            return True

        # /termsize <w>,<h>  — tell the server our terminal size for layout.
        if cmd == 'termsize':
            if not rest:
                await self.session.write(
                    f'Usage: /termsize <w>,<h>   '
                    f'(currently {self._term_columns},{self._term_lines})\r\n')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'TERMSIZE {rest}'})
            return True

        if cmd == 'banners':
            await self._send_json({
                'type': 'server_cmd', 'command': 'BANNERS'})
            return True

        if cmd == 'topic':
            cmd_str = f'TOPIC {rest}' if rest else 'TOPIC'
            await self._send_json({
                'type': 'server_cmd', 'command': cmd_str})
            return True

        if cmd == 'roompass':
            if not rest:
                await self.session.write('Usage: /roompass <password>\r\n')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'ROOMPASS {rest}'})
            return True

        if cmd == 'identify':
            password = rest
            if not password:
                # Prompt with masked echo if the session supports it; fall
                # back to a normal read_line otherwise (with a warning).
                read_password = getattr(
                    self.session, 'read_password', None)
                if callable(read_password):
                    try:
                        password = await read_password('MRC password: ')
                    except Exception:
                        password = await self.session.read_line(
                            'MRC password (visible): ')
                else:
                    await self.session.write(
                        '\x1b[33m(no masked-input support; password '
                        'will echo. Press Enter to skip.)\x1b[0m\r\n')
                    password = await self.session.read_line(
                        'MRC password: ')
                password = (password or '').strip()
            if not password:
                await self.session.write('No password provided.\r\n')
                return True
            # IDENTIFY is a top-level server verb per the MRC protocol spec
            # (bbswiki MRCDoc:MRC_Protocol, 8 apr 2023 changelog) — NOT a
            # TRUST subcommand. The hub returns "Invalid format for TRUST"
            # when prefixed.
            await self._send_json({
                'type': 'server_cmd',
                'command': f'IDENTIFY {password}',
            })
            await self.session.write('\x1b[36m(identify sent)\x1b[0m\r\n')
            return True

        if cmd == 'register':
            if not rest:
                await self.session.write(
                    'Usage: /register <password> [email]\r\n')
                return True
            # REGISTER is also a top-level verb (alongside IDENTIFY/UPDATE).
            await self._send_json({
                'type': 'server_cmd', 'command': f'REGISTER {rest}'})
            return True

        if cmd == 'update':
            # UPDATE param value — change account params (e.g. password, email).
            if not rest:
                await self.session.write(
                    'Usage: /update <param> <value>   (e.g. /update password newpass)\r\n')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'UPDATE {rest}'})
            return True

        if cmd == 'trust':
            # TRUST is a separate verb for the trust-LIST subcommands
            # (INFO/ADD/REM/LIST), distinct from IDENTIFY/REGISTER/UPDATE.
            sub = rest or 'INFO'
            await self._send_json({
                'type': 'server_cmd', 'command': f'TRUST {sub}'})
            return True

        if cmd == 'raw':
            if rest:
                await self._send_json({
                    'type': 'server_cmd', 'command': rest})
            return True

        if cmd == 'ctcp':
            # /ctcp <target> <CMD>  — VERSION/TIME/PING/CLIENTINFO
            # Bridge expects this as a server_cmd: "CTCP <target> <cmd>".
            # Reference: mrc/bridge/main.py:1132 (CTCP normalization).
            parts = rest.split(None, 1)
            if len(parts) < 2:
                await self._emit(
                    'Usage: /ctcp <target> <command>'
                    '   (VERSION / TIME / PING / CLIENTINFO)')
                return True
            await self._send_json({
                'type': 'server_cmd',
                'command': f'CTCP {parts[0]} {parts[1].upper()}',
            })
            return True

        # ---- Client-side commands (don't touch the wire) ----------

        if cmd == 'scroll':
            await self._emit(
                '\x1b[33m/scroll has been removed — use your terminal\'s '
                'native scrollback (Shift+PgUp / mouse wheel).\x1b[0m')
            return True

        # Diagnostic: dump the tab-completion set so we can tell whether
        # tracking is keeping up with the room.
        if cmd in ('tab-users', 'tabusers'):
            users = sorted(self._known_users, key=str.lower)
            await self._emit(
                f'\x1b[36mTab-complete pool ({len(users)}):\x1b[0m '
                + (', '.join(users) if users else '(empty)'))
            return True

        if cmd == 'mentions':
            n = self._mention_count
            log = list(self._mention_log)
            if not log:
                await self._emit('\x1b[36mMentions:\x1b[0m 0')
                self._mention_count = 0
                return True
            # Header
            await self._emit(
                f'\x1b[36mMentions:\x1b[0m {n} unread '
                f'({len(log)} in log) — \x1b[2mtime  room  from  message\x1b[0m')
            for m in log:
                # Trim body to fit a reasonable width but keep enough
                # context to recognize what was said.
                body = m['body']
                if len(body) > 100:
                    body = body[:97] + '...'
                room_disp = m['room'] or ''
                await self._emit(
                    f'  \x1b[2m{m["time"]}\x1b[0m  '
                    f'\x1b[36m{room_disp:<10}\x1b[0m  '
                    f'\x1b[1m{m["from"]:<24}\x1b[0m  {body}')
            await self._emit('\x1b[2m(cleared)\x1b[0m')
            self._mention_count = 0
            self._mention_log.clear()
            return True

        if cmd == 'split':
            arg = (rest or '').lower()
            if arg in ('on', 'enable', 'true', '1'):
                self._split_screen = True
                await self._enter_split_screen()
                await self._emit('Split-screen mode \x1b[32mon\x1b[0m.')
            elif arg in ('off', 'disable', 'false', '0'):
                await self._exit_split_screen()
                self._split_screen = False
                await self.session.write(
                    'Split-screen mode \x1b[31moff\x1b[0m.\r\n')
            else:
                await self._emit(
                    f'Split-screen is currently '
                    f'{"on" if self._split_screen else "off"}. '
                    'Usage: /split on|off')
            return True

        if cmd == 'clear':
            if self._split_screen:
                # Clear scroll region only — leave separator + input alone.
                await self.session.write(
                    SAVE_CURSOR
                    + _ansi_cup(1, 1)
                    + (CSI + '2K\n') * (self._scroll_bottom - 1)
                    + RESTORE_CURSOR)
                await self._draw_input_line()
            else:
                await self.session.write(f'{CSI}2J{CSI}H')
            return True

        await self._emit(
            f'\x1b[33mUnknown command:\x1b[0m /{cmd}. Try /help.')
        return True
