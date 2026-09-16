"""Regression tests for menu_engine._act_door() accepting a Game slug
in a menu item's action_args, not just a raw numeric id.

Real usability gap flagged directly by a sysop: neither the web admin
Games list nor anetbbs-cfg's Games section shows a game's numeric id
anywhere visible -- it's only ever in the edit-page URL
(/admin/games/<id>/edit). The slug, by contrast, is a labeled field
right on that same edit page. Fixed by trying an int() parse first
(so any menu item already configured with a raw id keeps working
unchanged), falling back to a slug lookup.

The existing is_active-only-for-hidden-games security gate (a prior
security-audit finding -- see _act_door's own comment) must still
apply on the slug path, not just the id path.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod

from tests.test_door_games_menu_layout import _fresh_app, _FakeSession  # noqa: E402


class ActDoorSlugLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.act_door_slug_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        os.environ['FLASK_ENV'] = 'testing'
        cls.app = _fresh_app(cls._tmp_db)

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.models import db, Game
        with self.app.app_context():
            Game.query.delete()
            db.session.commit()
            self.active_game = Game(
                name='Test MRC Door', slug='test-mrc-door',
                game_type='door_telnet', is_active=True)
            self.hidden_game = Game(
                name='Hidden Tool', slug='hidden-tool',
                game_type='door_telnet', is_active=False)
            db.session.add(self.active_game)
            db.session.add(self.hidden_game)
            db.session.commit()
            self.active_id = self.active_game.id
            self.hidden_id = self.hidden_game.id

    def _fake_ui(self, admin=False):
        session = _FakeSession(responses=[])
        session.user = {'id': 1, 'is_admin': admin}
        return type('FakeUI', (), {'session': session})(), session

    def _patch_launch(self):
        from unittest.mock import AsyncMock, patch
        return patch('anetbbs.games.door_runner.play_door_game_telnet',
                    new=AsyncMock())

    def test_resolves_by_numeric_id_backward_compat(self):
        from anetbbs.features.menu_engine import _act_door
        ui, session = self._fake_ui()
        with self._patch_launch() as launch:
            asyncio.run(_act_door(ui, str(self.active_id)))
        launch.assert_awaited_once()
        launched_game = launch.call_args.args[0]
        self.assertEqual(launched_game.slug, 'test-mrc-door')

    def test_resolves_by_slug(self):
        from anetbbs.features.menu_engine import _act_door
        ui, session = self._fake_ui()
        with self._patch_launch() as launch:
            asyncio.run(_act_door(ui, 'test-mrc-door'))
        launch.assert_awaited_once()
        launched_game = launch.call_args.args[0]
        self.assertEqual(launched_game.id, self.active_id)

    def test_unknown_slug_reports_not_found(self):
        from anetbbs.features.menu_engine import _act_door
        ui, session = self._fake_ui()
        with self._patch_launch() as launch:
            asyncio.run(_act_door(ui, 'does-not-exist'))
        launch.assert_not_awaited()
        self.assertIn('Game not found', session.transcript())

    def test_unknown_id_reports_not_found(self):
        from anetbbs.features.menu_engine import _act_door
        ui, session = self._fake_ui()
        with self._patch_launch() as launch:
            asyncio.run(_act_door(ui, '999999'))
        launch.assert_not_awaited()
        self.assertIn('Game not found', session.transcript())

    def test_hidden_game_refused_via_slug_same_as_via_id(self):
        """The pre-existing is_active-only-for-hidden-games security
        gate must apply on the slug path too, not just the numeric-id
        path it was originally written for."""
        from anetbbs.features.menu_engine import _act_door
        ui, session = self._fake_ui()
        with self._patch_launch() as launch:
            asyncio.run(_act_door(ui, 'hidden-tool'))
        launch.assert_not_awaited()
        self.assertIn('Game not found', session.transcript())

        ui2, session2 = self._fake_ui()
        with self._patch_launch() as launch2:
            asyncio.run(_act_door(ui2, str(self.hidden_id)))
        launch2.assert_not_awaited()
        self.assertIn('Game not found', session2.transcript())

    def test_blank_args_reports_not_found_not_a_crash(self):
        from anetbbs.features.menu_engine import _act_door
        ui, session = self._fake_ui()
        with self._patch_launch() as launch:
            asyncio.run(_act_door(ui, ''))
        launch.assert_not_awaited()
        self.assertIn('Game not found', session.transcript())


if __name__ == '__main__':
    unittest.main()
