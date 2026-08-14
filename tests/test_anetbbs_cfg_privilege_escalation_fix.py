"""Regression test for a real privilege-escalation bug found in a
security audit: anetbbs-cfg (the standalone curses tool that can edit
user security levels, echomail/hub credentials, etc.) is only ever
supposed to be reachable SSH-only + admin-only. bbs_ui.py's own
_sysop_cfg_tool() docstring specifically claimed it was "gated twice"
so a bug elsewhere in the menu system couldn't bypass that -- but
menu_engine.py's _act_door() (the generic "launch a door by raw
numeric id" menu action, reachable via any sysop-configured menu item)
performed NO admin/SSH/is_active check at all, and reached
door_runner.py's play_door_game_telnet() -- the real, shared door-
launch entry point -- directly, bypassing bbs_ui.py's gating entirely.

Two fixes, tested here:
  1. menu_engine._act_door() now refuses any is_active=False game
     (general hygiene -- hidden games should never be reachable by a
     generic numeric-id menu action).
  2. door_runner.play_door_game_telnet() independently refuses to
     launch the specific 'anetbbs-cfg' game unless the session is SSH
     AND the user is admin -- enforced at the actual shared launch
     chokepoint, not just in bbs_ui.py's wrapper, so no current or
     future caller can bypass it.
"""
import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, protocol=None, user=None):
        self.written = []
        self.encoding = 'cp437'
        self.presence = types.SimpleNamespace(protocol=protocol) if protocol else None
        self.user = user or {}

    async def write(self, data):
        self.written.append(data)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return ''

    def transcript(self):
        return ''.join(self.written)


class AnetbbsCfgPrivilegeEscalationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.cfg_privesc_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Game
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

            regular = User(username='regularuser', email='reg@example.com',
                            password_hash='x', access_level=50, is_admin=False)
            admin = User(username='sysop', email='sysop@example.com',
                         password_hash='x', access_level=100, is_admin=True)
            db.session.add_all([regular, admin])

            # create_app() itself bundled-door-seeds a REAL Game row at
            # slug='anetbbs-cfg' (web_app.py), but only when the
            # anetbbs-cfg console-script binary actually exists on
            # disk (its own 'must_exist' gate) -- true in a real
            # install/Docker build (setup.py's console_scripts entry
            # point gets installed for real), false in a bare
            # `pip install -e .` dev checkout with no such binary
            # present. A blind INSERT here collided with that real
            # seed row's UNIQUE(slug) constraint the moment this ran
            # somewhere the binary DOES exist (caught by CI, not by
            # local dev testing) -- get-or-create instead, so this
            # test's own outcome doesn't depend on which of those two
            # environments it happens to run in.
            cfg_game = Game.query.filter_by(slug='anetbbs-cfg').first()
            if cfg_game is None:
                cfg_game = Game(name='Config Tool', slug='anetbbs-cfg',
                                game_type='door_native', is_active=False,
                                web_enabled=False,
                                executable_path='/bin/true')
                db.session.add(cfg_game)
            else:
                cfg_game.is_active = False
            hidden_other = Game(name='Some Other Hidden Door', slug='hidden-other',
                                game_type='door_native', is_active=False,
                                executable_path='/bin/true')
            visible = Game(name='Visible Door', slug='visible-door',
                           game_type='door_native', is_active=True,
                           executable_path='/bin/true')
            db.session.add_all([hidden_other, visible])
            db.session.commit()

            cls.regular_id = regular.id
            cls.admin_id = admin.id
            cls.cfg_game_id = cfg_game.id
            cls.hidden_other_id = hidden_other.id
            cls.visible_id = visible.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def _app_ctx(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    # ── Fix 1: menu_engine._act_door() refuses hidden (is_active=False) games ──

    def test_act_door_refuses_the_cfg_tool_by_id(self):
        from anetbbs.features.menu_engine import _act_door

        class _UI:
            def __init__(self, session):
                self.session = session

        session = _FakeSession(protocol='ssh', user={'id': self.admin_id, 'is_admin': True})
        ui = _UI(session)
        with self._app_ctx(), patch('anetbbs.games.door_runner.play_door_game_telnet',
                                     new=AsyncMock()) as mock_launch:
            asyncio.run(_act_door(ui, str(self.cfg_game_id)))
        mock_launch.assert_not_awaited()
        self.assertIn('Game not found', session.transcript())

    def test_act_door_refuses_any_hidden_game_not_just_cfg(self):
        from anetbbs.features.menu_engine import _act_door

        class _UI:
            def __init__(self, session):
                self.session = session

        session = _FakeSession(protocol='telnet', user={'id': self.regular_id})
        ui = _UI(session)
        with self._app_ctx(), patch('anetbbs.games.door_runner.play_door_game_telnet',
                                     new=AsyncMock()) as mock_launch:
            asyncio.run(_act_door(ui, str(self.hidden_other_id)))
        mock_launch.assert_not_awaited()

    def test_act_door_still_launches_a_normal_visible_game(self):
        from anetbbs.features.menu_engine import _act_door

        class _UI:
            def __init__(self, session):
                self.session = session

        session = _FakeSession(protocol='telnet', user={'id': self.regular_id})
        ui = _UI(session)
        with self._app_ctx(), patch('anetbbs.games.door_runner.play_door_game_telnet',
                                     new=AsyncMock()) as mock_launch:
            asyncio.run(_act_door(ui, str(self.visible_id)))
        mock_launch.assert_awaited_once()

    # ── Fix 2: play_door_game_telnet() itself refuses anetbbs-cfg for
    #    non-SSH or non-admin sessions, independent of the caller ──

    def test_play_door_game_telnet_refuses_cfg_over_telnet_even_for_admin(self):
        from anetbbs.games.door_runner import play_door_game_telnet
        from anetbbs.models import Game
        with self.app.app_context():
            game = Game.query.get(self.cfg_game_id)
            result = asyncio.run(play_door_game_telnet(
                game, {'id': self.admin_id}, _FakeSession(protocol='telnet')))
        self.assertFalse(result)

    def test_play_door_game_telnet_refuses_cfg_over_ssh_for_non_admin(self):
        from anetbbs.games.door_runner import play_door_game_telnet
        from anetbbs.models import Game
        with self.app.app_context():
            game = Game.query.get(self.cfg_game_id)
            result = asyncio.run(play_door_game_telnet(
                game, {'id': self.regular_id}, _FakeSession(protocol='ssh')))
        self.assertFalse(result)

    def test_play_door_game_telnet_refuses_cfg_with_no_session_presence_at_all(self):
        # Simulates a caller that never went through the normal
        # login/protocol-detection flow at all -- the most direct
        # possible bypass attempt.
        from anetbbs.games.door_runner import play_door_game_telnet
        from anetbbs.models import Game
        with self.app.app_context():
            game = Game.query.get(self.cfg_game_id)
            result = asyncio.run(play_door_game_telnet(
                game, {'id': self.admin_id}, _FakeSession(protocol=None)))
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
