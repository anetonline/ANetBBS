"""Regression test: BBSMenuUI.sysop_menu() (anetbbs/features/bbs_ui.py)
now routes through core/mods_override.py's call_core_override(), the
same mechanism the pre-login menu, Chat Systems menu, and Game Center
menu already use. A sysop can drop a full replacement at
data/mods/core/sysop_tools.py (defining an async
show_sysop_tools_menu(session, bbs_ui)) and have it run instead of the
built-in Sysop Tools category picker.

Mirrors tests/test_chat_menu_mods_override.py's structure. This is the
picker's top-level entry point only -- the individual category screens
underneath (Users, Boards, etc.) are unchanged, untouched dynamic CRUD
screens, out of scope for this override.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _fresh_app, _FakeSession  # noqa: E402


def _stub_lightbar():
    """The stock Sysop Tools picker renders via BBSMenuUI._rss_lightbar()
    (an interactive Up/Down scroller reading real arrow keys via
    session.read_key_arrow(), which _FakeSession doesn't implement --
    that's real UI already covered elsewhere, e.g.
    tests/test_terminal_sysop_menu.py). These tests only care whether
    the mods/core override wins or the stock picker gets reached at
    all -- stub the lightbar to quit immediately so a real interactive
    render never has to run."""
    from anetbbs.features.bbs_ui import BBSMenuUI
    return patch.object(BBSMenuUI, '_rss_lightbar',
                        new=AsyncMock(return_value=('quit', None)))


class SysopToolsModsOverrideTests(unittest.TestCase):
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

        self._lightbar_patcher = _stub_lightbar()
        self._lightbar_mock = self._lightbar_patcher.start()
        self.addCleanup(self._lightbar_patcher.stop)

    def _write_override(self, code):
        (self._mods_core_dir / 'sysop_tools.py').write_text(code)

    def _make_ui(self, responses, is_admin=True):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(responses=responses)
        session.user = {'id': 1, 'is_admin': is_admin}
        return BBSMenuUI(session), session

    def test_no_override_falls_back_to_stock_menu(self):
        ui, session = self._make_ui(responses=[], is_admin=True)
        asyncio.run(ui.sysop_menu())
        self._lightbar_mock.assert_awaited_once()

    def test_non_admin_never_reaches_the_picker_even_without_override(self):
        # "Sysop access required" ends in its own read_line("Press
        # Enter...") prompt -- give it one scripted keystroke to consume.
        ui, session = self._make_ui(responses=[''], is_admin=False)
        asyncio.run(ui.sysop_menu())
        self.assertIn('Sysop access required', session.transcript())
        self._lightbar_mock.assert_not_awaited()

    def test_override_present_wins_over_stock_menu(self):
        self._write_override(
            "async def show_sysop_tools_menu(session, bbs_ui):\n"
            "    await session.write('CUSTOM SYSOP TOOLS')\n"
        )
        ui, session = self._make_ui(responses=[], is_admin=True)
        asyncio.run(ui.sysop_menu())
        self.assertIn('CUSTOM SYSOP TOOLS', session.transcript())
        self._lightbar_mock.assert_not_awaited()

    def test_override_receives_the_live_bbs_ui_instance(self):
        self._write_override(
            "async def show_sysop_tools_menu(session, bbs_ui):\n"
            "    await session.write('has sysop_users: ' + "
            "str(hasattr(bbs_ui, 'sysop_users')))\n"
        )
        ui, session = self._make_ui(responses=[], is_admin=True)
        asyncio.run(ui.sysop_menu())
        self.assertIn('has sysop_users: True', session.transcript())

    def test_broken_override_degrades_to_stock_menu(self):
        self._write_override("def broken(:\n")
        ui, session = self._make_ui(responses=[], is_admin=True)
        asyncio.run(ui.sysop_menu())
        self._lightbar_mock.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
