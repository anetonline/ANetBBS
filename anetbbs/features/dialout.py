# anetbbs/features/dialout.py
"""
Dial-out gateway — telnet/SSH/rlogin OUT from a BBS session.

Lets a logged-in telnet/SSH/rlogin user open an outbound connection to
another BBS without leaving ANET. We proxy bytes in both directions until
either side closes or the user types the escape sequence (Ctrl+] then 'q').

Synchronet has the same idea — they call it 'dial-out' or 'gateway'.
"""
import asyncio
import logging

from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD, load_menu_ansi


logger = logging.getLogger(__name__)


# Stock dial-out directory — minimal seed so the menu isn't empty on a fresh
# install. Sysop adds more (or removes A-Net Online) via /admin/dialout.
DEFAULT_DIRECTORY = [
    ('A-Net Online', 'bbs.a-net.online', 1337, 'telnet'),
]


class DialoutMenu:
    """Top-level menu — pick a destination from the directory or enter custom."""

    def __init__(self, session):
        self.session = session

    def _load_directory(self):
        """Pull directory from both PeerBbs (web BBS directory, Local tab)
        and DialoutDestination (legacy terminal-only entries), merged by
        hostname. PeerBbs entries come first; DialoutDestination fills any
        hostnames not already covered. Falls back to DEFAULT_DIRECTORY if
        both tables are empty or unavailable.

        All DB queries are wrapped in an explicit app_context() — the
        terminal runs outside gunicorn so there is no ambient Flask context."""
        try:
            from .. import models
            from ..models import db
            from .bbs_ui import _app
            results = []
            seen = set()

            with _app().app_context():
                # Discard any cached session so we always read the latest data
                # written by the web process — without this, scoped_session may
                # return stale rows from a previous transaction.
                db.session.remove()

                # Primary: approved+active PeerBbs rows (web /bbses/ admin)
                try:
                    peers = (models.PeerBbs.query
                             .filter_by(is_active=True, is_approved=True)
                             .order_by(models.PeerBbs.name).all())
                    for r in peers:
                        h = r.hostname.lower()
                        if h not in seen:
                            seen.add(h)
                            results.append((r.name, r.hostname,
                                            r.telnet_port or 23, 'telnet'))
                except Exception:
                    pass

                # Secondary: DialoutDestination for terminal-only entries
                try:
                    dests = (models.DialoutDestination.query
                             .filter_by(is_active=True)
                             .order_by(models.DialoutDestination.sort_order,
                                       models.DialoutDestination.name).all())
                    for r in dests:
                        h = r.hostname.lower()
                        if h not in seen:
                            seen.add(h)
                            results.append((r.name, r.hostname,
                                            r.port or 23,
                                            r.protocol or 'telnet'))
                except Exception:
                    pass

            if results:
                return results
        except Exception:
            pass
        return DEFAULT_DIRECTORY

    async def show_menu(self):
        while True:
            destinations = self._load_directory()
            ansi = load_menu_ansi('dialout')
            if ansi:
                self.session.writer.write(b'\x1b[2J\x1b[H' + ansi)
                await self.session.writer.drain()
            else:
                await self.session.write(banner('Dial Out - Visit Another BBS'))
                for i, (name, host, port, proto) in enumerate(destinations, 1):
                    line = f"  [{i:2d}] {name:<22} {host}:{port}  ({proto})"
                    line = line.replace(
                        f'[{i:2d}]',
                        f"{FG['yel']}{BOLD}[{i:2d}]{RESET}{FG['grn']}", 1)
                    await self.session.write(f"{FG['grn']}{line}{RESET}\r\n")
                await self.session.write(footer() + '\r\n')
                await self.session.write(
                    f"{FG['cyan']}  [C] Custom destination  [Q] Back{RESET}\r\n\r\n")
            choice = (await self.session.read_line(_prompt('Pick a BBS: '))
                      or '').strip().upper()
            if not choice or choice == 'Q':
                return
            if choice == 'C':
                await self._custom_destination()
                continue
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(destinations):
                    name, host, port, proto = destinations[idx]
                    await self._connect(name, host, port, proto)
            except ValueError:
                await self.session.write(
                    f"\r\n{FG['red']}Invalid choice.{RESET}\r\n")

    async def _custom_destination(self):
        host = (await self.session.read_line('Hostname: ')).strip()
        if not host:
            return
        port_s = (await self.session.read_line('Port [23]: ')).strip()
        try:
            port = int(port_s) if port_s else 23
        except ValueError:
            await self.session.write('\r\nInvalid port.\r\n')
            return
        await self._connect(host, host, port, 'telnet')

    async def _connect(self, name, host, port, proto):
        await self.session.write(
            f"\r\n{FG['cyan']}Connecting to {name} ({host}:{port}) - "
            f"Press Ctrl+] then Q to disconnect.{RESET}\r\n\r\n")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=15)
        except (OSError, asyncio.TimeoutError) as exc:
            await self.session.write(
                f"\r\n{FG['red']}Connect failed: {exc}{RESET}\r\n")
            await self.session.read_line('Press Enter...')
            return
        await self._proxy(reader, writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    async def _proxy(self, remote_reader, remote_writer):
        """Bidirectional byte pump between the local user and the remote
        BBS, with proper telnet protocol handling.

        Three problems the previous implementation had:

        1. Line-buffered user reads — `session.read_line()` blocked until
           Enter, so single-key prompts (bot-defense ESC, hotkey menus,
           game inputs) never made it to the remote. Now we read one
           raw byte at a time via `session.read_raw(1)`.
        2. No telnet IAC negotiation — remote BBS asks "do you support
           terminal type?" and gets no reply, so it falls back to a
           dumb-terminal mode with no ANSI. Now we run a minimal IAC
           state machine: announce WILL TTYPE / WILL NAWS / WILL BINARY,
           respond to peer DO/WILL queries, send our terminal type
           ("ANSI") on demand, and pass everything else through.
        3. Ctrl+] escape was declared but never wired. Now genuinely
           works: press Ctrl+], then 'Q' to disconnect, any other key
           to return to the session.

        Both ends speak CP437; we never decode/encode — just shuttle
        bytes and strip telnet protocol bytes from the remote-to-user
        direction so the user terminal doesn't see raw 0xff IACs.
        """
        IAC, DO, DONT, WILL, WONT = 255, 253, 254, 251, 252
        SB, SE = 250, 240
        OPT_BINARY, OPT_ECHO, OPT_SGA, OPT_TTYPE, OPT_NAWS = 0, 1, 3, 24, 31
        ESCAPE_BYTE = 0x1d   # Ctrl+]

        stop = asyncio.Event()
        in_escape = False  # closure-local via nonlocal in user_to_remote

        def _handle_option(cmd, opt):
            """Reply policy for IAC DO/DONT/WILL/WONT from the remote."""
            if cmd == DO:
                if opt in (OPT_TTYPE, OPT_NAWS, OPT_BINARY, OPT_SGA):
                    return bytes([IAC, WILL, opt])
                return bytes([IAC, WONT, opt])
            if cmd == WILL:
                if opt in (OPT_BINARY, OPT_SGA, OPT_ECHO):
                    return bytes([IAC, DO, opt])
                return bytes([IAC, DONT, opt])
            return b''

        def _handle_subnegotiation(sub):
            """IAC SB <opt> ... IAC SE — return our reply (if any)."""
            if not sub:
                return b''
            opt = sub[0]
            if opt == OPT_TTYPE and len(sub) >= 2 and sub[1] == 1:
                # SEND — respond with our terminal type.
                return (bytes([IAC, SB, OPT_TTYPE, 0])
                        + b'ANSI' + bytes([IAC, SE]))
            return b''

        # Up-front negotiation: announce what we support so the remote
        # BBS knows we're a real terminal and switches into ANSI mode.
        # Sent BEFORE the proxy threads start so it lands first.
        try:
            remote_writer.write(bytes([
                IAC, WILL, OPT_TTYPE,
                IAC, WILL, OPT_NAWS,
                IAC, WILL, OPT_BINARY,
                IAC, DO,   OPT_BINARY,
                IAC, DO,   OPT_SGA,
                IAC, DO,   OPT_ECHO,
            ]))
            # Volunteer our window size proactively — many BBSes use
            # NAWS to decide between 80- and 132-col layouts.
            remote_writer.write(
                bytes([IAC, SB, OPT_NAWS, 0, 80, 0, 24, IAC, SE]))
            await remote_writer.drain()
        except (OSError, ConnectionError):
            return

        async def remote_to_user():
            """Strip telnet IACs, forward the rest to the user's terminal."""
            state = 'data'      # 'data' / 'iac' / 'cmd' / 'sb'
            cmd_byte = 0
            sub_buf = bytearray()
            out = bytearray()
            try:
                while not stop.is_set():
                    chunk = await remote_reader.read(4096)
                    if not chunk:
                        break
                    out.clear()
                    for b in chunk:
                        if state == 'data':
                            if b == IAC:
                                state = 'iac'
                            else:
                                out.append(b)
                        elif state == 'iac':
                            if b == IAC:
                                # Escaped 0xff in data stream
                                out.append(IAC)
                                state = 'data'
                            elif b in (DO, DONT, WILL, WONT):
                                cmd_byte = b
                                state = 'cmd'
                            elif b == SB:
                                sub_buf.clear()
                                state = 'sb'
                            else:
                                # Other 2-byte IAC commands (NOP/AYT/etc) — eat.
                                state = 'data'
                        elif state == 'cmd':
                            reply = _handle_option(cmd_byte, b)
                            if reply:
                                try:
                                    remote_writer.write(reply)
                                    await remote_writer.drain()
                                except (OSError, ConnectionError):
                                    return
                            state = 'data'
                        elif state == 'sb':
                            if b == IAC:
                                state = 'sb_iac'
                            else:
                                sub_buf.append(b)
                        elif state == 'sb_iac':
                            if b == SE:
                                reply = _handle_subnegotiation(bytes(sub_buf))
                                if reply:
                                    try:
                                        remote_writer.write(reply)
                                        await remote_writer.drain()
                                    except (OSError, ConnectionError):
                                        return
                                state = 'data'
                            else:
                                # Doubled IAC inside subnegotiation
                                sub_buf.append(IAC)
                                if b != IAC:
                                    sub_buf.append(b)
                                state = 'sb'
                    if out:
                        try:
                            self.session.writer.write(bytes(out))
                            await self.session.writer.drain()
                        except (OSError, ConnectionError):
                            break
            except (OSError, ConnectionError):
                pass
            finally:
                stop.set()

        async def user_to_remote():
            """Read raw bytes from the user, watch for Ctrl+] escape,
            pass everything else to the remote. Doubles any 0xff byte
            (rare from a real user but required by the telnet spec)."""
            nonlocal in_escape
            try:
                while not stop.is_set():
                    try:
                        data = await self.session.read_raw(1)
                    except AttributeError:
                        # Older session shim — fall back to single-char
                        # read off the reader directly.
                        data = await self.session.reader.read(1)
                    if not data:
                        break
                    byte = data[0] if isinstance(data, (bytes, bytearray)) else \
                           ord(data) if isinstance(data, str) and data else 0
                    if not byte:
                        continue

                    if in_escape:
                        in_escape = False
                        if byte in (ord('q'), ord('Q')):
                            break
                        # Anything else: silently return to the session.
                        continue

                    if byte == ESCAPE_BYTE:
                        in_escape = True
                        continue

                    try:
                        if byte == IAC:
                            remote_writer.write(bytes([IAC, IAC]))
                        else:
                            remote_writer.write(bytes([byte]))
                        await remote_writer.drain()
                    except (OSError, ConnectionError):
                        break
            except (OSError, ConnectionError):
                pass
            finally:
                stop.set()

        await asyncio.gather(remote_to_user(), user_to_remote(),
                             return_exceptions=True)
        await self.session.write(
            f"\r\n{FG['cyan']}[Returned to ANetBBS]{RESET}\r\n")
