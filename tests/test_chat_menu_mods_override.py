"""Regression test: ChatManager.show_menu() (anetbbs/features/chat.py)
now routes through core/mods_override.py's call_core_override(), the
same mechanism the pre-login menu (data/mods/core/login_menu.py) has
used since v1.0.36 -- extending it to a second core screen for the
first time. A sysop can drop a full replacement at
data/mods/core/chat_menu.py (defining an async
show_chat_menu(session, chat_manager)) and have it run instead of the
built-in Chat Systems menu, including genuinely new behavior (not just
re-skinned art) since the override gets the live ChatManager instance
and can still call its stock building blocks (local_chat(),
chat_systems['mrc'], etc.).

call_core_override() itself (missing/broken-override fallback
behavior) is already covered by tests/test_core_mods_override.py --
this file only covers the new chat.py call site: that an override, when
present, actually wins over the stock menu, and that stock behavior
(already covered by tests/test_chat_mrc_toggle.py) is unaffected when
no override file exists.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _fresh_app, _FakeSession  # noqa: E402


class ChatMenuModsOverrideTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))

        self._mods_core_dir = Path(self._tmp.name) / 'mods_data' / 'mods' / 'core'
        self._mods_core_dir.mkdir(parents=True)
        self.app.config['DATA_DIR'] = str(Path(self._tmp.name) / 'mods_data')

        # ChatManager.show_menu() (via call_core_override) and its own
        # _mrc_enabled()/_chat_flags() helpers all resolve their Flask
        # app through anetbbs.features.bbs_ui._app() -- patched here
        # (matching tests/test_core_mods_override.py's own precedent)
        # so DATA_DIR resolves to this test's own tmp dir rather than
        # the real repo's data/ directory, and so all three call sites
        # agree on the same app instance/config.
        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=self.app)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _write_override(self, code):
        (self._mods_core_dir / 'chat_menu.py').write_text(code)

    def test_no_override_falls_back_to_stock_menu(self):
        from anetbbs.features.chat import ChatManager
        session = _FakeSession(responses=['Q'])
        mgr = ChatManager(session)
        asyncio.run(mgr.show_menu())
        self.assertIn('Chat Systems', session.transcript())

    def test_override_present_wins_over_stock_menu(self):
        from anetbbs.features.chat import ChatManager
        self._write_override(
            "async def show_chat_menu(session, chat_manager):\n"
            "    await session.write('CUSTOM CHAT MENU')\n"
        )
        session = _FakeSession(responses=[])
        mgr = ChatManager(session)
        asyncio.run(mgr.show_menu())
        transcript = session.transcript()
        self.assertIn('CUSTOM CHAT MENU', transcript)
        self.assertNotIn('Chat Systems', transcript)

    def test_override_receives_the_live_chat_manager_instance(self):
        """The override's second argument must be the real ChatManager
        -- not a copy, not just the session -- so it can reuse the
        stock building blocks (chat_systems['mrc'], local_chat(), etc.)
        rather than reimplementing them, per the override's own
        docstring in chat.py."""
        from anetbbs.features.chat import ChatManager
        self._write_override(
            "async def show_chat_menu(session, chat_manager):\n"
            "    await session.write('mrc key: ' + "
            "str(sorted(chat_manager.chat_systems.keys())))\n"
        )
        session = _FakeSession(responses=[])
        mgr = ChatManager(session)
        asyncio.run(mgr.show_menu())
        self.assertIn("mrc key: ['mrc']", session.transcript())

    def test_override_can_add_a_new_fourth_option(self):
        """The concrete motivating case: a sysop wants an option the
        stock menu doesn't offer at all, not just reordered/re-skinned
        existing ones."""
        from anetbbs.features.chat import ChatManager
        self._write_override(
            "async def show_chat_menu(session, chat_manager):\n"
            "    await session.write('1) Local  2) IRC  3) MRC  "
            "4) Shoutbox\\r\\nChoice: ')\n"
            "    choice = await session.read_line('')\n"
            "    if choice == '4':\n"
            "        await session.write('Welcome to the shoutbox!')\n"
        )
        session = _FakeSession(responses=['4'])
        mgr = ChatManager(session)
        asyncio.run(mgr.show_menu())
        self.assertIn('Welcome to the shoutbox!', session.transcript())

    def test_broken_override_degrades_to_stock_menu(self):
        from anetbbs.features.chat import ChatManager
        self._write_override("def broken(:\n")
        session = _FakeSession(responses=['Q'])
        mgr = ChatManager(session)
        asyncio.run(mgr.show_menu())
        self.assertIn('Chat Systems', session.transcript())


if __name__ == '__main__':
    unittest.main()
