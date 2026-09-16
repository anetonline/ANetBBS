"""Regression tests for Game.idle_timeout_enabled: the column migration
(fresh install + backfill on an upgrading install) and the web admin
form's create/edit round-trip. See test_door_idle_timeout_opt_out.py
for the actual door-launch enforcement behavior this field gates.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class IdleTimeoutEnabledColumnMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.idle_timeout_migration_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _has_column(self):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        return any(c['name'] == 'idle_timeout_enabled'
                  for c in insp.get_columns('games'))

    def test_fresh_install_has_the_column(self):
        with self.app.app_context():
            self.assertTrue(self._has_column())

    def test_backfills_column_and_defaults_existing_rows_to_true(self):
        from anetbbs.models import db, Game
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            self.assertTrue(self._has_column())

            game = Game(name='Predates Column', slug='predates-idle-col',
                       game_type='door_native')
            db.session.add(game)
            db.session.commit()
            game_id = game.id

            db.session.execute(db.text(
                'ALTER TABLE games DROP COLUMN idle_timeout_enabled'))
            db.session.commit()
            self.assertFalse(self._has_column())

            _lightweight_migrate(self.app)

            self.assertTrue(self._has_column())
            value = db.session.execute(db.text(
                'SELECT idle_timeout_enabled FROM games WHERE id = :id'),
                {'id': game_id}).scalar()
            self.assertEqual(value, 1,
                "a pre-existing game's new column must backfill to "
                "True (1), not NULL -- 'on by default' is the whole "
                "point of this field")

    def test_migration_is_idempotent(self):
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            self.assertTrue(self._has_column())


class GamesAdminFormIdleTimeoutFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.idle_timeout_form_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='idletimeoutadmintest', email='ita@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_new_game_checkbox_unchecked_disables_idle_timeout(self):
        from anetbbs.models import Game
        client = self._client_as_admin()
        client.post('/admin/games/add', data={
            'name': 'Test Chat Door', 'slug': 'test-chat-door-off',
            'category': 'other', 'min_access_level': '0',
            'game_type': 'door_native', 'max_nodes': '1', 'sort_order': '0',
            'is_active': 'y', 'terminal_enabled': 'y',
            # idle_timeout_enabled intentionally omitted -- unchecked
        }, follow_redirects=True)
        with self.app.app_context():
            g = Game.query.filter_by(slug='test-chat-door-off').first()
            self.assertIsNotNone(g)
            self.assertFalse(g.idle_timeout_enabled)

    def test_new_game_checkbox_checked_keeps_idle_timeout_on(self):
        from anetbbs.models import Game
        client = self._client_as_admin()
        client.post('/admin/games/add', data={
            'name': 'Test Normal Door', 'slug': 'test-normal-door-on',
            'category': 'other', 'min_access_level': '0',
            'game_type': 'door_native', 'max_nodes': '1', 'sort_order': '0',
            'is_active': 'y', 'terminal_enabled': 'y',
            'idle_timeout_enabled': 'y',
        }, follow_redirects=True)
        with self.app.app_context():
            g = Game.query.filter_by(slug='test-normal-door-on').first()
            self.assertIsNotNone(g)
            self.assertTrue(g.idle_timeout_enabled)

    def test_edit_game_can_toggle_it_off(self):
        from anetbbs.models import db, Game
        with self.app.app_context():
            g = Game(name='Toggle Test', slug='toggle-idle-test',
                     game_type='door_native', idle_timeout_enabled=True)
            db.session.add(g)
            db.session.commit()
            game_id = g.id

        client = self._client_as_admin()
        client.post(f'/admin/games/{game_id}/edit', data={
            'name': 'Toggle Test', 'slug': 'toggle-idle-test',
            'category': 'other', 'min_access_level': '0',
            'game_type': 'door_native', 'max_nodes': '1', 'sort_order': '0',
            'is_active': 'y', 'terminal_enabled': 'y',
            # idle_timeout_enabled omitted -- toggling off
        }, follow_redirects=True)

        with self.app.app_context():
            refreshed = Game.query.get(game_id)
            self.assertFalse(refreshed.idle_timeout_enabled)


if __name__ == '__main__':
    unittest.main()
