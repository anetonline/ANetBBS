"""Regression tests for the A-Net Game Server bulk-import admin routes
(GET/POST /admin/games/anet-import, anetbbs/web/games_admin.py).
Mocks the actual network scrape (anetbbs.features.anet_game_import.
scrape_games) throughout -- these tests are about the admin
route/form/DB wiring, not the live external site; scrape_games()
itself is covered directly in tests/test_anet_game_import.py.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'

_FAKE_GAMES = [
    {'name': 'Legend of the Red Dragon', 'code': 'LORD408', 'category': 'Arcade', 'is_new': False},
    {'name': 'Trade Wars 2002', 'code': 'TW2002', 'category': 'Arcade', 'is_new': True},
    {'name': 'Solar Realms Elite', 'code': 'SRE', 'category': 'RPG', 'is_new': False},
]


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _make_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'

    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class AnetImportAdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _login_as_admin(self, app, client):
        from anetbbs.models import db, User
        with app.app_context():
            admin = User.query.filter_by(username='admin').first()
            if admin is None:
                admin = User(username='admin', email='admin@example.com', is_admin=True)
                admin.set_password('password123')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

    def _ensure_base_server(self, app):
        from anetbbs.models import db, Game
        with app.app_context():
            game = Game.query.filter_by(slug='a-net-game-server').first()
            if game is None:
                game = Game(name='A-Net Game Server', slug='a-net-game-server',
                           game_type='door_rlogin')
                db.session.add(game)
            game.executable_path = 'game.a-net-online.lol:513'
            game.command_line_args = '@USER@ testpass123'
            game.rlogin_bbs_tag = 'ANET'
            db.session.commit()

    def test_get_shows_scraped_categories_and_counts(self):
        app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        client = app.test_client()
        self._login_as_admin(app, client)

        with patch('anetbbs.features.anet_game_import.requests.get') as mock_get:
            from unittest.mock import Mock
            resp = Mock()
            resp.raise_for_status = Mock()
            resp.text = ''
            mock_get.return_value = resp
            with patch('anetbbs.features.anet_game_import.scrape_games',
                       return_value=_FAKE_GAMES):
                r = client.get('/admin/games/anet-import')
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn('Arcade', body)
        self.assertIn('RPG', body)
        self.assertIn('2', body)  # Arcade has 2 games

    def test_get_when_scrape_fails_redirects_with_a_flash(self):
        from anetbbs.features.anet_game_import import AnetGameImportError
        app = _make_app(str(Path(self._tmp.name) / 'b.db'))
        client = app.test_client()
        self._login_as_admin(app, client)

        with patch('anetbbs.features.anet_game_import.scrape_games',
                   side_effect=AnetGameImportError('site unreachable')):
            r = client.get('/admin/games/anet-import', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'site unreachable', r.data)

    def test_post_imports_selected_category_into_existing_local_category(self):
        app = _make_app(str(Path(self._tmp.name) / 'c.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        self._ensure_base_server(app)

        from anetbbs.models import db, GameCategory, Game
        with app.app_context():
            db.session.add(GameCategory(name='My Arcade', slug='my-arcade'))
            db.session.commit()

        with patch('anetbbs.features.anet_game_import.scrape_games',
                   return_value=_FAKE_GAMES):
            r = client.post('/admin/games/anet-import', data={
                'cat_arcade': 'my-arcade',
                'cat_rpg': '',  # skip RPG
            }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)

        with app.app_context():
            lord = Game.query.filter_by(slug='anet-lord408').first()
            self.assertIsNotNone(lord)
            self.assertEqual(lord.category, 'my-arcade')
            self.assertEqual(lord.game_type, 'door_rlogin')
            self.assertEqual(lord.command_line_args, '@USER@ testpass123 xtrn=LORD408')
            self.assertEqual(lord.rlogin_bbs_tag, 'ANET')
            self.assertIsNotNone(Game.query.filter_by(slug='anet-tw2002').first())
            # RPG category was skipped entirely
            self.assertIsNone(Game.query.filter_by(slug='anet-sre').first())

    def test_post_create_new_category_creates_it(self):
        app = _make_app(str(Path(self._tmp.name) / 'd.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        self._ensure_base_server(app)

        with patch('anetbbs.features.anet_game_import.scrape_games',
                   return_value=_FAKE_GAMES):
            client.post('/admin/games/anet-import', data={
                'cat_arcade': '__new__',
                'cat_rpg': '',
            }, follow_redirects=True)

        from anetbbs.models import GameCategory, Game
        with app.app_context():
            self.assertIsNotNone(GameCategory.query.filter_by(slug='arcade').first())
            lord = Game.query.filter_by(slug='anet-lord408').first()
            self.assertEqual(lord.category, 'arcade')

    def test_post_is_idempotent_on_rerun(self):
        app = _make_app(str(Path(self._tmp.name) / 'e.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        self._ensure_base_server(app)

        from anetbbs.models import Game
        with patch('anetbbs.features.anet_game_import.scrape_games',
                   return_value=_FAKE_GAMES):
            client.post('/admin/games/anet-import',
                       data={'cat_arcade': '__new__', 'cat_rpg': '__new__'},
                       follow_redirects=True)
            with app.app_context():
                count_after_first = Game.query.filter(Game.slug.like('anet-%')).count()

            r2 = client.post('/admin/games/anet-import',
                            data={'cat_arcade': '__new__', 'cat_rpg': '__new__'},
                            follow_redirects=True)
        self.assertEqual(r2.status_code, 200)
        with app.app_context():
            count_after_second = Game.query.filter(Game.slug.like('anet-%')).count()
        self.assertEqual(count_after_first, count_after_second,
                         're-running the import must not create duplicate rows')
        self.assertIn(b'already imported', r2.data)

    def test_post_with_missing_base_server_shows_clear_error(self):
        app = _make_app(str(Path(self._tmp.name) / 'f.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        # Deliberately do NOT call _ensure_base_server -- but the real
        # seed still runs on create_app(), so delete it to simulate a
        # genuinely missing/misconfigured base server.
        from anetbbs.models import db, Game
        with app.app_context():
            existing = Game.query.filter_by(slug='a-net-game-server').first()
            if existing is not None:
                db.session.delete(existing)
                db.session.commit()

        with patch('anetbbs.features.anet_game_import.scrape_games',
                   return_value=_FAKE_GAMES):
            r = client.post('/admin/games/anet-import',
                           data={'cat_arcade': '__new__'},
                           follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'No active', r.data)
        with app.app_context():
            self.assertIsNone(Game.query.filter_by(slug='anet-lord408').first())

    def test_post_with_only_an_inactive_base_server_shows_clear_error(self):
        """Direct regression test for the real live bug (2026-09-01):
        an inactive bundled a-net-game-server row must never be usable
        as the credential source, even when it's the only door_rlogin
        row pointed at the real host -- it must be treated exactly
        like "missing", not silently used."""
        app = _make_app(str(Path(self._tmp.name) / 'f2.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, Game
        with app.app_context():
            game = Game.query.filter_by(slug='a-net-game-server').first()
            if game is None:
                game = Game(name='A-Net Game Server', slug='a-net-game-server',
                           game_type='door_rlogin')
                db.session.add(game)
            game.is_active = False
            game.executable_path = 'game.a-net-online.lol:513'
            game.command_line_args = '@USER@ neverUsedPassword'
            game.rlogin_bbs_tag = 'TBIG'
            db.session.commit()

        with patch('anetbbs.features.anet_game_import.scrape_games',
                   return_value=_FAKE_GAMES):
            r = client.post('/admin/games/anet-import',
                           data={'cat_arcade': '__new__'},
                           follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'No active', r.data)
        with app.app_context():
            self.assertIsNone(Game.query.filter_by(slug='anet-lord408').first())

    def test_post_with_no_categories_selected_imports_nothing(self):
        app = _make_app(str(Path(self._tmp.name) / 'g.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        self._ensure_base_server(app)

        from anetbbs.models import Game
        with patch('anetbbs.features.anet_game_import.scrape_games',
                   return_value=_FAKE_GAMES):
            r = client.post('/admin/games/anet-import',
                           data={'cat_arcade': '', 'cat_rpg': ''},
                           follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        with app.app_context():
            self.assertEqual(Game.query.filter(Game.slug.like('anet-%')).count(), 0)


if __name__ == '__main__':
    unittest.main()
