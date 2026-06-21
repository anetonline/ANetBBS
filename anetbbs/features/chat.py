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
        from .ansi_ui import banner, menu_item, footer, prompt as _p, load_menu_ansi
        while True:
            flags = _chat_flags(self.session)
            ansi = load_menu_ansi('chat')
            if ansi:
                # Process Synchronet @-codes / Mystic |XX display codes so
                # @USER@ / |UN / etc. in chat.ans are substituted — matches
                # the pattern in session._show_ansi_screen() and menu_engine.
                try:
                    from ..features.display_codes import apply as _apply_codes
                    from ..features.bbs_ui import _app as _bbs_app
                    import anetbbs as _anetbbs_pkg
                    _cfg = _bbs_app().config
                    body = _apply_codes(
                        ansi.decode('latin-1'),
                        user=self.session.user,
                        bbs_name=_cfg.get('BBS_NAME', ''),
                        sysop=_cfg.get('SYSOP_NAME', ''),
                        node=(getattr(self.session, '_node_entry', None).slot
                              if getattr(self.session, '_node_entry', None) else 1),
                        version=getattr(_anetbbs_pkg, '__version__', 'v1.0a'),
                    )
                    self.session.writer.write(b'\x1b[2J\x1b[H' + body.encode('latin-1'))
                except Exception:
                    self.session.writer.write(b'\x1b[2J\x1b[H' + ansi)
                await self.session.writer.drain()
            else:
                await self.session.write('\x1b[2J\x1b[H')
                await self.session.write(banner('Chat Systems'))
                for hk, lbl in (('1', 'Local Chat'),
                                ('2', 'IRC Chat (A-Net IRC)'),
                                ('3', 'MRC Chat (Inter-BBS)'),
                                ('Q', 'Return to Main Menu')):
                    await self.session.write(menu_item(hk, lbl) + '\r\n')
                await self.session.write('\r\n' + footer() + '\r\n')
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
        await self.session.write("\r\nLocal Chat Room (type /quit to exit)\r\n\r\n")
        while True:
            message = await self.session.read_line("> ")
            if message.lower() == '/quit':
                break
            await self.session.write(f"\r\n<{self.session.user['username']}> {message}\r\n")
