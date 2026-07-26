# anetbbs/features/irc_chat.py
"""
Telnet/SSH/rlogin IRC chat menu.

Replaces the previous version that had:
  - no /nick, /msg, /me, /list commands
  - no channel switching with multiple joined channels
  - no input loop running concurrently with the receive task (each typed line
    blocked the receive thread because it awaited read_line synchronously)
  - PARTed channel handling left current_channel = None and silently
    swallowed user input

The new chat loop:
  - runs the IRC receive task in the background
  - reads user input on the main task
  - supports /join /part /msg /me /nick /list /channels /quit /raw
"""
import asyncio
import logging

from .base_chat import BaseChatSystem
from .irc import IRCClient
from ..core.protocols import SessionProtocol


logger = logging.getLogger(__name__)


class IRCChat(BaseChatSystem):
    def __init__(self, session: SessionProtocol):
        super().__init__(session)
        self.irc_client = None

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    async def connect(self, server, port, nick='', use_ssl=False):
        try:
            self.irc_client = IRCClient(self.session)
            ok = await self.irc_client.connect(
                server=server, port=port, use_ssl=use_ssl, nick=nick)
            if ok:
                # Run the receive loop in the background so the user input
                # loop in chat_loop() can run concurrently.
                self._recv_task = asyncio.create_task(
                    self.irc_client.start_receiving())
            return ok
        except Exception as exc:
            await self.session.write(f'\r\nIRC connect error: {exc}\r\n')
            return False

    async def disconnect(self):
        if self.irc_client:
            try:
                await self.irc_client.quit()
            except Exception:
                logger.debug('quit() failed', exc_info=True)
            self.irc_client = None

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _load_presets(self):
        """Return list of active IrcPreset rows ordered by order,name."""
        try:
            # Plain pyflakes has no `# noqa` suppression -- this import is
            # only here to trigger app init as a side effect, so give it a
            # real reference to count as "used".
            from ..web.admin import admin_bp
            assert admin_bp is not None
            from ..models import IrcPreset
            from ..features.bbs_ui import _app
            with _app().app_context():
                return (IrcPreset.query
                        .filter_by(is_active=True)
                        .order_by(IrcPreset.order, IrcPreset.name)
                        .all())
        except Exception:
            return []

    async def show_menu(self):
        from .ansi_ui import (write_menu_art, banner, menu_item, footer,
                              prompt as _p, FG, RESET)
        while True:
            # Build preset map regardless of ANSI mode so numbered choices
            # always work the same way.
            presets = self._load_presets()
            preset_map = {str(i + 1): p for i, p in enumerate(presets)}

            if not await write_menu_art(self.session, 'irc_chat'):
                await self.session.write('\x1b[2J\x1b[H')
                await self.session.write(banner('IRC Chat'))
                for key, p in preset_map.items():
                    ssl_tag = ' (SSL)' if p.use_ssl else ''
                    await self.session.write(
                        menu_item(key,
                                  f'{p.name} — {p.server}:{p.port}{ssl_tag}') + '\r\n')
                await self.session.write(
                    menu_item('M', 'Manual connect to any server') + '\r\n')
                await self.session.write(
                    menu_item('Q', 'Return to Chat menu') + '\r\n')
                await self.session.write('\r\n' + footer() + '\r\n')

            choice = (await self.session.read_line(_p('Choice: ')) or '').strip().upper()

            if choice == 'Q' or not choice:
                if self.irc_client:
                    await self.disconnect()
                break
            elif choice in preset_map:
                p = preset_map[choice]
                default_nick = p.default_nick or self.session.user.get('username', '')
                nick = (await self.session.read_line(
                    f"Nick [{default_nick}]: ")).strip() or default_nick
                if await self.connect(p.server, p.port, nick=nick, use_ssl=p.use_ssl):
                    if p.channels:
                        for ch in (c.strip() for c in p.channels.split(',') if c.strip()):
                            await self.irc_client.join(ch)
                    await self.chat_loop()
            elif choice == 'M':
                server = (await self.session.read_line(
                    "Server [irc.libera.chat]: ")) or 'irc.libera.chat'
                port_s = (await self.session.read_line(
                    "Port [6667 / 6697 for SSL]: ")) or '6667'
                try:
                    port = int(port_s)
                except ValueError:
                    await self.session.write('\r\nInvalid port.\r\n')
                    continue
                use_ssl = (await self.session.read_line(
                    "SSL? (y/N): ")).strip().lower().startswith('y')
                nick = (await self.session.read_line(
                    "Nickname [your username]: ")).strip()
                channels = (await self.session.read_line(
                    "Auto-join channels (comma-separated, optional): "))
                if await self.connect(server, port, nick=nick, use_ssl=use_ssl):
                    if channels.strip():
                        for ch in (c.strip() for c in channels.split(',') if c.strip()):
                            await self.irc_client.join(ch)
                    await self.chat_loop()
            else:
                await self.session.write(f'\r\n{FG["red"]}Unknown choice.{RESET}\r\n')

    # ------------------------------------------------------------------
    # Main chat loop
    # ------------------------------------------------------------------

    async def chat_loop(self):
        if not self.irc_client:
            return

        await self.session.write(
            '\r\nIRC chat commands:\r\n'
            '  /join #chan         join channel\r\n'
            '  /part [#chan]       leave channel (default: current)\r\n'
            '  /switch #chan       set current channel for plain text\r\n'
            '  /msg nick text      private message\r\n'
            '  /me action          CTCP action\r\n'
            '  /nick newnick       change your nickname\r\n'
            '  /topic [text]       view or set channel topic\r\n'
            '  /whois nick         look up a user\r\n'
            '  /list [pattern]     list channels on the server\r\n'
            '  /mode <args>        set/query modes\r\n'
            '  /channels           your joined channels\r\n'
            '  /raw <line>         send raw IRC command\r\n'
            '  /quit               exit IRC\r\n\r\n')
        try:
            while self.irc_client and self.irc_client.connected:
                prompt = (f'{self.irc_client.current_channel}> '
                          if self.irc_client.current_channel else '> ')
                line = await self.session.read_line(prompt)
                if line is None:
                    break
                line = line.rstrip('\r\n')
                if not line:
                    continue
                if line.startswith('/'):
                    cmd, _, arg = line[1:].partition(' ')
                    cmd = cmd.lower()
                    if cmd == 'quit':
                        break
                    elif cmd == 'join' and arg.strip():
                        await self.irc_client.join(arg.strip().split()[0])
                    elif cmd == 'part':
                        ch = (arg.strip().split()[0] if arg.strip()
                              else self.irc_client.current_channel)
                        if ch:
                            await self.irc_client.part(ch)
                        else:
                            await self.session.write('\r\nNo channel to part.\r\n')
                    elif cmd == 'nick' and arg.strip():
                        new = arg.strip().split()[0]
                        await self.irc_client.send(f'NICK {new}')
                    elif cmd == 'msg':
                        target_msg = arg.split(None, 1)
                        if len(target_msg) == 2:
                            await self.irc_client.privmsg(*target_msg)
                        else:
                            await self.session.write('\r\nUsage: /msg nick text\r\n')
                    elif cmd == 'me' and arg and self.irc_client.current_channel:
                        await self.irc_client.privmsg(
                            self.irc_client.current_channel,
                            f'\x01ACTION {arg}\x01')
                    elif cmd == 'channels':
                        if self.irc_client.channels:
                            await self.session.write(
                                '\r\nJoined channels: ' +
                                ', '.join(sorted(self.irc_client.channels)) + '\r\n')
                        else:
                            await self.session.write('\r\nNot in any channel.\r\n')
                    elif cmd == 'list':
                        await self.irc_client.send(
                            f'LIST {arg.strip()}' if arg.strip() else 'LIST')
                    elif cmd == 'whois' and arg.strip():
                        await self.irc_client.send(f'WHOIS {arg.strip().split()[0]}')
                    elif cmd == 'topic' and arg.strip():
                        parts = arg.strip().split(None, 1)
                        if parts[0].startswith('#'):
                            chan = parts[0]
                            new_topic = parts[1] if len(parts) > 1 else ''
                        else:
                            chan = self.irc_client.current_channel
                            new_topic = arg.strip()
                        if new_topic:
                            await self.irc_client.send(f'TOPIC {chan} :{new_topic}')
                        else:
                            await self.irc_client.send(f'TOPIC {chan}')
                    elif cmd == 'mode' and arg.strip():
                        await self.irc_client.send(f'MODE {arg.strip()}')
                    elif cmd in ('raw', 'quote') and arg.strip():
                        await self.irc_client.send(arg.strip())
                    elif cmd == 'switch' and arg.strip():
                        ch = arg.strip().split()[0]
                        if ch in self.irc_client.channels:
                            self.irc_client.current_channel = ch
                            await self.session.write(
                                f'\r\nNow on {ch}\r\n')
                        else:
                            await self.session.write(
                                f'\r\nNot in {ch}. /join first.\r\n')
                    else:
                        await self.session.write(f'\r\nUnknown command: /{cmd}\r\n')
                else:
                    if self.irc_client.current_channel:
                        await self.irc_client.privmsg(
                            self.irc_client.current_channel, line)
                    else:
                        await self.session.write(
                            '\r\nNot in a channel. Use /join #channel.\r\n')
        finally:
            await self.disconnect()
