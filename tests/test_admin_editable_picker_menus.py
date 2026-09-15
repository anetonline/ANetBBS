"""Regression tests for making the Chat Systems / Game Center / Sysop
Tools top-level pickers admin-editable, the same way the main BBS menu
already is -- and for finishing mods/core wiring for those same three
screens (previously only the pre-login menu and Chat Systems had it).

Covers, in order:

1. The single shared ACTION_TYPE_CHOICES source in
   anetbbs/features/menu_engine.py -- previously three separately
   hand-maintained lists (menu_engine._ACTIONS, web/menu_admin.py's
   ACTION_TYPES, cfg/sections/menu.py's ACTION_TYPE_CHOICES) that had
   already drifted (the cfg copy was missing 12 action types the web
   UI had). Both consumers now import the one canonical list, and this
   file asserts they actually do, plus that _ACTIONS and the choices
   list stay in 1:1 sync (a parity check against FUTURE drift, not
   just today's).
2. _picker_goto_or_stock()'s 3-tier routing (mods/core override wins >
   admin-editable BbsMenu wins > hardcoded stock fallback), exercised
   directly.
3. Each new leaf action type (chat_local/chat_irc/chat_mrc,
   game_door_list/game_number_guess, and the 15 sysop_* ones) actually
   calls the right underlying method, and the ones with their own
   gating (no_irc/no_mrc/no_games, is_admin) enforce it even when
   reached directly (not just through the top-level picker's own
   gate) -- the same defense-in-depth reasoning as the pre-existing
   _sysop_cfg_tool() double SSH-only gate.
4. One true end-to-end test driving the real menu_engine.run_menu()
   loop: main -> (goto) chat_systems, where chat_systems is a real
   sysop-created BbsMenu with a genuinely new 4th item -- confirming
   the concrete "sysop adds a 4th chat option via Admin, no code"
   case this whole feature exists for.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


# ---- shared fake session plumbing, matching the existing
# test_menu_engine_stale_access_level.py pattern for driving the real
# run_menu() loop end to end ----

class _FakeWriter:
    def __init__(self, peer=('1.2.3.4', 1234)):
        self._peer = peer
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return self._peer if key == 'peername' else None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass


class _InstantReader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class ActionTypeChoicesConsistencyTests(unittest.TestCase):
    def test_web_menu_admin_imports_the_shared_list(self):
        from anetbbs.features.menu_engine import ACTION_TYPE_CHOICES
        from anetbbs.web import menu_admin
        self.assertIs(menu_admin.ACTION_TYPES, ACTION_TYPE_CHOICES)

    def test_cfg_section_is_the_flat_value_projection_of_the_shared_list(self):
        from anetbbs.features.menu_engine import ACTION_TYPE_CHOICES
        from anetbbs.cfg.sections import menu as cfg_menu
        expected = [value for value, _label in ACTION_TYPE_CHOICES]
        self.assertEqual(cfg_menu.ACTION_TYPE_CHOICES, expected)

    def test_previously_missing_action_types_now_present_in_cfg(self):
        """These 12 were on the web UI's list but missing from the cfg
        tool's -- a real, pre-existing drift this consolidation closes
        as a side effect."""
        from anetbbs.cfg.sections.menu import ACTION_TYPE_CHOICES
        for missing in ('exec', 'ansi', 'wall', 'games', 'guru',
                        'multinode', 'oneliners', 'lastcallers',
                        'dialout', 'page', 'imsg', 'imsg_send'):
            self.assertIn(missing, ACTION_TYPE_CHOICES)

    def test_every_registered_action_has_a_choices_entry(self):
        from anetbbs.features.menu_engine import _ACTIONS, ACTION_TYPE_CHOICES
        choice_values = {value for value, _label in ACTION_TYPE_CHOICES}
        missing = set(_ACTIONS.keys()) - choice_values
        self.assertEqual(missing, set(),
                         f'action type(s) registered in _ACTIONS but not '
                         f'offered in ACTION_TYPE_CHOICES: {missing}')

    def test_every_choices_entry_has_a_registered_action(self):
        from anetbbs.features.menu_engine import _ACTIONS, ACTION_TYPE_CHOICES
        choice_values = {value for value, _label in ACTION_TYPE_CHOICES}
        extra = choice_values - set(_ACTIONS.keys())
        self.assertEqual(extra, set(),
                         f'action type(s) offered in ACTION_TYPE_CHOICES '
                         f'but not registered in _ACTIONS: {extra}')

    def test_new_picker_leaf_action_types_are_registered(self):
        from anetbbs.features.menu_engine import _ACTIONS
        for name in ('chat_local', 'chat_irc', 'chat_mrc',
                     'game_door_list', 'game_number_guess',
                     'sysop_users', 'sysop_boards', 'sysop_echomail',
                     'sysop_games', 'sysop_wall', 'sysop_file_queue',
                     'sysop_events', 'sysop_rss_admin',
                     'sysop_login_modules', 'sysop_notifications',
                     'sysop_registry', 'sysop_callers',
                     'sysop_node_monitor', 'sysop_status',
                     'sysop_cfg_tool'):
            self.assertIn(name, _ACTIONS)


class PickerGotoOrStockTests(unittest.TestCase):
    """Direct tests of _picker_goto_or_stock()'s 3-tier decision,
    mocking has_core_override() and the BbsMenu lookup so this doesn't
    need a real DB/app for the routing logic itself."""

    def setUp(self):
        self._app_patcher = patch(
            'anetbbs.features.menu_engine._app',
            return_value=_DummyAppCtx())
        self._app_patcher.start()
        self.addCleanup(self._app_patcher.stop)

    def test_mods_core_override_present_skips_goto_and_calls_stock(self):
        from anetbbs.features import menu_engine
        stock = AsyncMock()
        with patch('anetbbs.core.mods_override.has_core_override',
                  return_value=True):
            result = asyncio.run(
                menu_engine._picker_goto_or_stock('chat_menu', 'chat_systems', stock))
        self.assertIsNone(result)
        stock.assert_awaited_once()

    def test_admin_menu_exists_and_no_override_returns_goto(self):
        from anetbbs.features import menu_engine
        stock = AsyncMock()
        with patch('anetbbs.core.mods_override.has_core_override',
                  return_value=False), \
             patch('anetbbs.models.BbsMenu') as mock_model:
            mock_model.query.filter_by.return_value.first.return_value = object()
            result = asyncio.run(
                menu_engine._picker_goto_or_stock('chat_menu', 'chat_systems', stock))
        self.assertEqual(result, ('goto', 'chat_systems'))
        stock.assert_not_awaited()

    def test_neither_override_nor_admin_menu_calls_stock(self):
        from anetbbs.features import menu_engine
        stock = AsyncMock()
        with patch('anetbbs.core.mods_override.has_core_override',
                  return_value=False), \
             patch('anetbbs.models.BbsMenu') as mock_model:
            mock_model.query.filter_by.return_value.first.return_value = None
            result = asyncio.run(
                menu_engine._picker_goto_or_stock('chat_menu', 'chat_systems', stock))
        self.assertIsNone(result)
        stock.assert_awaited_once()


class _DummyAppCtx:
    class config(dict):
        pass

    def __init__(self):
        self.config = {}

    def app_context(self):
        return _NullCtx()


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeUI:
    """Minimal stand-in for BBSMenuUI, just enough surface for the leaf
    action types under test: a .session with .write()/.user, plus
    whichever bound methods a given test exercises (attached as
    AsyncMocks per-test)."""
    def __init__(self, is_admin=False):
        self.session = AsyncMock()
        self.session.write = AsyncMock()
        self.session.read_line = AsyncMock(return_value='')
        self.session.read_key = AsyncMock(return_value='')
        self.session.user = {'id': 1, 'is_admin': is_admin}


class ChatLeafActionTests(unittest.TestCase):
    def test_chat_local_calls_local_chat(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        ui.session.chat = AsyncMock()
        asyncio.run(menu_engine._act_chat_local(ui, None))
        ui.session.chat.local_chat.assert_awaited_once()

    def test_chat_irc_suspended_user_is_blocked(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        flags = type('F', (), {'no_irc': True, 'no_mrc': False, 'no_games': False})()
        with patch('anetbbs.features.menu_engine._get_flags', return_value=flags):
            asyncio.run(menu_engine._act_chat_irc(ui, None))
        out = ''.join(c.args[0] for c in ui.session.write.call_args_list)
        self.assertIn('IRC access has been suspended', out)

    def test_chat_irc_allowed_launches_anetirc(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        flags = type('F', (), {'no_irc': False})()
        with patch('anetbbs.features.menu_engine._get_flags', return_value=flags), \
             patch('anetbbs.features.anetirc2.launch_anetirc_telnet',
                  new=AsyncMock()) as launch:
            asyncio.run(menu_engine._act_chat_irc(ui, None))
        launch.assert_awaited_once()

    def test_chat_mrc_disabled_shows_message_not_invalid_choice(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        with patch('anetbbs.features.chat._mrc_enabled', return_value=False):
            asyncio.run(menu_engine._act_chat_mrc(ui, None))
        out = ''.join(c.args[0] for c in ui.session.write.call_args_list)
        self.assertIn('MRC chat is currently disabled', out)

    def test_chat_mrc_suspended_user_is_blocked(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        flags = type('F', (), {'no_mrc': True})()
        with patch('anetbbs.features.chat._mrc_enabled', return_value=True), \
             patch('anetbbs.features.menu_engine._get_flags', return_value=flags):
            asyncio.run(menu_engine._act_chat_mrc(ui, None))
        out = ''.join(c.args[0] for c in ui.session.write.call_args_list)
        self.assertIn('MRC access has been suspended', out)

    def test_chat_mrc_allowed_launches_mrc_show_menu(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        ui.session.chat = AsyncMock()
        mrc = AsyncMock()
        ui.session.chat.chat_systems = {'mrc': mrc}
        flags = type('F', (), {'no_mrc': False})()
        with patch('anetbbs.features.chat._mrc_enabled', return_value=True), \
             patch('anetbbs.features.menu_engine._get_flags', return_value=flags):
            asyncio.run(menu_engine._act_chat_mrc(ui, None))
        mrc.show_menu.assert_awaited_once()
        self.assertIs(ui.session.chat.current_chat, mrc)


class GameLeafActionTests(unittest.TestCase):
    def test_game_door_list_calls_show_door_menu(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        ui.session.games = AsyncMock()
        flags = type('F', (), {'no_games': False})()
        with patch('anetbbs.features.menu_engine._get_flags', return_value=flags):
            asyncio.run(menu_engine._act_game_door_list(ui, None))
        ui.session.games.show_door_menu.assert_awaited_once()

    def test_game_door_list_suspended_user_is_blocked(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        ui.session.games = AsyncMock()
        flags = type('F', (), {'no_games': True})()
        with patch('anetbbs.features.menu_engine._get_flags', return_value=flags):
            asyncio.run(menu_engine._act_game_door_list(ui, None))
        ui.session.games.show_door_menu.assert_not_awaited()

    def test_game_number_guess_calls_play_number_guess(self):
        from anetbbs.features import menu_engine
        ui = _FakeUI()
        ui.session.games = AsyncMock()
        flags = type('F', (), {'no_games': False})()
        with patch('anetbbs.features.menu_engine._get_flags', return_value=flags):
            asyncio.run(menu_engine._act_game_number_guess(ui, None))
        ui.session.games.play_number_guess.assert_awaited_once()


class SysopLeafActionTests(unittest.TestCase):
    """Every sysop_* leaf action type must (a) call its matching
    ui.sysop_*() bound method and (b) refuse a non-admin even when
    called directly -- the point of _require_sysop() existing as its
    own re-check rather than trusting a menu item's min_access alone,
    since an admin-editable sysop_tools menu means these are now
    reachable via a plain 'goto' from anywhere."""

    ACTION_TO_METHOD = {
        'sysop_users': 'sysop_users',
        'sysop_boards': 'sysop_boards',
        'sysop_echomail': 'sysop_echomail',
        'sysop_games': 'sysop_games',
        'sysop_wall': 'sysop_wall',
        'sysop_file_queue': 'sysop_file_queue',
        'sysop_events': 'sysop_events',
        'sysop_rss_admin': 'sysop_rss_admin',
        'sysop_login_modules': 'sysop_login_modules',
        'sysop_notifications': 'sysop_notifications',
        'sysop_registry': 'sysop_registry',
        'sysop_callers': 'sysop_callers',
        'sysop_node_monitor': 'sysop_node_monitor',
        'sysop_status': 'sysop_status',
        'sysop_cfg_tool': 'sysop_cfg_tool',
    }

    def test_each_action_calls_its_matching_bound_method_when_admin(self):
        from anetbbs.features import menu_engine
        for action_name, method_name in self.ACTION_TO_METHOD.items():
            with self.subTest(action=action_name):
                ui = _FakeUI(is_admin=True)
                setattr(ui, method_name, AsyncMock())
                fn = menu_engine._ACTIONS[action_name]
                asyncio.run(fn(ui, None))
                getattr(ui, method_name).assert_awaited_once()

    def test_each_action_refuses_a_non_admin_even_called_directly(self):
        from anetbbs.features import menu_engine
        for action_name, method_name in self.ACTION_TO_METHOD.items():
            with self.subTest(action=action_name):
                ui = _FakeUI(is_admin=False)
                setattr(ui, method_name, AsyncMock())
                fn = menu_engine._ACTIONS[action_name]
                asyncio.run(fn(ui, None))
                getattr(ui, method_name).assert_not_awaited()
                out = ''.join(c.args[0] for c in ui.session.write.call_args_list)
                self.assertIn('Sysop access required', out)


class EndToEndAdminEditableChatMenuTests(unittest.TestCase):
    """Drives the real menu_engine.run_menu() loop against a seeded DB
    -- the concrete case this whole feature exists for: a sysop builds
    a real chat_systems BbsMenu (the exact reserved name
    _picker_goto_or_stock() looks for) with a genuinely new 4th item,
    and picking Chat from the main menu reaches it automatically, no
    code required."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_editable_chat_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, BbsMenu, BbsMenuItem, User
        # create_app() itself seeds 'main' and the three picker menus
        # (menu_engine.seed_default_menus(), called at web_app.py's own
        # create_app() -- see DEFAULT_MENUS in menu_engine.py) -- no
        # need to build the tree by hand here.
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            user = User(username='chatmenutester', email='cmt@example.com',
                       password_hash='x', access_level=10, is_admin=False)
            db.session.add(user)
            db.session.flush()
            cls.user_id = user.id

            chat_systems = BbsMenu.query.filter_by(name='chat_systems').first()
            assert chat_systems is not None, (
                'seed_default_menus() should have already created '
                'chat_systems -- if this fails, the seeding itself broke')

            # A real sysop's edit, exactly as they'd make it from
            # Admin -> BBS Menus or anetbbs-cfg: drop the stock IRC
            # item, and add a genuinely new 4th option (any action
            # type works here -- the point is it's not one of the
            # stock three) pointing at a plain welcome-style ansi slot.
            BbsMenuItem.query.filter_by(
                menu_id=chat_systems.id, action_type='chat_irc').delete()
            db.session.add(BbsMenuItem(
                menu_id=chat_systems.id, hotkey='4', label='House Rules',
                action_type='ansi', action_args='welcome', is_visible=True,
                min_access=0, sort_order=15))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_session(self, reader):
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        session = BBSSession(reader, writer, config={})
        session.user = {'id': self.user_id, 'access_level': 10, 'is_admin': False}
        session.window_size = (80, 24)
        return session, writer

    def test_picking_chat_reaches_the_sysop_customized_menu(self):
        from anetbbs.features import menu_engine
        from anetbbs.core.session import CarrierLost

        reader = _InstantReader([b'H'])  # 'H' = Chat on the seeded main menu
        session, writer = self._make_session(reader)

        try:
            asyncio.run(menu_engine.run_menu(session, start='main'))
        except CarrierLost:
            pass

        out = bytes(writer.written).decode('latin-1', errors='replace')
        self.assertIn('House Rules', out,
                      "the sysop's custom 4th item must render -- the "
                      "hardcoded stock Chat Systems menu never shows this")
        self.assertNotIn('IRC Chat', out,
                         'the sysop removed IRC from their custom menu; '
                         'the stock hardcoded picker must not appear instead')


if __name__ == '__main__':
    unittest.main()
