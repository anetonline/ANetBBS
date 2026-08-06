"""MRC_BRIDGE_ENABLED regression test.

Jerry asked directly: if the sysop turns ANetBBS's MRC off, does the
web UI stop showing it under Chat (leaving just IRC)? At the time of
asking, the answer was no -- MRC_BRIDGE_ENABLED was written to .env by
install.sh's ENABLE_MRC prompt but never actually read by the Flask
app anywhere; both the web Chat dropdown (base.html) and the terminal
Chat Systems menu (anetbbs/features/chat.py) showed "MRC Chat"
unconditionally. This covers the fix on the terminal side; the web
template change is a plain Jinja `{% if %}` with no logic to unit test.

SECOND ROUND, found live on the Pi: the first fix read MRC_BRIDGE_ENABLED
via `current_app.config.get(...)` directly in ChatManager.show_menu() --
passed every test in this file because setUp() pushed a real Flask app
context around the whole test, but menu_engine.py's real call site (a
caller pressing the Chat Systems menu hotkey) has no such ambient
context, and crashed instantly with "RuntimeError: Working outside of
application context" the first time anyone actually tried it. Fixed by
routing through a new _mrc_enabled() helper that builds its own transient
app context the same way _chat_flags() (right above it in chat.py)
already does. This file no longer pushes an app context in setUp() --
matching the real call path is the whole point now -- and patches
TestingConfig.MRC_BRIDGE_ENABLED directly (same idiom _fresh_app() in
test_door_games_menu_layout.py already uses for SQLALCHEMY_DATABASE_URI),
since _app()'s get_config(FLASK_ENV) resolves that class fresh on every
call rather than reading a per-test Flask app instance's mutated config.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _fresh_app, _FakeSession  # noqa: E402


class ChatMenuMrcToggleTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # _fresh_app() also points TestingConfig.SQLALCHEMY_DATABASE_URI
        # at this throwaway db -- needed because _mrc_enabled()'s own
        # transient _app() (inside chat.py) resolves the SAME
        # TestingConfig class, and would otherwise try to talk to
        # whatever URI a previous test left behind.
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        # Deliberately NOT pushing an app context here -- the real bug
        # this file exists to catch only reproduces without one. Each
        # test that needs a context (e.g. to seed data) pushes its own,
        # scoped narrowly, then pops it before exercising show_menu().
        self._orig_flask_env = os.environ.get('FLASK_ENV')
        os.environ['FLASK_ENV'] = 'testing'
        self.addCleanup(self._restore_flask_env)

    def _restore_flask_env(self):
        if self._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = self._orig_flask_env

    def _set_mrc_enabled(self, value):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.MRC_BRIDGE_ENABLED = value
        self.addCleanup(lambda: delattr(cfg_mod.TestingConfig, 'MRC_BRIDGE_ENABLED'))

    def test_mrc_menu_item_hidden_when_bridge_disabled(self):
        from anetbbs.features.chat import ChatManager
        self._set_mrc_enabled(False)
        session = _FakeSession(responses=['Q'])
        mgr = ChatManager(session)
        import asyncio
        asyncio.run(mgr.show_menu())
        transcript = session.transcript()
        self.assertNotIn('MRC Chat', transcript)
        self.assertIn('IRC Chat', transcript)

    def test_mrc_menu_item_shown_when_bridge_enabled(self):
        from anetbbs.features.chat import ChatManager
        self._set_mrc_enabled(True)
        session = _FakeSession(responses=['Q'])
        mgr = ChatManager(session)
        import asyncio
        asyncio.run(mgr.show_menu())
        transcript = session.transcript()
        self.assertIn('MRC Chat', transcript)

    def test_choosing_3_is_a_no_op_when_bridge_disabled(self):
        """With MRC hidden, its hotkey must not still secretly work --
        otherwise the toggle would be cosmetic only."""
        from anetbbs.features.chat import ChatManager
        self._set_mrc_enabled(False)
        session = _FakeSession(responses=['3', 'Q'])
        mgr = ChatManager(session)
        import asyncio
        asyncio.run(mgr.show_menu())
        transcript = session.transcript()
        self.assertIn('Invalid choice', transcript)

    def test_show_menu_works_with_no_ambient_flask_app_context(self):
        """Direct reproduction of the real production crash: menu_engine.
        py's _act_chat calls ui.session.chat.show_menu() with no Flask
        app/request context pushed anywhere in the call stack -- confirm
        this doesn't raise "Working outside of application context"
        (it did, live on the Pi, before _mrc_enabled() existed)."""
        from anetbbs.features.chat import ChatManager
        # No app context pushed anywhere above this line (unlike the
        # other tests' _set_mrc_enabled() helper, this one deliberately
        # doesn't push one) -- if show_menu() ever again reaches for
        # current_app without first opening its own context, this
        # raises "Working outside of application context" exactly like
        # it did live on the Pi.
        session = _FakeSession(responses=['Q'])
        mgr = ChatManager(session)
        import asyncio
        asyncio.run(mgr.show_menu())  # must not raise
        self.assertIn('MRC Chat', session.transcript())


if __name__ == '__main__':
    unittest.main()
