# anetbbs/features/irc.py
"""
Asyncio-based IRC client used by the telnet/SSH/rlogin IRC chat menu.

This is a focused rewrite of the previous client that had several bugs:
  - PING parser assumed an exact byte offset and dropped multi-server prefixes
  - readline() was used but not buffered correctly across partial reads
  - JOIN / PART / NAMES / numerics were ignored — users only saw PRIVMSG
  - self.session.user was indexed as a dict without checking, raising KeyError
  - no graceful close on connect failure or PING timeout
  - quit() left the writer half-open if QUIT bombed

The new version:
  - Parses canonical IRC protocol lines (RFC 1459/2812)
  - Handles PING auto-reply, JOIN/PART/NAMES, MOTD/welcome numerics, ERROR
  - Surfaces all server text to the user with sensible formatting
  - Tolerates user being a dict OR a model (matches our session.user variants)
  - Cleanly tears down on quit / disconnect / exception
"""
import asyncio
import logging
import ssl
from typing import Optional, Set

from .irc_format import to_ansi as _to_ansi

logger = logging.getLogger(__name__)


def _u(user, field, default=None):
    """Read a field from a user that may be a dict or a model instance."""
    if user is None:
        return default
    if isinstance(user, dict):
        return user.get(field, default)
    return getattr(user, field, default)


