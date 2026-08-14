# anetbbs/features/anetirc2.py
"""
ANet IRC Client v7 — pure-Python asyncio rewrite.

Replaces the C binary door + PTY bridge (anetirc_door.py) with a fully
async Python implementation that:
  - Runs directly inside the BBS asyncio session (no fork, no PTY, no binary)
  - Uses asyncio.open_connection(ssl=...) for TLS — no lockups
  - SASL PLAIN authentication (same CAP negotiation as v1)
  - Word-wrap, scrollback, nick tab-complete, mIRC color stripping
  - Same pipe-delimited bookmark config as the C client (backward-compatible)
  - Works on any architecture without recompiling

Entry point: launch_anetirc_telnet(user, session)  — compatible with chat.py
"""
import asyncio
import base64
import datetime
import os
from ..core.tz import to_eastern
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

_VERSION      = "7.0"
_MAX_LINES    = 2000
_MAX_SCROLL   = 800
_PING_SECS    = 90
_USERS_W      = 22   # right panel width including its borders
_READ_TIMEOUT = 30   # seconds for IRC socket read before considering stale

# ── ANSI helpers ───────────────────────────────────────────────────────────────

_E = "\x1b"

def _mv(r, c):     return f"{_E}[{r};{c}H"
def _cls():        return f"{_E}[2J{_E}[H"
def _alt_on():     return f"{_E}[?1049h"
def _alt_off():    return f"{_E}[?1049l"
def _hide_cur():   return f"{_E}[?25l"
def _show_cur():   return f"{_E}[?25h"
def _reset():      return f"{_E}[0m"

# Theme: [border_color, statusbar_color, accent_color, title_color]
_THEMES = [
    [f"{_E}[38;5;51m",  f"{_E}[48;5;25m{_E}[38;5;15m",  f"{_E}[38;5;39m",  f"{_E}[1;38;5;51m"],   # cyan
    [f"{_E}[38;5;46m",  f"{_E}[48;5;22m{_E}[38;5;15m",  f"{_E}[38;5;120m", f"{_E}[1;38;5;46m"],   # green
    [f"{_E}[38;5;214m", f"{_E}[48;5;52m{_E}[38;5;15m",  f"{_E}[38;5;223m", f"{_E}[1;38;5;214m"],  # amber
]

_MIRC_RE = re.compile(r'\x03(?:\d{1,2}(?:,\d{1,2})?)?|\x02|\x0f|\x16|\x1f|\x1d|\x01[^\x01]*\x01')
# Real vulnerability found in a security audit: _MIRC_RE only strips
# mIRC formatting bytes -- it never matched \x1b (ESC), so any IRC
# user (no ANetBBS account needed) could put a raw ANSI/CSI/OSC escape
# sequence in a PRIVMSG and have it written straight to another BBS
# user's real terminal (screen clears, spoofed prompts, cursor tricks).
# Two passes: _ANSI_RE first, so well-formed CSI ("\x1b[...letter")
# and OSC ("\x1b]...BEL-or-ST") sequences are removed cleanly (no
# leftover bracket/digit text visible in chat); then _CONTROL_RE as a
# blanket safety net that strips the ENTIRE C0 range (+ DEL) --
# IRC message text is legitimately single-line/plain, so nothing
# legitimate is lost, and this is what actually guarantees a bare or
# malformed ESC byte can never survive even if it doesn't match the
# well-formed patterns above.
_ANSI_RE = re.compile(r'\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[A-Za-z0-9=><~])')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')

def _strip(text: str) -> str:
    return _CONTROL_RE.sub('', _ANSI_RE.sub('', _MIRC_RE.sub('', text)))


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class _Line:
    nick:   str
    text:   str
    ts:     float = field(default_factory=time.time)
    status: bool  = False   # True = server/system line

@dataclass
class Bookmark:
    label:    str  = "New Server"
    server:   str  = "irc.libera.chat"
    port:     int  = 6667
    nick:     str  = "ANetUser"
    channel:  str  = "#chat"
    tls:      bool = False
    password: str  = ""


# ── Config ─────────────────────────────────────────────────────────────────────

def _load_cfg(path: str) -> list[Bookmark]:
    bms: list[Bookmark] = []
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for raw in f:
                p = raw.strip().split('|')
                if len(p) < 5:
                    continue
                bms.append(Bookmark(
                    label=p[0], server=p[1],
                    port=int(p[2]) if p[2].isdigit() else 6667,
                    nick=p[3], channel=p[4],
                    tls=p[5].strip() in ('1','true','yes') if len(p) > 5 else False,
                    password=p[6].strip() if len(p) > 6 else '',
                ))
    except (FileNotFoundError, IOError):
        pass
    return bms or [Bookmark()]

def _save_cfg(path: str, bms: list[Bookmark]):
    # Real data-corruption bug found in a deep review: this format has
    # no escaping at all, and _load_cfg's read side does an unbounded
    # `.split('|')`. A literal '|' typed into ANY free-text field --
    # most plausibly `label`, which a sysop might reasonably separate
    # with "Home | Personal" the way many UIs do -- silently shifts
    # every field after it on the next load (server becomes the tail
    # of the label, port becomes the server, etc.), with no error and
    # no way to tell from the UI that it happened. Fixed by refusing to
    # ever WRITE an ambiguous '|' into a field, using a visually
    # similar substitute (U+00A6 BROKEN BAR) -- the read side and file
    # format are unchanged, so this stays compatible with the original
    # C client's understanding of the format, and existing saved files
    # need no migration.
    def _esc(s: str) -> str:
        return s.replace('|', '¦')
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for b in bms:
            f.write(f"{_esc(b.label)}|{_esc(b.server)}|{b.port}|{_esc(b.nick)}"
                    f"|{_esc(b.channel)}|{1 if b.tls else 0}|{_esc(b.password)}\n")


# ── IRC protocol ───────────────────────────────────────────────────────────────

