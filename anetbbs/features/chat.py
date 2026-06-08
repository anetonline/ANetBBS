# anetbbs/features/chat.py
from .irc_chat import IRCChat
from .mrc_chat import MRCChat
from ..core.protocols import SessionProtocol

class ChatManager:
    def __init__(self, session: SessionProtocol):
        self.session = session
        self.chat_systems = {
            'irc': IRCChat(session),
            'mrc': MRCChat(session),
        }

    async def show_menu(self):
        """Chat system selection — local, IRC, MRC (terminal MRC reuses
        the local websocket bridge so web + terminal users share rooms)."""
        from .ansi_ui import banner, menu_item, footer, prompt as _p, load_menu_ansi
        while True:
            ansi = load_menu_ansi('chat')
            if ansi:
                self.session.writer.write(b'\x1b[2J\x1b[H' + ansi)
                await self.session.writer.drain()
            else:
                await self.session.write(banner('Chat Systems'))
                for hk, lbl in (('1', 'Local Chat'),
                                ('2', 'IRC Chat (multi-server)'),
                                ('3', 'MRC Chat (Inter-BBS)'),
                                ('4', 'Return to Main Menu')):
                    await self.session.write(menu_item(hk, lbl) + '\r\n')
                await self.session.write(footer() + '\r\n')
            choice = (await self.session.read_line(_p('Choice: ')) or '').strip()
            if choice == "1":
                await self.local_chat()
            elif choice == "2":
                self.current_chat = self.chat_systems['irc']
                await self.current_chat.show_menu()
            elif choice == "3":
                self.current_chat = self.chat_systems['mrc']
                await self.current_chat.show_menu()
            elif choice == "4":
                break
            else:
                await self.session.write("\r\nInvalid choice. Please try again.\r\n")

    async def local_chat(self):
        await self.session.write("\r\nLocal Chat Room (type /quit to exit)\r\n\r\n")
        while True:
            message = await self.session.read_line("> ")
            if message.lower() == '/quit':
                break
            await self.session.write(f"\r\n<{self.session.user['username']}> {message}\r\n")
