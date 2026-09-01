"""Regression tests for the A-Net Game Server bulk-import tool
(Jerry's ask, first of two feature requests made 2026-09-01: "I would
like a tool made for ANetBBS to be able to easily import ALL the games
A-Net Game server offers... an option in the admin door game menu, to
'Add games from A-Net Game Server' and then also be able to select the
categories of those games where you want on your local setup").

anetbbs/features/anet_game_import.py's scrape_games() intentionally
mirrors Jerry's own reference scraper script (extracted from
/home/jerry/Desktop/anet_games.zip) selector-for-selector -- same
'.code-category' / 'h3' / 'ul.flex-list > li' / 'span.door-code' /
'span.-tag' structure -- so these tests build a synthetic HTML fixture
matching that exact shape rather than hitting the real live site.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from anetbbs.features.anet_game_import import (
    scrape_games, group_by_category, category_form_key, slug_for_code,
    build_game_kwargs, base_server_credentials, AnetGameImportError,
)

_FIXTURE_HTML = """
<html><body>
<div class="code-category">
  <h3>Arcade</h3>
  <ul class="flex-list">
    <li>Legend of the Red Dragon - <span class="door-code">LORD408</span></li>
    <li>Trade Wars 2002 - <span class="door-code">TW2002</span> <span class="-tag">Added</span></li>
  </ul>
</div>
<div class="code-category">
  <h3>RPG</h3>
  <ul class="flex-list">
    <li>Solar Realms Elite - <span class="door-code">SRE</span></li>
  </ul>
</div>
<div class="code-category">
  <h3>No Games Here</h3>
  <ul class="flex-list"></ul>
