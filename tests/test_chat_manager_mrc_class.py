"""ChatManager must hand ascii sessions the plain AsciiMRCChat client,
not the full cursor-addressed MRCChat -- see mrc_chat_ascii.py's
docstring for why that was a real, pre-existing bug (write() strips
every ANSI escape for term_mode == 'ascii', so split-screen mode's
layout was silently dropped). petscii sessions never reach ChatManager
at all (they have their own separate menu system, see petscii_ui.py),
so there's nothing to select there -- this only covers ascii vs.
everything else.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _FakeSession  # noqa: E402


class ChatManagerMrcClassSelectionTests(unittest.TestCase):
    def test_ascii_session_gets_ascii_mrc_chat(self):
        from anetbbs.features.chat import ChatManager
        from anetbbs.features.mrc_chat_ascii import AsciiMRCChat

        session = _FakeSession(responses=[])
        session.term_mode = 'ascii'
        mgr = ChatManager(session)

        self.assertIsInstance(mgr.chat_systems['mrc'], AsciiMRCChat)

    def test_ansi_session_gets_the_full_mrc_chat(self):
        from anetbbs.features.chat import ChatManager
        from anetbbs.features.mrc_chat import MRCChat
        from anetbbs.features.mrc_chat_ascii import AsciiMRCChat

        session = _FakeSession(responses=[])
        session.term_mode = 'ansi'
        mgr = ChatManager(session)

        self.assertIsInstance(mgr.chat_systems['mrc'], MRCChat)
        self.assertNotIsInstance(mgr.chat_systems['mrc'], AsciiMRCChat)

    def test_wide_session_also_gets_the_full_mrc_chat(self):
        from anetbbs.features.chat import ChatManager
        from anetbbs.features.mrc_chat_ascii import AsciiMRCChat

        session = _FakeSession(responses=[])
        session.term_mode = 'wide'
        mgr = ChatManager(session)

        self.assertNotIsInstance(mgr.chat_systems['mrc'], AsciiMRCChat)


if __name__ == '__main__':
    unittest.main()
