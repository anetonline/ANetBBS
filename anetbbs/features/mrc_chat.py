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
from datetime import datetime

from .base_chat import BaseChatSystem
from ..core.protocols import SessionProtocol


MAX_OUTGOING_CHARS = 140        # MRC hub hard limit
SCROLLBACK_LINES   = 500        # local ring buffer
PING_INTERVAL      = 60         # seconds between WS pings
AWAY_AFTER         = 600        # seconds idle before IAMHERE AWAY

logger = logging.getLogger(__name__)

# ── ANSI helpers ────────────────────────────────────────────────────────────

_ANSI_SEQ_RE = re.compile(r'\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[78])')

# Pipe-color → ANSI SGR (Synchronet/MRC |NN convention)
_PIPE_COLORS = {
    '00': '30', '01': '34', '02': '32', '03': '36',
    '04': '31', '05': '35', '06': '33', '07': '37',
    '08': '90', '09': '94', '10': '92', '11': '96',
    '12': '91', '13': '95', '14': '93', '15': '97',
}
_PIPE_RE = re.compile(r'\|(\d{2})')

# ← → arrow keys cycle through these pipe codes for outgoing text color
_COLOR_SEQ = ['07','15','14','13','12','11','10','09',
              '08','06','05','04','03','02','01','00']


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