</div>
</body></html>
"""


class ScrapeGamesTests(unittest.TestCase):
    def _mock_get(self, text=_FIXTURE_HTML, status=200):
        resp = Mock()
        resp.text = text
        resp.raise_for_status = Mock()
        if status != 200:
            resp.raise_for_status.side_effect = requests.HTTPError(f'{status}')
        return resp

    def test_parses_name_code_and_category(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   return_value=self._mock_get()):
            games = scrape_games()
        self.assertEqual(len(games), 3)
        lord = next(g for g in games if g['code'] == 'LORD408')
        self.assertEqual(lord['name'], 'Legend of the Red Dragon')
        self.assertEqual(lord['category'], 'Arcade')
        self.assertFalse(lord['is_new'])

    def test_added_tag_marks_is_new(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   return_value=self._mock_get()):
            games = scrape_games()
        tw2 = next(g for g in games if g['code'] == 'TW2002')
        self.assertTrue(tw2['is_new'])

    def test_empty_category_contributes_no_games(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   return_value=self._mock_get()):
            games = scrape_games()
        self.assertFalse(any(g['category'] == 'No Games Here' for g in games))

    def test_network_failure_raises_clear_error(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   side_effect=requests.ConnectionError('refused')):
            with self.assertRaises(AnetGameImportError):
                scrape_games()

    def test_http_error_raises_clear_error(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   return_value=self._mock_get(status=500)):
            with self.assertRaises(AnetGameImportError):
                scrape_games()

    def test_page_with_no_games_at_all_raises(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   return_value=self._mock_get(text='<html><body>nothing here</body></html>')):
            with self.assertRaises(AnetGameImportError):
                scrape_games()


class GroupByCategoryTests(unittest.TestCase):
    def test_groups_preserve_first_seen_order(self):
        games = [
            {'name': 'A', 'code': 'A1', 'category': 'Zeta', 'is_new': False},
            {'name': 'B', 'code': 'B1', 'category': 'Alpha', 'is_new': False},
            {'name': 'C', 'code': 'C1', 'category': 'Zeta', 'is_new': False},
        ]
        grouped = group_by_category(games)
        self.assertEqual(list(grouped.keys()), ['Zeta', 'Alpha'])
        self.assertEqual(len(grouped['Zeta']), 2)


class CategoryFormKeyTests(unittest.TestCase):
    def test_deterministic_and_url_safe(self):
        self.assertEqual(category_form_key('Sci-Fi & Space'), 'sci-fi-space')
        self.assertEqual(category_form_key('RPG'), 'rpg')
        # Same input -> same output every time (no hidden randomness),
        # since the GET review page and the POST handler both compute
        # this independently and must agree.
        self.assertEqual(category_form_key('Sci-Fi & Space'),
                         category_form_key('Sci-Fi & Space'))


class SlugForCodeTests(unittest.TestCase):
    def test_lowercases_and_prefixes(self):
        self.assertEqual(slug_for_code('LORD408'), 'anet-lord408')

    def test_sanitizes_punctuation(self):
        self.assertEqual(slug_for_code('TW-2002!'), 'anet-tw-2002')

    def test_empty_code_returns_none(self):
        self.assertIsNone(slug_for_code('---'))


class BuildGameKwargsTests(unittest.TestCase):
    def test_produces_a_door_rlogin_game_with_xtrn(self):
        game = {'name': 'Legend of the Red Dragon', 'code': 'LORD408',
               'category': 'Arcade', 'is_new': False}
        kwargs = build_game_kwargs(game, 'action', 'game.a-net-online.lol:513',
                                   'secretpass', 'ANET')
        self.assertEqual(kwargs['slug'], 'anet-lord408')
        self.assertEqual(kwargs['game_type'], 'door_rlogin')
        self.assertEqual(kwargs['executable_path'], 'game.a-net-online.lol:513')
        self.assertEqual(kwargs['command_line_args'], '@USER@ secretpass xtrn=LORD408')
        self.assertEqual(kwargs['rlogin_bbs_tag'], 'ANET')
        self.assertEqual(kwargs['category'], 'action')
        self.assertTrue(kwargs['is_active'])


_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _fresh_app(db_path):
    import os
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class BaseServerCredentialsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = __import__('os').environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import os
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            import shutil
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

    def test_missing_bundled_row_raises_clear_error(self):
        # a-net-game-server is itself one of web_app.py's bundled-door
        # seed slugs (create_app() seeds it automatically on a fresh
        # DB) -- delete it first rather than assuming a fresh DB has
        # no such row, per the bundled-door-slug test-collision trap
        # (see the anetbbs-release skill).
        from anetbbs.models import db, Game
        existing = Game.query.filter_by(slug='a-net-game-server').first()
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()
        with self.assertRaises(AnetGameImportError) as cm:
            base_server_credentials()
        self.assertIn('a-net-game-server', str(cm.exception))

    def test_reads_host_password_and_tag_from_bundled_row(self):
        # a-net-game-server is a bundled slug -- query-or-update the
        # already-seeded row instead of blind-inserting a duplicate
        # (would UNIQUE-constraint-fail whenever the seed already ran).
        from anetbbs.models import db, Game
        game = Game.query.filter_by(slug='a-net-game-server').first()
        if game is None:
            game = Game(name='A-Net Game Server', slug='a-net-game-server',
                       game_type='door_rlogin')
            db.session.add(game)
        game.executable_path = 'game.a-net-online.lol:513'
        game.command_line_args = '@USER@ mySecretPass123'
        game.rlogin_bbs_tag = 'ANET'
        db.session.commit()
        host_port, password, tag = base_server_credentials()
        self.assertEqual(host_port, 'game.a-net-online.lol:513')
        self.assertEqual(password, 'mySecretPass123')
        self.assertEqual(tag, 'ANET')

    def test_missing_password_in_command_line_args_raises(self):
        from anetbbs.models import db, Game
        game = Game.query.filter_by(slug='a-net-game-server').first()
        if game is None:
            game = Game(name='A-Net Game Server', slug='a-net-game-server',
                       game_type='door_rlogin')
            db.session.add(game)
        game.executable_path = 'game.a-net-online.lol:513'
        game.command_line_args = '@USER@'  # no password token
        db.session.commit()
        with self.assertRaises(AnetGameImportError):
            base_server_credentials()


if __name__ == '__main__':
    unittest.main()