class IRCClient:
    """Asyncio IRC client with one-channel-at-a-time terminal output.

    Attaches to a session (telnet/SSH/rlogin) and proxies messages between
    the user's terminal and the IRC server. The chat loop in IRCChat handles
    user input; this class handles I/O with the IRC server."""

    def __init__(self, session):
        self.session = session
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.nick: str = ""
        self.username: str = ""
        self.realname: str = ""
        self.channels: Set[str] = set()
        self.current_channel: str = ""
        self.connected: bool = False
        self._recv_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self, server: str, port: int, use_ssl: bool = False,
                      nick: str = "", username: str = "", realname: str = "") -> bool:
        try:
            ctx = ssl.create_default_context() if use_ssl else None
            self.reader, self.writer = await asyncio.open_connection(
                server, port, ssl=ctx)
        except (OSError, ssl.SSLError) as exc:
            await self._user(f'\r\nIRC connect failed: {exc}\r\n')
            return False

        self.nick = nick or _u(self.session.user, 'username', 'guest') or 'guest'
        self.username = (username or self.nick).lower()
        self.realname = realname or self.nick

        await self.send(f'NICK {self.nick}')
        await self.send(f'USER {self.username} 0 * :{self.realname}')
        self.connected = True
        await self._user(f'\r\nConnected to {server}:{port} as {self.nick}\r\n')
        return True

    async def send(self, line: str) -> None:
        """Send a raw IRC line (no CRLF — added here)."""
        if self.writer is None or self.writer.is_closing():
            return
        try:
            self.writer.write((line + '\r\n').encode('utf-8', errors='replace'))
            await self.writer.drain()
        except (OSError, ConnectionError):
            self.connected = False

    async def join(self, channel: str) -> None:
        ch = channel if channel.startswith('#') else '#' + channel
        await self.send(f'JOIN {ch}')

    async def part(self, channel: str) -> None:
        await self.send(f'PART {channel}')

    async def privmsg(self, target: str, text: str) -> None:
        await self.send(f'PRIVMSG {target} :{text}')

    async def quit(self, message: str = 'Goodbye') -> None:
        if not self.connected:
            return
        try:
            await self.send(f'QUIT :{message}')
        finally:
            self.connected = False
            if self._recv_task:
                self._recv_task.cancel()
            if self.writer:
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except (OSError, ConnectionError):
                    pass

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    async def start_receiving(self) -> None:
        self._recv_task = asyncio.current_task()
        try:
            while self.connected and self.reader:
                try:
                    raw = await self.reader.readline()
                except (OSError, ConnectionError):
                    break
                if not raw:
                    break
                line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
                if not line:
                    continue
                try:
                    await self._handle_line(line)
                except Exception:
                    logger.exception('IRC line handler crashed: %s', line)
        finally:
            self.connected = False
            await self._user('\r\n[IRC connection closed]\r\n')

    async def _handle_line(self, line: str) -> None:
        # PING — first thing to handle (before generic prefix parse)
        if line.startswith('PING'):
            payload = line[5:] if len(line) > 5 else ''
            await self.send(f'PONG {payload}')
            return

        # Strip leading prefix ":nick!user@host" or ":server"
        prefix = ''
        rest = line
        if line.startswith(':'):
            prefix, _, rest = line[1:].partition(' ')

        # Trailing parameter (after " :") — preserved with embedded spaces
        trailing = ''
        if ' :' in rest:
            head, trailing = rest.split(' :', 1)
            args = head.split()
        else:
            args = rest.split()
        if not args:
            return
        cmd = args[0].upper()
        params = args[1:]

        if cmd == 'PRIVMSG' and len(params) >= 1:
            sender = prefix.split('!', 1)[0]
            target = params[0]
            colored = _to_ansi(trailing)
            # CTCP ACTION → render as "* nick action"
            if trailing.startswith('\x01ACTION ') and trailing.endswith('\x01'):
                action = _to_ansi(trailing[8:-1])
                if target.startswith('#'):
                    await self._user(f'\r\n\x1b[33m{target}\x1b[0m * {sender} {action}\r\n')
                else:
                    await self._user(f'\r\n* {sender} {action}\r\n')
            elif target.startswith('#'):
                await self._user(f'\r\n\x1b[33m{target}\x1b[0m \x1b[1m<{sender}>\x1b[0m {colored}\r\n')
            else:
                await self._user(f'\r\n\x1b[1m*{sender}*\x1b[0m {colored}\r\n')
        elif cmd == 'NOTICE' and len(params) >= 1:
            sender = prefix.split('!', 1)[0] or 'server'
            await self._user(f'\r\n\x1b[36m-{sender}-\x1b[0m {_to_ansi(trailing)}\r\n')
        elif cmd == 'JOIN':
            who = prefix.split('!', 1)[0]
            chan = params[0] if params else trailing
            if who == self.nick:
                self.channels.add(chan)
                self.current_channel = chan
                await self._user(f'\r\n*** Joined {chan}\r\n')
            else:
                await self._user(f'\r\n--> {who} joined {chan}\r\n')
        elif cmd == 'PART':
            who = prefix.split('!', 1)[0]
            chan = params[0] if params else trailing
            if who == self.nick:
                self.channels.discard(chan)
                if self.current_channel == chan:
                    self.current_channel = next(iter(self.channels), '')
                await self._user(f'\r\n*** Left {chan}\r\n')
            else:
                await self._user(f'\r\n<-- {who} left {chan}\r\n')
        elif cmd == 'KICK' and len(params) >= 2:
            chan, victim = params[0], params[1]
            if victim == self.nick:
                self.channels.discard(chan)
                if self.current_channel == chan:
                    self.current_channel = next(iter(self.channels), '')
                await self._user(f'\r\n*** You were kicked from {chan}: {trailing}\r\n')
            else:
                await self._user(f'\r\n*** {victim} kicked from {chan}: {trailing}\r\n')
        elif cmd == 'NICK':
            old = prefix.split('!', 1)[0]
            new = trailing or (params[0] if params else '')
            if old == self.nick:
                self.nick = new
                await self._user(f'\r\n*** Now known as {new}\r\n')
            else:
                await self._user(f'\r\n*** {old} is now known as {new}\r\n')
        elif cmd == 'QUIT':
            who = prefix.split('!', 1)[0]
            await self._user(f'\r\n*** {who} quit ({trailing})\r\n')
        elif cmd == 'ERROR':
            await self._user(f'\r\n*** Server error: {trailing}\r\n')
            self.connected = False
        elif cmd == '001':
            self.connected = True
            await self._user(f'\r\n{trailing}\r\n')
        elif cmd in ('002', '003', '004', '005', '372', '375', '376',
                     '251', '252', '253', '254', '255', '265', '266'):
            if trailing:
                await self._user(f'{trailing}\r\n')
        elif cmd == '353' and len(params) >= 3:
            # NAMES reply
            chan = params[2]
            await self._user(f'Names in {chan}: {trailing}\r\n')
        elif cmd == '366':
            # End of NAMES
            pass
        elif cmd in ('432', '433', '436'):
            await self._user(f'\r\n*** Nick error: {trailing}\r\n')
        elif cmd in ('401', '403', '404', '405', '442', '482'):
            await self._user(f'\r\n\x1b[31m*** {cmd} {trailing}\x1b[0m\r\n')
        elif cmd == 'TOPIC' and len(params) >= 1:
            who = prefix.split('!', 1)[0]
            await self._user(f'\x1b[36m*** {who} set topic of {params[0]}: '
                             f'{_to_ansi(trailing)}\x1b[0m\r\n')
        elif cmd == '332' and len(params) >= 2:
            await self._user(f'\x1b[36m*** Topic of {params[1]}: '
                             f'{_to_ansi(trailing)}\x1b[0m\r\n')
        elif cmd == 'MODE' and len(params) >= 1:
            who = prefix.split('!', 1)[0] or 'server'
            modes = ' '.join(params[1:]) + ((' ' + trailing) if trailing else '')
            await self._user(f'*** Mode {params[0]} {modes} (by {who})\r\n')
        elif cmd in ('311', '312', '313', '317', '318', '319', '330',
                     '671', '276', '338'):
            # WHOIS replies — params[1] is the target nick
            target = params[1] if len(params) > 1 else ''
            tail = trailing or ' '.join(params[2:])
            await self._user(f'\x1b[36m  WHOIS {target}: {tail}\x1b[0m\r\n')
        elif cmd == '321':
            await self._user('\r\n  --- Channel listings ---\r\n')
        elif cmd == '322' and len(params) >= 3:
            chan, users = params[1], params[2]
            await self._user(f'  {chan:<25} {users:>5} users  '
                             f'{_to_ansi(trailing)}\r\n')
        elif cmd == '323':
            await self._user('  --- end of /list ---\r\n')
        # else: silently drop other numerics

    async def _user(self, text: str) -> None:
        try:
            await self.session.write(text)
        except Exception:
            logger.debug('Failed to write to session', exc_info=True)