def _split_for_wire(text: str, cap: int = MAX_OUTGOING_CHARS) -> list:
    """Split text into wire-safe chunks (≤ cap chars each)."""
    text = (text or '').rstrip('\r\n')
    if not text:
        return []
    if len(text) <= cap:
        return [text]
    inner = cap - 8          # reserve "(99/99) " tag
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
        return chunks
    t = len(chunks)
    return [f'({i+1}/{t}) {c}' for i, c in enumerate(chunks)]


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

    # ── Menu entry point ────────────────────────────────────────────────────

    async def show_menu(self):
        from flask import current_app
        bridge_url = self.DEFAULT_BRIDGE_URL
        try:
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
            pass

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
            })

            await self._enter_split_screen()
            await self._emit(
                f'\x1b[32mJoined #{self._room} as {self._handle}.\x1b[0m  '
                'Type /help for commands, /quit to leave.')
            await self._emit(
                '\x1b[33mTip:\x1b[0m use [<] [>] arrow keys to change outgoing text color.')

            self._recv_task = asyncio.create_task(self._recv_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())
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
                # WebSocket-level ping
                await self._send_json({'type': 'ping', 'msgext': str(time.time())})
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

    # ── Split-screen helpers ─────────────────────────────────────────────────

    async def _enter_split_screen(self):
        ws = getattr(self.session, 'window_size', None) or (80, 24)
        try:
            cols, lines = int(ws[0]), int(ws[1])
        except (TypeError, ValueError):
            cols, lines = 80, 24
        self._term_columns  = max(40, min(240, cols))
        self._term_lines    = max(10, min(120, lines))
        self._status_row    = 1
        self._scroll_top    = 2
        self._scroll_bottom = self._term_lines - 1
        self._input_row     = self._term_lines

        await self.session.write(
            '\x1b[r'                                              # reset DECSTBM
            '\x1b[2J\x1b[H'                                       # clear screen
            f'\x1b[{self._scroll_top};{self._scroll_bottom}r'    # set scroll region
        )
        await self._draw_status_line()
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
        room_s = f'\x1b[1;96m[#{self._room}]\x1b[0m'
        topic_s = f' \x1b[96m{self._topic}\x1b[0m' if self._topic else ''

        right_bits = []
        if self._scroll_offset:
            right_bits.append(f'\x1b[1;37;43m PAUSED+{self._scroll_offset} \x1b[0m')
        if self._mention_count:
            right_bits.append(f'\x1b[1;7;91m !{self._mention_count} \x1b[0m')
        # Character count: show remaining chars out of 140 limit
        typed = len(self._input_buf)
        if typed > 0:
            remaining = 140 - typed
            if remaining < 0:
                cc = f'\x1b[1;91m{remaining}\x1b[0m'   # red: over limit
            elif remaining <= 15:
                cc = f'\x1b[1;93m{remaining}\x1b[0m'   # yellow: close
            else:
                cc = f'\x1b[96m{remaining}\x1b[0m'     # cyan: fine
            right_bits.append(cc)
        if self._latency_ms is not None:
            right_bits.append(f'\x1b[2;37m{self._latency_ms}ms\x1b[0m')
        if self._is_away:
            right_bits.append('\x1b[93mAWAY\x1b[0m')
        right = '  '.join(right_bits)

        left_v  = _visible_len(room_s) + _visible_len(topic_s)
        right_v = _visible_len(right)
        gap = max(1, self._term_columns - left_v - right_v - 1)

        overflow = (left_v + right_v + 1) - self._term_columns
        if overflow > 0 and self._topic:
            cut = max(1, len(self._topic) - overflow - 1)
            topic_s = f' \x1b[96m{self._topic[:cut]}>\x1b[0m'
            gap = 1

        await self.session.write(
            '\x1b[1;1H'
            '\x1b[2K'
            '\x1b[44m'
            + room_s + topic_s
            + ' ' * gap
            + right
            + '\x1b[0m'
        )

    async def _draw_input_line(self):
        if not self._split_screen:
            return
        text = ''.join(self._input_buf)
        # Mask password fields
        if self._should_mask(text):
            for pfx in ('/identify ', '/register ', '/update password ',
                        '/trust identify '):
                if text.lower().startswith(pfx):
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

    async def _emit(self, text: str):
        """Append text to the chat history and redraw the entire scroll region.

        Approach: maintain _display_lines (rendered display strings).  For each
        emit, take the last N lines (N = scroll region height) and draw them
        row-by-row with explicit cursor positioning + line-clear.  No DECSTBM
        scroll dependency — every row is cleared before writing, so no bleed
        from previous content is possible.
        """
        self._scrollback.append(text)

        if not self._split_screen:
            await self.session.write(text + '\r\n')
            return

        ts     = datetime.now().strftime('%H:%M ')
        ts_len = len(ts)                     # always 6 "HH:MM "
        pfx0   = f'\x1b[2;37m{ts}\x1b[0m'   # dim grey timestamp for first line
        indent = ' ' * ts_len               # alignment for continuation lines
        max_w  = max(20, self._term_columns - ts_len)

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

        out = []
        for idx in range(scroll_rows):
            row = self._scroll_top + idx
            out.append(f'\x1b[{row};1H\x1b[2K')
            if idx >= pad:
                line = visible[idx - pad]
                if line:
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

    async def _handle_event(self, data: dict):
        evt  = data.get('type', '')

        # ── pong: calculate round-trip latency ──
        if evt == 'pong':
            try:
                t0 = float(data.get('msgext') or data.get('echo') or 0)
                if t0 > 0:
                    self._latency_ms = max(0, int((time.time() - t0) * 1000))
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
                    self._topic = topic.strip()
                    if room.strip().lower() == self._room.lower():
                        await self._emit(
                            f'\x1b[1;96m── Topic: {_pipe_to_ansi(self._topic)}\x1b[0m')
                return

            if head.startswith('USERROOM:'):
                room = body_raw.split(':', 1)[1].strip()
                if room and room.lower() != self._room.lower():
                    self._room = room
                    await self._emit(f'\x1b[96m*** Now in #{room}\x1b[0m')
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
                raw_list = body_raw.split(':', 1)[1].strip()
                for entry in raw_list.split():
                    nick = re.split(r'[@]', entry, 1)[0]
                    if nick:
                        self._known_users.add(nick)
                return

            if head.startswith('USERNICK:'):
                raw = body_raw.split(':', 1)[1].strip()
                parts = raw.split(None, 1)
                old_nick = re.split(r'[@]', parts[0], 1)[0] if parts else ''
                new_nick = parts[1].strip() if len(parts) > 1 else ''
                if old_nick:
                    self._known_users.discard(old_nick)
                if new_nick:
                    self._known_users.add(new_nick)
                return

            if head.startswith(('USERIP:', 'TYPING:', 'CAPABILITIES:',
                                  'NEWROOM:', 'NEWTOPIC:', 'KEEPALIVE:',
                                  'BANNER:')):
                return

        if evt == 'error':
            await self._emit(
                f'\x1b[31m*** Error: {data.get("message", "")}\x1b[0m')
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
                f'\x1b[96m{label}:\x1b[0m {", ".join(items) or "(none)"}')
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
            # Format CTCP lines
            plain = _strip_pipe(_ANSI_SEQ_RE.sub('', body))
            if plain.startswith('[CTCP] '):
                pieces = plain[7:].split(None, 3)
                if len(pieces) >= 3:
                    sender, _, ctcp_cmd = pieces[0], pieces[1], pieces[2]
                    args = pieces[3] if len(pieces) > 3 else ''
                    await self._emit(
                        f'\x1b[90m[\x1b[93mCTCP\x1b[90m]\x1b[0m '
                        f'\x1b[1m{sender}\x1b[0m -> \x1b[33m{ctcp_cmd.upper()}\x1b[0m'
                        + (f' \x1b[2m{args}\x1b[0m' if args else ''))
                return
            if plain.startswith('[CTCP-REPLY] '):
                rest = plain[13:]
                await self._emit(
                    f'\x07\x1b[90m[\x1b[93mCTCP-REPLY\x1b[90m]\x1b[0m '
                    f'\x1b[1;96m{rest}\x1b[0m')
                return
            if body:
                # Fish nick out of formatted body for tab-complete
                m = re.match(r'^\s*[<\[\(&@]?\s*([A-Za-z][\w\-\.]{1,29})', plain)
                if m:
                    nick = m.group(1)
                    if nick.upper() not in ('PM','NOTICE','JOIN','PART',
                            'TIMEOUT','JOINING','TOPIC','BROADCAST',
                            'WELCOME','STATUS'):
                        self._known_users.add(nick)
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
                'time': datetime.utcnow().strftime('%H:%M'),
                'room': '(PM)',
                'from': f'{user}@{bbs}',
                'body': plain_body[:200],
            })
            rendered = self._highlight_mentions(_pipe_to_ansi(body))
            await self._emit(
                f'\x07\x1b[90m[\x1b[93mPM\x1b[90m]\x1b[0m '
                f'\x1b[1;93m{user}@{bbs}\x1b[0m: {rendered}')
            return

        if evt == 'action':
            mentioned = self._was_mentioned(plain_body)
            if mentioned:
                self._mention_count += 1
                self._mention_log.append({
                    'time': datetime.utcnow().strftime('%H:%M'),
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
                    'time': datetime.utcnow().strftime('%H:%M'),
                    'room': f'#{room}' if room else '',
                    'from': f'{user}@{bbs}',
                    'body': plain_body[:200],
                })
            bell = '\x07' if mentioned else ''
            rendered = self._highlight_mentions(_pipe_to_ansi(body))
            # Format: |08[|11Nick|08]|07@bbs text  (anetmrc style with bbs suffix)
            await self._emit(
                f'{bell}'
                f'\x1b[90m[\x1b[96m{user}\x1b[90m]\x1b[2;37m@{bbs}\x1b[0m '
                f'{rendered}')
            return

        # Unknown event with body — show it
        if body:
            await self._emit(
                f'\x1b[90m[{evt}]\x1b[0m {_pipe_to_ansi(body)}')

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
                colored = self._current_color_pipe() + line
                for chunk in _split_for_wire(colored):
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
                    if seq in (b'[D', b'OD'):    # ← left arrow: cycle color left
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
          ESC [ A          → b'[A'   (cursor keys, single letter)
          ESC O A          → b'OA'   (alternate cursor keys)
          ESC [ 5 ~        → b'[5'   (PgUp — trailing ~ consumed and discarded)
          ESC [ 1 ; 2 A    → b'[1'   (shifted cursor — rest consumed)
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
        # If third is a digit, this is a multi-byte sequence like ESC [ 5 ~
        # Drain remaining bytes (up to 4) until we hit a non-digit/letter
        if third and third[0:1].isdigit():
            for _ in range(4):
                try:
                    extra = await asyncio.wait_for(reader.read(1), timeout=timeout)
                    if not extra or extra[0:1] not in b'0123456789;':
                        break
                except (asyncio.TimeoutError, Exception):
                    break
        return seq

    async def _tab_complete(self):
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
        await self._emit(f'\x1b[2;96mTab: {", ".join(matches)}\x1b[0m')

    async def _cycle_color(self, delta: int):
        self._color_idx = (self._color_idx + delta) % len(_COLOR_SEQ)
        await self._draw_input_line()
        code = _COLOR_SEQ[self._color_idx]
        ansi = self._current_color_ansi()
        await self._emit(f'{ansi}>> text color\x1b[0m |{code}')

    def _current_color_ansi(self) -> str:
        return f'\x1b[{_PIPE_COLORS.get(_COLOR_SEQ[self._color_idx], "37")}m'

    def _current_color_pipe(self) -> str:
        return f'|{_COLOR_SEQ[self._color_idx]}'

    def _highlight_mentions(self, text: str) -> str:
        if not self._handle or not text:
            return text
        try:
            pat = re.compile(
                r'(?<!\w)(@?' + re.escape(self._handle) + r')(?!\w)',
                re.IGNORECASE)
            return pat.sub(r'\x1b[1;7;91m\1\x1b[0m', text)
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

    # ── Slash commands ───────────────────────────────────────────────────────

    async def _handle_slash(self, line: str) -> bool:
        parts = line.split(' ', 1)
        cmd  = parts[0].lower().lstrip('/')
        rest = parts[1].strip() if len(parts) > 1 else ''

        if cmd in ('quit', 'exit', 'leave'):
            await self._emit('\x1b[36mLeaving MRC...\x1b[0m')
            return False

        if cmd in ('help', 'h', '?'):
            for ln in (
                '',
                '\x1b[1mMessaging\x1b[0m',
                '  /msg user text      private message  (aliases: /t /pm /dm /tell /w)',
                '  /me action          emote (*action*)',
                '  /broadcast text     broadcast to all rooms (sysop)',
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
                '  /changelog  /routing  /quickstats  /helpserver',
                '  /last [n]           recent chat history',
                '',
                '\x1b[1mClient\x1b[0m',
                '  [<]/[>] arrows       cycle outgoing text color',
                '  [up]/[dn] arrows     scroll chat 1 line',
                '  Tab                 nick autocomplete',
                '  /mentions           show & clear mention log',
                '  /scroll [n]         scroll up n lines (default 5)',
                '  /scroll down [n]    scroll down n lines',
                '  /scroll 0           scroll to bottom (live view)',
                '  /clear              clear chat area',
                '  /termsize w,h       report terminal size to server',
                '  /raw text           raw server command',
                '  /quit               leave MRC',
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
            for chunk in _split_for_wire(body):
                await self._send_json({
                    'type': 'direct_message',
                    'to_user': target,
                    'message': chunk,
                })
            # Local echo so sender sees their own DM in the chat + scrollback
            await self._emit(
                f'\x1b[95m[DM \x1b[0m\x1b[1;95m-> {target}\x1b[0m\x1b[95m] '
                f'\x1b[0m{body}\x1b[0m'
            )
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
                await self._emit('Usage: /join <room>')
                return True
            new_room = rest.strip().lstrip('#').lower()
            await self._send_json({
                'type': 'server_cmd',
                'command': f'JOIN {new_room}',
            })
            self._room = new_room
            await self._emit(f'\x1b[96mJoining #{new_room}...\x1b[0m')
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
                       'quickstats', 'helpserver'):
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
                f'\x1b[96m(afk{": " + rest if rest else ""})\x1b[0m')
            return True

        if cmd == 'back':
            await self._send_json({'type': 'server_cmd', 'command': 'BACK'})
            self._is_away = False
            self._last_input_time = time.time()
            await self._emit('\x1b[96m(back)\x1b[0m')
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
            await self._emit('\x1b[96m(identify sent)\x1b[0m')
            return True

        if cmd == 'register':
            if not rest:
                await self._emit('Usage: /register <password> [email]')
                return True
            await self._send_json({
                'type': 'server_cmd', 'command': f'REGISTER {rest}'})
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
                await self._emit('\x1b[96mMentions:\x1b[0m 0')
                self._mention_count = 0
                return True
            await self._emit(
                f'\x1b[96mMentions:\x1b[0m {n} unread '
                f'({len(log)} in log)')
            for m in log:
                body = m['body']
                if len(body) > 100:
                    body = body[:97] + '...'
                await self._emit(
                    f'  \x1b[2m{m["time"]}\x1b[0m  '
                    f'\x1b[96m{m["room"]:<10}\x1b[0m  '
                    f'\x1b[1m{m["from"]:<24}\x1b[0m  {body}')
            await self._emit('\x1b[2m(cleared)\x1b[0m')
            self._mention_count = 0
            self._mention_log.clear()
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

        if cmd == 'clear':
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
                f'\x1b[96mTab pool ({len(users)}):\x1b[0m '
                + (', '.join(users) if users else '(empty)'))
            return True

        await self._emit(
            f'\x1b[33mUnknown command:\x1b[0m /{cmd}. Try /help.')
        return True
