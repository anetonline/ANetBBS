"""Regression test: GameManager.show_menu() (anetbbs/features/games.py)
now routes through core/mods_override.py's call_core_override(), the
same mechanism the pre-login menu and Chat Systems menu already use.
A sysop can drop a full replacement at data/mods/core/game_center.py
(defining an async show_game_center_menu(session, game_manager)) and
have it run instead of the built-in Game Center menu.

Mirrors tests/test_chat_menu_mods_override.py's structure exactly.
call_core_override() itself is covered by tests/test_core_mods_override.py;
this file only covers the new games.py call site.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _fresh_app, _FakeSession  # noqa: E402


class GameCenterModsOverrideTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))

        self._mods_core_dir = Path(self._tmp.name) / 'mods_data' / 'mods' / 'core'
        self._mods_core_dir.mkdir(parents=True)
        self.app.config['DATA_DIR'] = str(Path(self._tmp.name) / 'mods_data')

        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=self.app)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _write_override(self, code):
        (self._mods_core_dir / 'game_center.py').write_text(code)

    def test_no_override_falls_back_to_stock_menu(self):
        from anetbbs.features.games import GameManager
        session = _FakeSession(responses=['3'])
        mgr = GameManager(session)
        asyncio.run(mgr.show_menu())
        self.assertIn('Game Center', session.transcript())

    def test_override_present_wins_over_stock_menu(self):
        from anetbbs.features.games import GameManager
        self._write_override(
            "async def show_game_center_menu(session, game_manager):\n"
            "    await session.write('CUSTOM GAME CENTER')\n"
        )
        session = _FakeSession(responses=[])
        mgr = GameManager(session)
        asyncio.run(mgr.show_menu())
        transcript = session.transcript()
        self.assertIn('CUSTOM GAME CENTER', transcript)
        self.assertNotIn('Game Center', transcript)

    def test_override_receives_the_live_game_manager_instance(self):
        from anetbbs.features.games import GameManager
        self._write_override(
            "async def show_game_center_menu(session, game_manager):\n"
            "    await session.write('has session: ' + "
            "str(game_manager.session is session))\n"
        )
        session = _FakeSession(responses=[])
        mgr = GameManager(session)
        asyncio.run(mgr.show_menu())
        self.assertIn('has session: True', session.transcript())

    def test_broken_override_degrades_to_stock_menu(self):
        from anetbbs.features.games import GameManager
        self._write_override("def broken(:\n")
        session = _FakeSession(responses=['3'])
        mgr = GameManager(session)
        asyncio.run(mgr.show_menu())
        self.assertIn('Game Center', session.transcript())


if __name__ == '__main__':
    unittest.main()