class _IRC:
    """Manages the IRC TCP/TLS connection and protocol state."""

    def __init__(self, client: 'ANetIRC'):
        self.client   = client
        self.reader:  Optional[asyncio.StreamReader] = None
        self.writer:  Optional[asyncio.StreamWriter] = None
        self.server   = ""
        self.port     = 0
        self.nick     = ""
        self.channel  = ""
        self.connected   = False
        self.registered  = False
        self.users: list[str] = []
        self._rbuf    = ""
        self._pw      = ""
        self._sasl    = "none"   # none/wait_cap/wait_ack/wait_plus/wait_result/done
        self._ping_task: Optional[asyncio.Task] = None

    # ── Connection ─────────────────────────────────────────────────────────────

    async def connect(self, bm: Bookmark) -> bool:
        self.server  = bm.server
        self.port    = bm.port
        self.nick    = bm.nick
        self.channel = bm.channel
        self._pw     = bm.password
        self.users   = []
        self.registered = False

        ssl_ctx = None
        if bm.tls:
            ssl_ctx = ssl.create_default_context()

        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(bm.server, bm.port, ssl=ssl_ctx),
                timeout=15,
            )
        except asyncio.TimeoutError:
            self.client._sys(f"Connection timed out to {bm.server}:{bm.port}")
            return False
        except ssl.SSLError as e:
            self.client._sys(f"TLS error: {e}")
            return False
        except OSError as e:
            self.client._sys(f"Connection failed: {e}")
            return False

        self.connected = True
        self.client._sys(f"Connected to {bm.server}:{bm.port}"
                         + (" [TLS]" if bm.tls else ""))
        self.client.dirty_status = True

        if self._pw:
            self._sasl = "wait_cap"
            await self._tx("CAP LS 302")
        else:
            self._sasl = "none"
            await self._tx(f"NICK {self.nick}")
            await self._tx(f"USER {self.nick} 0 * :ANet BBS")

        self._ping_task = asyncio.create_task(self._ping_loop())
        return True

    async def disconnect(self, reason: str = "Goodbye"):
        self.connected = False
        if self._ping_task:
            self._ping_task.cancel()
        if self.writer:
            try:
                await self._tx(f"QUIT :{reason}")
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), timeout=3)
            except Exception:
                pass
            self.writer = None
            self.reader = None

    # ── Send helpers ───────────────────────────────────────────────────────────

    async def _tx(self, line: str):
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write((line + "\r\n").encode('utf-8', errors='replace'))
                await self.writer.drain()
            except Exception:
                pass

    async def privmsg(self, target: str, text: str):
        await self._tx(f"PRIVMSG {target} :{text}")
        self.client._add(_Line(nick=self.nick, text=text, status=False))

    # ── Read loop (runs as a background Task) ──────────────────────────────────

    async def read_loop(self):
        while self.connected and self.reader:
            try:
                data = await asyncio.wait_for(
                    self.reader.read(4096), timeout=_READ_TIMEOUT)
            except asyncio.TimeoutError:
                if self.connected:
                    await self._tx(f"PING :{self.server}")
                continue
            except Exception:
                break
            if not data:
                break
            self._rbuf += data.decode('utf-8', errors='replace')
            while '\n' in self._rbuf:
                line, self._rbuf = self._rbuf.split('\n', 1)
                line = line.rstrip('\r')
                if line:
                    # Real robustness gap found in a deep review: _handle()
                    # (e.g. its `raw.index(' ')` on a ':'-prefixed line with
                    # no following space) can raise on a malformed line,
                    # and this call was completely unguarded. Since this
                    # loop runs as a detached background task
                    # (asyncio.create_task in _chat_session), an uncaught
                    # exception here doesn't crash anything visibly -- it
                    # just silently kills read_loop, so the user sees no
                    # error at all, just chat that never receives anything
                    # again, indistinguishable from a hung connection.
                    try:
                        await self._handle(line)
                    except Exception as exc:
                        self.client._sys(
                            f"[client error parsing a server line: {exc}]")

        if self.connected:
            self.connected = False
            self.client._sys("Disconnected from server")
            self.client.dirty_status = True

    async def _ping_loop(self):
        while self.connected:
            await asyncio.sleep(_PING_SECS)
            if self.connected:
                await self._tx(f"PING :{self.server}")

    # ── Protocol parser ────────────────────────────────────────────────────────

    async def _handle(self, raw: str):
        prefix = ""
        if raw.startswith(':'):
            sp = raw.index(' ')
            prefix = raw[1:sp]
            raw = raw[sp + 1:]

        cmd, _, rest = raw.partition(' ')
        cmd = cmd.upper()
        src = prefix.split('!')[0] if '!' in prefix else prefix

        trailing = ""
        if ':' in rest:
            pre, _, trailing = rest.partition(':')
            params = pre.split() + [trailing]
        else:
            params = rest.split()
            trailing = params[-1] if params else ""

        # ── Dispatch ───────────────────────────────────────────────────────────

        if cmd == "PING":
            await self._tx(f"PONG :{trailing}")

        elif cmd == "CAP":
            await self._cap(params, trailing)

        elif cmd == "AUTHENTICATE":
            if trailing == '+' and self._sasl == "wait_plus":
                self._sasl = "wait_result"
                await self._sasl_creds()

        elif cmd == "903":                              # SASL success
            self._sasl = "done"
            await self._tx("CAP END")
            await self._tx(f"NICK {self.nick}")
            await self._tx(f"USER {self.nick} 0 * :ANet BBS")

        elif cmd in ("904", "905", "906", "907"):       # SASL fail
            self.client._sys("SASL auth failed — connecting without authentication")
            self._sasl = "done"
            await self._tx("CAP END")
            await self._tx(f"NICK {self.nick}")
            await self._tx(f"USER {self.nick} 0 * :ANet BBS")

        elif cmd == "001":
            self.registered = True
            self.client._sys(f"Logged in as {self.nick}")
            self.client.dirty_status = True
            if self.channel:
                await self._tx(f"JOIN {self.channel}")

        elif cmd == "433":                              # Nick in use
            self.nick += "_"
            await self._tx(f"NICK {self.nick}")
            self.client._sys(f"Nick taken — trying {self.nick}")
            self.client.dirty_status = True

        elif cmd == "PRIVMSG":
            if params and src.lower() != self.nick.lower():
                # Real bug found in a deep review of the whole client:
                # ANY CTCP request other than ACTION (VERSION, PING,
                # CLIENTINFO, TIME, ...) used to fall straight into the
                # plain-chat branch below. _strip() (the mIRC-color
                # stripper) also matches the generic "\x01...\x01" CTCP
                # framing as one of its patterns, so the request text
                # got silently deleted -- the user saw a blank ghost
                # line from that nick, and the requester got no reply
                # at all (real IRC etiquette expects VERSION/PING
                # replies; many bots/clients flag a nick that never
                # answers). Now explicitly handled: ACTION displays as
                # before, VERSION/PING get a real CTCP reply and are
                # NOT shown in chat, anything else CTCP-framed is
                # silently ignored (not displayed either) instead of
                # leaking a blank line.
                if trailing.startswith('\x01') and trailing.endswith('\x01') and len(trailing) >= 2:
                    ctcp = trailing[1:-1]
                    verb, _, ctcp_arg = ctcp.partition(' ')
                    verb = verb.upper()
                    if verb == "ACTION":
                        self.client._add(_Line(
                            nick="*", text=f"* {src} {ctcp_arg}", status=True))
                    elif verb == "VERSION":
                        await self._tx(
                            f"NOTICE {src} :\x01VERSION ANetIRC {_VERSION} "
                            f"(ANetBBS)\x01")
                    elif verb == "PING":
                        await self._tx(f"NOTICE {src} :\x01PING {ctcp_arg}\x01")
                    # Any other CTCP verb (CLIENTINFO, TIME, ...): silently
                    # ignored rather than answered incorrectly or leaked
                    # into chat as a blank line.
                else:
                    self.client._add(_Line(nick=src, text=_strip(trailing), status=False))

        elif cmd == "NOTICE":
            self.client._sys(f"{src}: {_strip(trailing)}")

        elif cmd == "JOIN":
            chan = trailing or (params[0] if params else "")
            if src.lower() == self.nick.lower():
                self.channel = chan
                self.client.dirty_status = True
                self.client._sys(f"Joined {chan}")
            else:
                self.client._sys(f"{src} joined {chan}")
                if src not in self.users:
                    self.users.append(src)
                    self.client.dirty_users = True

        elif cmd == "PART":
            self.client._sys(f"{src} left {self.channel}")
            if src in self.users:
                self.users.remove(src)
                self.client.dirty_users = True

        elif cmd == "QUIT":
            self.client._sys(f"{src} quit: {_strip(trailing)}")
            if src in self.users:
                self.users.remove(src)
                self.client.dirty_users = True

        elif cmd == "KICK":
            target = params[1] if len(params) > 1 else "?"
            self.client._sys(f"{src} kicked {target}: {trailing}")
            if target in self.users:
                self.users.remove(target)
                self.client.dirty_users = True

        elif cmd == "NICK":
            new_nick = trailing or params[0] if params else trailing
            if src.lower() == self.nick.lower():
                self.nick = new_nick
                self.client.dirty_status = True
            self.client._sys(f"{src} is now {new_nick}")
            if src in self.users:
                self.users[self.users.index(src)] = new_nick
                self.client.dirty_users = True

        elif cmd in ("MODE", "TOPIC", "332"):
            if trailing:
                label = "Topic" if cmd in ("TOPIC","332") else "Mode"
                self.client._sys(f"{label}: {_strip(trailing)}")

        elif cmd == "353":                              # NAMES list
            for n in trailing.split():
                clean = n.lstrip('@+&~%')
                if clean and clean not in self.users:
                    self.users.append(clean)
            self.client.dirty_users = True

        elif cmd == "366":                              # End of NAMES
            self.client.dirty_users = True

        elif cmd in ("372", "375", "376", "265", "266"):   # MOTD / lusers
            if trailing and trailing not in ("-", "- ", ""):
                self.client._sys(_strip(trailing))

        elif cmd.isdigit() and int(cmd) >= 400:        # Error numerics
            self.client._sys(f"[{cmd}] {_strip(trailing)}")

        elif not cmd.isdigit() and trailing:
            pass   # ignore unknown server messages silently

    async def _cap(self, params: list[str], trailing: str):
        sub = params[1].upper() if len(params) > 1 else ""
        if sub == "LS" and self._sasl == "wait_cap":
            if "sasl" in trailing.lower():
                self._sasl = "wait_ack"
                await self._tx("CAP REQ :sasl")
            else:
                self.client._sys("Server lacks SASL — connecting without auth")
                self._sasl = "done"
                await self._tx("CAP END")
                await self._tx(f"NICK {self.nick}")
                await self._tx(f"USER {self.nick} 0 * :ANet BBS")
        elif sub == "ACK" and self._sasl == "wait_ack":
            self._sasl = "wait_plus"
            await self._tx("AUTHENTICATE PLAIN")
        elif sub == "NAK":
            self.client._sys("Server rejected SASL — connecting without auth")
            self._sasl = "done"
            await self._tx("CAP END")
            await self._tx(f"NICK {self.nick}")
            await self._tx(f"USER {self.nick} 0 * :ANet BBS")

    async def _sasl_creds(self):
        plain = b'\x00' + self.nick.encode() + b'\x00' + self._pw.encode()
        await self._tx(f"AUTHENTICATE {base64.b64encode(plain).decode()}")

    # ── User commands ──────────────────────────────────────────────────────────

    async def command(self, text: str):
        if not text.startswith('/'):
            if self.connected and self.channel:
                await self.privmsg(self.channel, text)
            return
        parts = text[1:].split(' ', 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        dispatch = {
            "quit":   lambda: self._quit(arg),
            "join":   lambda: self._tx(f"JOIN {arg}"),
            "part":   lambda: self._tx(f"PART {self.channel} :{arg or 'Goodbye'}"),
            "nick":   lambda: self._tx(f"NICK {arg}"),
            "topic":  lambda: self._tx(f"TOPIC {self.channel} :{arg}"),
            "raw":    lambda: self._tx(arg),
            "quote":  lambda: self._tx(arg),
            "names":  lambda: self._names(),
            "who":    lambda: self._tx(f"WHO {arg or self.channel}"),
            "whois":  lambda: self._tx(f"WHOIS {arg}"),
            "me":     lambda: self._me(arg),
        }
        if cmd in ("msg", "query"):
            p2 = arg.split(' ', 1)
            if len(p2) == 2:
                await self._tx(f"PRIVMSG {p2[0]} :{p2[1]}")
                self.client._sys(f"-> {p2[0]}: {p2[1]}")
            return
        if cmd == "help":
            for line in [
                "--- ANet IRC v7 help ---",
                "ESC or Ctrl+Q : return to startup / exit IRC",
                "F2            : toggle users panel",
                "Up/Down arrows: scroll chat (when input is empty)",
                "PgUp/PgDn     : scroll chat 10 lines",
                "Ctrl+U/D      : scroll up/down (alternate keys)",
                "Tab           : nick auto-complete",
                "Up/Down (typing): command history",
                "/join #chan  /part  /nick new  /topic txt",
                "/msg user text  /me action  /names  /whois user",
                "/raw line  /quit [reason]",
                "--- end help ---",
            ]:
                self.client._sys(line)
            return
        if cmd in dispatch:
            await dispatch[cmd]()
        else:
            self.client._sys(f"Unknown command /{cmd}  — try /help")

    async def _quit(self, reason: str):
        await self.disconnect(reason or "Goodbye")
        self.client._back = True

    async def _names(self):
        self.users.clear()
        self.client.dirty_users = True
        await self._tx(f"NAMES {self.channel}")

    async def _me(self, text: str):
        await self._tx(f"PRIVMSG {self.channel} :\x01ACTION {text}\x01")
        self.client._sys(f"* {self.nick} {text}")


# ── Screen renderer ────────────────────────────────────────────────────────────

class _Screen:
    def __init__(self, w: int, h: int, theme: int = 0):
        self.w = w
        self.h = h
        self.theme = theme

    @property
    def _b(self): return _THEMES[self.theme][0]      # border
    @property
    def _s(self): return _THEMES[self.theme][1]      # status bar
    @property
    def _a(self): return _THEMES[self.theme][2]      # accent
    @property
    def _t(self): return _THEMES[self.theme][3]      # title

    def _split(self, show_users: bool) -> int:
        return self.w - _USERS_W if show_users else self.w

    def full_clear(self) -> str:
        return _cls()

    def draw_frame(self, show_users: bool, startup: bool = False) -> str:
        w, h = self.w, self.h
        sp = self._split(show_users) if not startup else w // 2
        b  = self._b
        o  = [_reset()]

        # Title
        title = (f" A-Net IRC Client {_VERSION} ║ "
                 + ("Startup Manager" if startup else "Chat View") + " ")
        o.append(_mv(1, 1) + self._t + title.ljust(w)[:w] + _reset())

        # Top border
        o.append(_mv(2, 1) + b + "╔" + "═" * (sp - 2))
        if show_users or startup:
            o.append("╦" + "═" * (w - sp - 1) + "╗")
        else:
            o.append("╗")
        o.append(_reset())

        top = 3; bot = h - 4
        for r in range(top, bot + 1):
            o.append(_mv(r, 1) + b + "║" + _reset())
            o.append(_mv(r, sp) + b + "║" + _reset())
            if show_users or startup:
                o.append(_mv(r, w) + b + "║" + _reset())

        # Bottom border
        o.append(_mv(bot + 1, 1) + b + "╚" + "═" * (sp - 2))
        if show_users or startup:
            o.append("╩" + "═" * (w - sp - 1) + "╝")
        else:
            o.append("╝")
        o.append(_reset())

        # Labels
        label = "BOOKMARKS" if startup else "CHAT"
        o.append(_mv(top, 3) + self._a + label + _reset())
        if show_users:
            o.append(_mv(top, sp + 2) + self._a + "USERS" + _reset())
        if startup:
            o.append(_mv(top, sp + 2) + self._a + "DETAILS" + _reset())

        return "".join(o)

    def draw_chat(self, lines: list[_Line], scroll: int, show_users: bool) -> str:
        h      = self.h
        sp     = self._split(show_users)
        col_w  = sp - 4
        top    = 3; bot = h - 4
        max_r  = bot - top + 1

        # Word-wrap all lines into display rows
        rows: list[str] = []
        for ln in lines:
            # fromtimestamp() alone uses the HOST's own OS-local
            # timezone (whatever that happens to be configured to) --
            # inconsistent with the rest of the app; go through UTC
            # explicitly first, then the shared Eastern converter.
            ts_utc = datetime.datetime.fromtimestamp(ln.ts, tz=datetime.timezone.utc)
            ts  = to_eastern(ts_utc).strftime("%H:%M:%S")
            pfx = f"[{ts}] * " if ln.status else f"[{ts}] <{ln.nick}> "
            pl  = len(pfx)
            avail = max(col_w - pl, 6)
            src = _strip(ln.text)
            first = True
            while True:
                row = (pfx if first else " " * pl)
                first = False
                if not src:
                    break
                if len(src) <= avail:
                    row += src; src = ""; rows.append(row); break
                cut = avail
                for j in range(avail - 1, 0, -1):
                    if src[j] == ' ':
                        cut = j; break
                row += src[:cut]
                src  = src[cut:].lstrip(' ')
                rows.append(row)

        o = []
        # Clear chat area
        for r in range(top, bot + 1):
            o.append(_mv(r, 3) + " " * col_w)

        start = max(0, len(rows) - max_r - scroll)
        for i in range(max_r):
            idx = start + i
            if idx >= len(rows):
                break
            o.append(_mv(top + i, 3) + rows[idx][:col_w].ljust(col_w)[:col_w])

        return "".join(o)

    def draw_users(self, users: list[str], show_users: bool) -> str:
        if not show_users:
            return ""
        w, h  = self.w, self.h
        sp    = self._split(True)
        col_w = w - sp - 2
        top   = 3; bot = h - 4
        max_r = bot - top + 1
        o = []
        for r in range(top, bot + 1):
            o.append(_mv(r, sp + 2) + " " * col_w)
        o.append(_mv(top, sp + 2) + self._a + "USERS" + _reset())
        for i, u in enumerate(users[:max_r]):
            o.append(_mv(top + i, sp + 2) + u[:col_w])
        return "".join(o)

    def draw_status(self, server: str, port: int, channel: str,
                    nick: str, connected: bool, scroll: int) -> str:
        state = "ON" if connected else "OFF"
        scr   = f"+{scroll}" if scroll > 0 else ""
        line  = (f" {state}{scr} | {nick} | {channel} | {server}:{port}"
                 f" | ESC:exit  F2:users  /help ")
        return (_mv(self.h - 2, 1) + self._s
                + line.ljust(self.w)[:self.w] + _reset())

    def draw_input(self, text: str, cur: int) -> str:
        max_w = self.w - 3
        # Scroll input if cursor would go off screen
        if cur > max_w:
            offset = cur - max_w
            text = text[offset:]
            cur  = max_w
        disp = text[:max_w]
        return (_mv(self.h - 1, 1) + _reset() + "> "
                + disp.ljust(max_w)[:max_w]
                + _mv(self.h - 1, cur + 3))

    # ── Startup screen ─────────────────────────────────────────────────────────

    def draw_startup(self, bms: list[Bookmark], sel: int,
                     edit_field: int, edit_val: str, theme: int) -> str:
        w, h  = self.w, self.h
        sp    = w // 2
        top   = 3; bot = h - 4
        max_r = bot - top + 1
        o     = [self.draw_frame(False, startup=True)]

        # Bookmark list
        for i, bm in enumerate(bms[:max_r - 1]):
            r = top + i
            if i == sel:
                o.append(_mv(r, 3) + self._s + bm.label[:sp - 5].ljust(sp - 5) + _reset())
            else:
                o.append(_mv(r, 3) + bm.label[:sp - 5].ljust(sp - 5))

        # Detail panel
        bm    = bms[sel] if bms else Bookmark()
        names = ["Label", "Server", "Port", "Nick", "Channel", "TLS", "Password"]
        vals  = [
            bm.label, bm.server, str(bm.port), bm.nick, bm.channel,
            "Yes" if bm.tls else "No",
            "*" * len(bm.password) if bm.password else "(none — SASL disabled)",
        ]
        if edit_field >= 0:
            display_val = edit_val if edit_field != 6 else "*" * len(edit_val)
            vals[edit_field] = display_val + "█"

        fw = w - sp - 14
        for i, (n, v) in enumerate(zip(names, vals)):
            r = top + i * 2
            if r > bot - 2:
                break
            o.append(_mv(r, sp + 2) + self._a + f"{n}: " + _reset())
            o.append(_mv(r, sp + 12) + v[:fw].ljust(fw)[:fw])

        # Footer
        ft = "Enter:edit  N:new  Spc:TLS  C:connect  T:theme  A:save  D:del  Esc:quit"
        o.append(_mv(h - 2, 3) + ft[:w - 4])
        o.append(_mv(h - 1, 3) + " " * (w - 4))

        return "".join(o)


# ── Key parser ─────────────────────────────────────────────────────────────────

# Tilde-terminated CSI sequences: "ESC [ <n> [; <mod>] ~". Codes 1-6 are
# the standard vt220 set (Home/Insert/Delete/End/PgUp/PgDn); 11-24 with
# the gaps at 16 and 22 are SyncTERM's own function-key codes, confirmed
# verbatim against SyncTERM's own CTerm documentation ("Sequences sent
# by SyncTERM": F1 "\033[11~" ... F5 "\033[15~", F6 "\033[17~" ...
# F10 "\033[21~", F11 "\033[23~", F12 "\033[24~") -- SyncTERM does NOT
# use xterm's "ESC O P"-style SS3 codes for function keys at all, which
# is the real root cause of a live bug report: F2 (and, it turns out,
# every other function key, PgUp/PgDn, and several more) did nothing
# at all for a real SyncTerm/SSH user, because the old parser only
# recognized narrow prefix patterns that never matched any of these
# real codes. Modifier suffixes (";2"=Shift, ";3"=Alt, ";5"=Ctrl) are
# also confirmed against the same table.
_TILDE_KEYS = {
    1: 'HOME', 2: 'INSERT', 3: 'DELETE', 4: 'END', 5: 'PGUP', 6: 'PGDN',
    11: 'F1', 12: 'F2', 13: 'F3', 14: 'F4', 15: 'F5',
    17: 'F6', 18: 'F7', 19: 'F8', 20: 'F9', 21: 'F10',
    23: 'F11', 24: 'F12',
}
_TILDE_MODIFIERS = {2: 'SHIFT_', 3: 'ALT_', 5: 'CTRL_'}


class _Keys:
    """Accumulates raw bytes from session, yields parsed key names."""

    def __init__(self):
        self._buf = b""

    def feed(self, data: bytes):
        self._buf += data

    def next(self) -> Optional[str]:
        if not self._buf:
            return None
        b = self._buf

        if b[0:1] == b'\x1b':
            if len(b) < 2:
                return None   # wait for more
            if b[1:2] == b'[':
                if len(b) < 3:
                    return None
                seq = b[2:].decode('latin-1', errors='replace')
                # Collect until we hit a letter, '~', or '@' (VT sequence
                # terminators). '@' must terminate too -- SyncTERM's
                # Insert key is the bare, non-numeric "ESC [ @" with no
                # letter and no '~' at all; without '@' as a recognized
                # terminator this sequence never completes and silently
                # blocks all further key parsing behind it.
                end = 0
                while (end < len(seq) and not seq[end].isalpha()
                      and seq[end] not in ('~', '@')):
                    end += 1
                if end == len(seq):
                    return None   # incomplete
                full = seq[:end + 1]
                self._buf = b[3 + end:]

                # Letter-terminated: arrows/Home match the common xterm/
                # vt100 convention every terminal agrees on. End/Back Tab/
                # Insert below are SyncTERM's own documented (and, for
                # End and Insert, non-standard -- xterm uses "ESC [ F" and
                # a numeric "ESC [ 2~" respectively) choices; 'F' is kept
                # too so xterm-family clients' real End key still works.
                mapping = {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT',
                           'H': 'HOME',
                           'F': 'END',      # xterm's End
                           'K': 'END',      # SyncTERM's End
                           'Z': 'BACKTAB',  # SyncTERM's Back Tab
                           '@': 'INSERT'}   # SyncTERM's Insert
                if full in mapping:
                    return mapping[full]

                # SyncTERM's own Page Up/Down -- "\033[V"/"\033[U", NOT
                # the vt220 "\033[5~"/"\033[6~" tilde form (handled below
                # via _TILDE_KEYS for non-SyncTERM clients that use it).
                if full == 'V': return "PGUP"
                if full == 'U': return "PGDN"

                if full.endswith('~'):
                    num_part = full[:-1]
                    mod = None
                    if ';' in num_part:
                        num_part, mod_str = num_part.split(';', 1)
                        try:
                            mod = int(mod_str)
                        except ValueError:
                            mod = None
                    try:
                        num = int(num_part)
                    except ValueError:
                        num = None
                    base = _TILDE_KEYS.get(num)
                    if base is None:
                        return None
                    prefix = _TILDE_MODIFIERS.get(mod, '')
                    return prefix + base

                # xterm-style "ESC [ 1 ; <mod> <letter>" (shifted arrows,
                # e.g. Shift+Up = "\033[1;2A") -- structurally distinct
                # from the plain tilde-numeric form above (no trailing
                # '~'), kept for non-SyncTERM clients that use it.
                if full.startswith('1;') and full[-1].isalpha():
                    return "SHIFT_" + full[-1]
                return None
            if b[1:2] == b'O':
                if len(b) < 3:
                    return None
                c = chr(b[2])
                self._buf = b[3:]
                return {'P':'F1','Q':'F2','R':'F3','S':'F4',
                        'H':'HOME','F':'END'}.get(c)
            # Bare ESC
            self._buf = b[1:]
            return "ESC"

        # Single byte / printable
        c = b[0:1].decode('latin-1')
        self._buf = b[1:]
        if c == '\r' or c == '\n': return "ENTER"
        # \x7f (DEL byte) is SyncTERM's own Delete key, but it's also
        # the near-universal Backspace byte real physical keyboards send
        # over ssh/telnet on most OTHER terminals -- a genuine ambiguity
        # with no clean resolution short of full terminal-type detection
        # (this project already does that for sixel support via a DA1/
        # CTDA probe -- see the sixel auto-detect work -- but wiring the
        # same probe through here is a bigger change than this fix
        # warrants). Kept as Backspace: that's the overwhelmingly more
        # common real-world meaning, and forward-delete is low-value in
        # a single-line chat input anyway.
        if c == '\x7f' or c == '\x08': return "BACKSPACE"
        if c == '\t':  return "TAB"
        if c == '\x1d': return "CTRL_]"
        return c


# ── Main client ────────────────────────────────────────────────────────────────

class ANetIRC:
    def __init__(self, session, cfg_path: str):
        self.session  = session
        self.cfg_path = cfg_path
        self.bms      = _load_cfg(cfg_path)
        self.irc      = _IRC(self)
        self.lines:   list[_Line] = []
        self.scroll   = 0
        self.show_users  = True
        self.theme    = 0
        self._back    = False       # signal to return to startup after chat
        self._sel     = 0           # selected bookmark index
        self._ef      = -1          # edit_field (-1 = none)
        self._ev      = ""          # edit value
        self._inp     = ""          # chat input text
        self._cur     = 0           # cursor position in _inp
        self._hist:   list[str] = []
        self._hidx    = -1
        self._tabidx  = 0
        self._tab_state = None      # last tab-complete's {pre,matches,output}, or None
        self._keys    = _Keys()
        self._w = 80; self._h = 24
        self._scr: Optional[_Screen] = None
        # Dirty flags
        self.dirty_frame  = True
        self.dirty_chat   = True
        self.dirty_users  = True
        self.dirty_status = True
        self.dirty_input  = True

    def _sys(self, text: str):
        self._add(_Line(nick="*", text=text, status=True))

    def _add(self, ln: _Line):
        self.lines.append(ln)
        if len(self.lines) > _MAX_LINES:
            self.lines = self.lines[-_MAX_LINES:]
        self.dirty_chat = True

    # ── Session I/O ────────────────────────────────────────────────────────────

    async def _wr(self, text: str):
        try:
            await self.session.write(text)
        except Exception:
            pass

    async def _detect_size(self) -> tuple[int, int]:
        # Use session attrs if available; cap width at 131 (132-col wide terminal)
        cols = getattr(self.session, 'cols', None) or getattr(self.session, 'window_size', (80, 24))[0]
        rows = getattr(self.session, 'rows', None) or getattr(self.session, 'window_size', (80, 24))[1]
        if cols and rows and int(cols) > 20 and int(rows) > 5:
            return min(int(cols), 131), min(int(rows), 55)
        # ANSI terminal size query
        try:
            await self._wr("\x1b[s\x1b[999;999H\x1b[6n\x1b[u")
            resp = b""
            dl = asyncio.get_event_loop().time() + 1.5
            while asyncio.get_event_loop().time() < dl:
                try:
                    chunk = await asyncio.wait_for(
                        self.session.read_raw(32), timeout=0.2)
                    if chunk:
                        resp += chunk
                    if b'R' in resp:
                        break
                except Exception:
                    break
            m = re.search(rb'\x1b\[(\d+);(\d+)R', resp)
            if m:
                return min(int(m.group(2)), 131), min(int(m.group(1)), 55)
        except Exception:
            pass
        return 79, 23

    async def _read_key(self, timeout: float) -> Optional[str]:
        # Drain any already-buffered keys first
        k = self._keys.next()
        if k is not None:
            return k
        try:
            data = await asyncio.wait_for(
                self.session.read_raw(64), timeout=timeout)
            if data:
                self._keys.feed(data)
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass

        k = self._keys.next()
        if k is not None:
            return k

        # Pending bare ESC: wait briefly for any follow-up escape sequence bytes.
        # If nothing arrives, the user pressed Esc alone — return "ESC".
        if self._keys._buf == b'\x1b':
            try:
                data = await asyncio.wait_for(
                    self.session.read_raw(8), timeout=0.09)
                if data:
                    self._keys.feed(data)
            except asyncio.TimeoutError:
                self._keys._buf = b''
                return "ESC"
            except Exception:
                self._keys._buf = b''
                return "ESC"

        return self._keys.next()

    # ── Entry point ────────────────────────────────────────────────────────────

    async def run(self):
        self._w_sz, self._h_sz = await self._detect_size()
        self._w = self._w_sz; self._h = self._h_sz
        self._scr = _Screen(self._w, self._h, self.theme)

        await self._wr(_alt_on() + _hide_cur() + _cls())
        try:
            while True:
                done = await self._startup_loop()
                if done:
                    break
        finally:
            await self._wr(_alt_off() + _show_cur() + _reset() + "\r\n")

    # ── Startup screen ─────────────────────────────────────────────────────────

    async def _startup_loop(self) -> bool:
        """Returns True when the user exits IRC, False when returning from chat."""
        self._back = False
        self.dirty_frame = True
        await self._wr(_cls())   # clear any leftover chat content on mode switch

        while True:
            if self.dirty_frame:
                out = self._scr.draw_startup(
                    self.bms, self._sel, self._ef, self._ev, self.theme)
                await self._wr(out)
                self.dirty_frame = False

            key = await self._read_key(0.3)
            if key is None:
                continue

            if self._ef >= 0:
                done = await self._startup_edit(key)
                if done == "connect":
                    await self._chat_session()
                    return False
            else:
                action = await self._startup_nav(key)
                if action == "connect":
                    await self._chat_session()
                    return False
                if action == "quit":
                    return True

    async def _startup_nav(self, key: str) -> Optional[str]:
        bms = self.bms
        if key == "UP":
            self._sel = max(0, self._sel - 1); self.dirty_frame = True
        elif key == "DOWN":
            self._sel = min(len(bms) - 1, self._sel + 1); self.dirty_frame = True
        elif key == "ENTER":
            if bms:
                self._ef = 0; self._ev = bms[self._sel].label; self.dirty_frame = True
        elif key.lower() == "c":
            return "connect"
        elif key == " ":
            if bms:
                b = bms[self._sel]; b.tls = not b.tls
                if b.tls and b.port == 6667: b.port = 6697
                elif not b.tls and b.port == 6697: b.port = 6667
                self.dirty_frame = True
        elif key.lower() == "t":
            self.theme = (self.theme + 1) % len(_THEMES)
            self._scr.theme = self.theme; self.dirty_frame = True
        elif key.lower() == "n":
            bms.append(Bookmark(nick=bms[self._sel].nick if bms else "ANetUser"))
            self._sel = len(bms) - 1; self.dirty_frame = True
        elif key.lower() == "a":
            _save_cfg(self.cfg_path, bms); self.dirty_frame = True
        elif key.lower() == "d" and bms:
            bms.pop(self._sel)
            self._sel = max(0, self._sel - 1)
            if not bms: bms.append(Bookmark())
            _save_cfg(self.cfg_path, bms); self.dirty_frame = True
        elif key == "ESC":
            return "quit"
        return None

    _FIELDS = ["label","server","port","nick","channel","tls","password"]

    async def _startup_edit(self, key: str) -> Optional[str]:
        bm    = self.bms[self._sel]
        nf    = len(self._FIELDS)
        fname = self._FIELDS[self._ef]

        if key == "ESC":
            self._ef = -1; self.dirty_frame = True
        elif key in ("DOWN", "TAB"):
            self._commit_field(bm)
            self._ef = (self._ef + 1) % nf
            self._ev = str(getattr(bm, self._FIELDS[self._ef])); self.dirty_frame = True
        elif key == "UP":
            self._commit_field(bm)
            self._ef = (self._ef - 1) % nf
            self._ev = str(getattr(bm, self._FIELDS[self._ef])); self.dirty_frame = True
        elif key == "ENTER":
            self._commit_field(bm)
            self._ef = -1; self.dirty_frame = True
        elif key == "BACKSPACE":
            self._ev = self._ev[:-1]; self.dirty_frame = True
        elif fname == "tls" and key == " ":
            bm.tls = not bm.tls
            self._ev = "Yes" if bm.tls else "No"; self.dirty_frame = True
        elif len(key) == 1 and (key.isprintable() or key == ' '):
            self._ev += key; self.dirty_frame = True
        return None

    def _commit_field(self, bm: Bookmark):
        fname = self._FIELDS[self._ef]
        if fname == "port":
            bm.port = int(self._ev) if self._ev.isdigit() else 6667
        elif fname == "tls":
            bm.tls = self._ev.lower() in ('1','yes','true','y')
        else:
            setattr(bm, fname, self._ev)

    # ── Chat session ────────────────────────────────────────────────────────────

    async def _chat_session(self):
        bm = self.bms[self._sel]
        self.lines = []
        self.scroll = 0
        self._inp = ""; self._cur = 0
        self._hist = []; self._hidx = -1
        self.dirty_frame = True
        self.dirty_chat = True
        self.dirty_users = True
        self.dirty_status = True
        self.dirty_input = True

        self._sys(f"Connecting to {bm.server}:{bm.port}" +
                  (" [TLS]" if bm.tls else "") + "...")

        # Run connect + read as a single background task so the UI loop
        # starts immediately — ESC works during the connect timeout too.
        irc_task = asyncio.create_task(self._connect_and_read(bm))
        try:
            await self._ui_loop()
        finally:
            await self.irc.disconnect()
            irc_task.cancel()
            try:
                await asyncio.wait_for(irc_task, timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    async def _connect_and_read(self, bm: Bookmark):
        ok = await self.irc.connect(bm)
        if not ok:
            self._sys("Connection failed. Press Esc to return to startup.")
            self.dirty_chat = True
        else:
            await self.irc.read_loop()

    async def _ui_loop(self):
        """Input + redraw loop for the chat screen."""
        await self._wr(_cls())   # clear startup screen before drawing chat
        first = True

        while not self._back:
            out = []
            if self.dirty_frame or first:
                out.append(self._scr.draw_frame(self.show_users))
                self.dirty_frame = False
                self.dirty_chat = self.dirty_users = True
                first = False
            if self.dirty_chat:
                out.append(self._scr.draw_chat(
                    self.lines, self.scroll, self.show_users))
                self.dirty_chat = False
            if self.dirty_users:
                out.append(self._scr.draw_users(
                    self.irc.users, self.show_users))
                self.dirty_users = False
            if self.dirty_status:
                out.append(self._scr.draw_status(
                    self.irc.server, self.irc.port,
                    self.irc.channel, self.irc.nick,
                    self.irc.connected, self.scroll))
                self.dirty_status = False
            if self.dirty_input or out:
                out.append(self._scr.draw_input(self._inp, self._cur))
                self.dirty_input = False
            if out:
                await self._wr("".join(out))

            key = await self._read_key(0.05)
            if key:
                await self._chat_key(key)

    async def _chat_key(self, key: str):
        # Any key other than TAB itself ends the current tab-complete
        # cycle -- see _tab_complete()'s docstring for the real bug
        # this (and that method's own rewrite) fixes. TAB must not
        # clear this, or repeated presses could never continue cycling.
        if key != "TAB":
            self._tab_state = None

        # ── Scroll (PgUp/PgDn or Up/Down when input empty, Ctrl+U/D) ─────────
        scroll_up   = key in ("PGUP", "\x15")         # PgUp or Ctrl+U
        scroll_down = key in ("PGDN", "\x04")         # PgDn or Ctrl+D
        if not scroll_up and not scroll_down and not self._inp:
            if key == "UP":   scroll_up   = True
            if key == "DOWN": scroll_down = True
        if scroll_up:
            self.scroll = min(self.scroll + 10, _MAX_SCROLL)
            self.dirty_chat = self.dirty_status = True; return
        if scroll_down:
            self.scroll = max(self.scroll - 10, 0)
            self.dirty_chat = self.dirty_status = True; return

        # ── Panel toggle ──────────────────────────────────────────────────────
        if key == "F2":
            self.show_users = not self.show_users
            self.dirty_frame = True; return

        # ── Return to startup (ESC or Ctrl+Q) ─────────────────────────────────
        if key in ("ESC", "\x11"):
            self._back = True; return

        # ── Submit ────────────────────────────────────────────────────────────
        if key == "ENTER":
            text = self._inp.strip()
            if text:
                if self._hist and self._hist[-1] != text:
                    self._hist.append(text)
                elif not self._hist:
                    self._hist.append(text)
                self._hidx = -1
                await self.irc.command(text)
            self._inp = ""; self._cur = 0
            self.scroll = 0
            self.dirty_chat = self.dirty_status = self.dirty_input = True
            return

        # ── History (only when input has text) ────────────────────────────────
        if key == "UP" and self._inp:
            if self._hist:
                self._hidx = max(0, (len(self._hist) - 1
                                     if self._hidx < 0 else self._hidx - 1))
                self._inp = self._hist[self._hidx]
                self._cur = len(self._inp)
                self.dirty_input = True
            return
        if key == "DOWN" and self._hidx >= 0:
            self._hidx += 1
            if self._hidx >= len(self._hist):
                self._hidx = -1; self._inp = ""
            else:
                self._inp = self._hist[self._hidx]
            self._cur = len(self._inp)
            self.dirty_input = True; return

        # ── Editing ───────────────────────────────────────────────────────────
        if key == "BACKSPACE":
            if self._cur > 0:
                self._inp = self._inp[:self._cur-1] + self._inp[self._cur:]
                self._cur -= 1; self.dirty_input = True
        elif key == "DELETE":
            if self._cur < len(self._inp):
                self._inp = self._inp[:self._cur] + self._inp[self._cur+1:]
                self.dirty_input = True
        elif key == "LEFT":
            if self._cur > 0:
                self._cur -= 1; self.dirty_input = True
        elif key == "RIGHT":
            if self._cur < len(self._inp):
                self._cur += 1; self.dirty_input = True
        elif key == "HOME":
            self._cur = 0; self.dirty_input = True
        elif key == "END":
            self._cur = len(self._inp); self.dirty_input = True
        elif key == "TAB":
            self._tab_complete()
        elif len(key) == 1 and (key.isprintable() or key == ' '):
            self._inp = self._inp[:self._cur] + key + self._inp[self._cur:]
            self._cur += 1; self._hidx = -1; self.dirty_input = True

    def _tab_complete(self):
        """Complete the partial nick before the cursor; repeated Tab
        presses (with no other key in between) cycle through every
        matching nick.

        Real bug found in a deep review: this used to unconditionally
        re-derive `partial` from the text immediately before the
        cursor on EVERY call. That's correct for the FIRST Tab press,
        but on the SECOND consecutive press the buffer already
        contains the previous completion's output (e.g. "Alice: "),
        which no longer looks like a bare nick prefix at all -- so
        `matches` came back empty and the function returned before
        ever reaching the `_tabidx` increment. In practice, repeated
        Tab never cycled to a different candidate; it only ever
        offered the very first match, no matter how many other users
        shared that prefix. Fixed by remembering the ORIGINAL prefix/
        matches from the last completion and detecting a same-state
        follow-up Tab press (buffer unchanged since we last wrote it)
        as "continue this cycle" rather than "start a new one".
        """
        if not self.irc.users:
            return
        text = self._inp[:self._cur]

        if self._tab_state is not None and text == self._tab_state['output']:
            pre = self._tab_state['pre']
            matches = self._tab_state['matches']
            self._tabidx = (self._tabidx + 1) % len(matches)
        else:
            parts = text.split()
            if not parts:
                self._tab_state = None
                return
            partial = parts[-1].lower()
            pre = text[:len(text) - len(partial)]
            matches = [u for u in self.irc.users if u.lower().startswith(partial)]
            if not matches:
                self._tab_state = None
                return
            self._tabidx = 0

        full = matches[self._tabidx]
        sep  = ": " if not pre else " "
        tail = self._inp[self._cur:]
        output = pre + full + sep
        self._inp = output + tail
        self._cur = len(output)
        self._tab_state = {'pre': pre, 'matches': matches, 'output': output}
        self.dirty_input = True


# ── Entry point ────────────────────────────────────────────────────────────────

def _cfg_path(username: str) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, '..', '..'))
    d    = os.path.join(root, 'data', 'anetirc', 'users', username)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'anetirc.cfg')


async def launch_anetirc_telnet(user, session):
    """Drop-in replacement for anetirc_door.launch_anetirc_telnet."""
    username = (user or {}).get('username', 'BBSUser') or 'BBSUser'
    cfg      = _cfg_path(username)
    client   = ANetIRC(session, cfg)
    await client.run()
