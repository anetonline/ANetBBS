# anetbbs/features/chat.py
from .mrc_chat import MRCChat
from .anetirc2 import launch_anetirc_telnet
from ..core.protocols import SessionProtocol


def _chat_flags(session):
    """Return UserAccessFlags for this session, or None (unrestricted)."""
    try:
        from anetbbs.models import UserAccessFlags
        from .bbs_ui import _app
        uid = (session.user or {}).get('id')
        if not uid:
            return None
        with _app().app_context():
            return UserAccessFlags.query.filter_by(user_id=uid).first()
    except Exception:
        return None


class ChatManager:
    def __init__(self, session: SessionProtocol):
        self.session = session
        self.chat_systems = {
            'mrc': MRCChat(session),
        }

    async def show_menu(self):
        """Chat system selection — local, IRC (ANetIRC door), MRC."""
        from .ansi_ui import banner, menu_item, footer, prompt as _p, write_menu_art, ui_width
        while True:
            flags = _chat_flags(self.session)
            _w = ui_width(self.session)
            if not await write_menu_art(self.session, 'chat'):
                await self.session.write('\x1b[2J\x1b[H')
                await self.session.write(banner('Chat Systems', _w))
                for hk, lbl in (('1', 'Local Chat'),
                                ('2', 'IRC Chat (A-Net IRC)'),
                                ('3', 'MRC Chat (Inter-BBS)'),
                                ('Q', 'Return to Main Menu')):
                    await self.session.write(menu_item(hk, lbl, _w) + '\r\n')
                await self.session.write('\r\n' + footer(_w) + '\r\n')
            choice = (await self.session.read_line(_p('Choice: ')) or '').strip().upper()
            if choice == "1":
                await self.local_chat()
            elif choice == "2":
                if flags and flags.no_irc:
                    await self.session.write(
                        '\r\n\x1b[1;31mYour IRC access has been suspended.\x1b[0m\r\n')
                else:
                    await launch_anetirc_telnet(self.session.user, self.session)
            elif choice == "3":
                if flags and flags.no_mrc:
                    await self.session.write(
                        '\r\n\x1b[1;31mYour MRC access has been suspended.\x1b[0m\r\n')
                else:
                    self.current_chat = self.chat_systems['mrc']
                    await self.current_chat.show_menu()
            elif choice in ('4', 'Q') or not choice:
                break
            else:
                await self.session.write("\r\nInvalid choice. Please try again.\r\n")

    async def local_chat(self):
        # Was previously a stub that only ever echoed back to the
        # sender -- never actually broadcast to other nodes, which is
        # why two logged-in users couldn't hear each other. Delegates
        # to the same real-time broadcast/queue machinery the
        # main-menu 'multinode' action already used (that one was
        # fully working, just never wired into a default menu).
        from .multinode import run_chat_session
        await run_chat_session(self.session)
