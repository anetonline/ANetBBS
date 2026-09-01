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

    def _clear_bundled_seed_row(self):
        """a-net-game-server is one of web_app.py's bundled-door seed
        slugs (create_app() seeds it automatically on a fresh DB) --
        remove it so each test starts from a clean slate, per the
        bundled-door-slug test-collision trap (see the anetbbs-release
        skill)."""
        from anetbbs.models import db, Game
        existing = Game.query.filter_by(slug='a-net-game-server').first()
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()

    def test_no_active_anet_game_raises_clear_error(self):
        self._clear_bundled_seed_row()
        with self.assertRaises(AnetGameImportError) as cm:
            base_server_credentials()
        self.assertIn('No active', str(cm.exception))

    def test_reads_host_password_and_tag_from_the_one_active_match(self):
        self._clear_bundled_seed_row()
        from anetbbs.models import db, Game
        db.session.add(Game(
            name='My Real Game Server', slug='my-real-anet-server',
            game_type='door_rlogin', is_active=True,
            executable_path='game.a-net-online.lol:513',
            command_line_args='@USER@ mySecretPass123',
            rlogin_bbs_tag='ANET'))
        db.session.commit()
        host_port, password, tag = base_server_credentials()
        self.assertEqual(host_port, 'game.a-net-online.lol:513')
        self.assertEqual(password, 'mySecretPass123')
        self.assertEqual(tag, 'ANET')

    def test_missing_password_in_command_line_args_raises(self):
        self._clear_bundled_seed_row()
        from anetbbs.models import db, Game
        db.session.add(Game(
            name='My Real Game Server', slug='my-real-anet-server',
            game_type='door_rlogin', is_active=True,
            executable_path='game.a-net-online.lol:513',
            command_line_args='@USER@'))  # no password token
        db.session.commit()
        with self.assertRaises(AnetGameImportError):
            base_server_credentials()

    def test_an_inactive_row_is_never_used_even_if_it_is_the_only_one(self):
        """Direct regression test for the real live bug reported by
        Jerry (2026-09-01): a sysop who added their own A-Net Game
        Server entry, under a DIFFERENT slug, before the bundled
        a-net-game-server row ever existed, and later left the bundled
        row inactive rather than deleting it -- the old code did
        `Game.query.filter_by(slug='a-net-game-server').first()`,
        finding the inactive BUNDLED row (with its own random,
        never-actually-used auto-generated password/tag) regardless of
        is_active, and silently copied those wrong credentials onto
        every single imported game. An inactive row, bundled slug or
        not, must never be usable as the credential source."""
        from anetbbs.models import db, Game
        bundled = Game.query.filter_by(slug='a-net-game-server').first()
        if bundled is None:
            bundled = Game(name='A-Net Game Server', slug='a-net-game-server',
                          game_type='door_rlogin')
            db.session.add(bundled)
        bundled.is_active = False
        bundled.executable_path = 'game.a-net-online.lol:513'
        bundled.command_line_args = '@USER@ neverActuallyUsedPassword'
        bundled.rlogin_bbs_tag = 'TBIG'
        db.session.commit()

        with self.assertRaises(AnetGameImportError) as cm:
            base_server_credentials()
        self.assertIn('No active', str(cm.exception))

    def test_two_active_matches_raises_an_ambiguity_error_instead_of_guessing(self):
        """The other half of the same bug class: even when both
        candidates are active, silently picking one (by slug, by id,
        by whatever) is exactly the kind of guess that caused the
        original bug. Must fail loudly and name both candidates so the
        sysop can deactivate the one they don't want, instead of
        silently using the wrong one."""
        # Deliberately NOT slugged 'anet-*' -- that prefix is reserved
        # for games THIS tool creates (see slug_for_code()) and is
        # excluded from candidate matching for exactly that reason
        # (see the idempotent-rerun regression this excludes).
        self._clear_bundled_seed_row()
        from anetbbs.models import db, Game
        db.session.add(Game(
            name='First Server', slug='my-first-server',
            game_type='door_rlogin', is_active=True,
            executable_path='game.a-net-online.lol:513',
            command_line_args='@USER@ pass1'))
        db.session.add(Game(
            name='Second Server', slug='my-second-server',
            game_type='door_rlogin', is_active=True,
            executable_path='game.a-net-online.lol:513',
            command_line_args='@USER@ pass2'))
        db.session.commit()

        with self.assertRaises(AnetGameImportError) as cm:
            base_server_credentials()
        msg = str(cm.exception)
        self.assertIn('my-first-server', msg)
        self.assertIn('my-second-server', msg)

    def test_previously_imported_games_are_excluded_from_candidates(self):
        """Direct regression test for a real bug caught while testing
        the fix above: every game THIS tool creates is itself an
        active door_rlogin pointed at the same host, so re-running the
        import without this exclusion always found more than one
        candidate (the real config entry PLUS every already-imported
        game) and wrongly raised the ambiguity error on the second
        run, forever, for every sysop who ever imports more than
        once."""
        self._clear_bundled_seed_row()
        from anetbbs.models import db, Game
        db.session.add(Game(
            name='My Real Server', slug='my-real-server',
            game_type='door_rlogin', is_active=True,
            executable_path='game.a-net-online.lol:513',
            command_line_args='@USER@ realpass', rlogin_bbs_tag='REAL'))
        db.session.add(Game(
            name='Legend of the Red Dragon', slug='anet-lord408',
            game_type='door_rlogin', is_active=True,
            executable_path='game.a-net-online.lol:513',
            command_line_args='@USER@ realpass xtrn=LORD408'))
        db.session.commit()

        host_port, password, tag = base_server_credentials()
        self.assertEqual(password, 'realpass')
        self.assertEqual(tag, 'REAL')

    def test_many_agreeing_candidates_is_not_treated_as_ambiguous(self):
        """Direct regression test for a real live bug reported by
        Jerry (2026-09-01): his real setup has ONE general "A-Net Game
        Server" browse-menu entry plus over a DOZEN of his own hand-
        added direct-to-door entries (Immortal Barons, LORD, BRE,
        etc.), each an independently-created active door_rlogin row
        pointed at the same host, all sharing the same per-BBS
        password/tag. The ambiguity check must only fire on real
        DISAGREEMENT between candidates, not merely "more than one
        row" -- otherwise a sysop with this completely normal, common
        setup could never use the import tool at all."""
        self._clear_bundled_seed_row()
        from anetbbs.models import db, Game
        shared_args = '@USER@ SharedPassword777'
        db.session.add(Game(
            name='A-Net Game Server', slug='A-NET-GAME-SERVER',
            game_type='door_rlogin', is_active=True,
            executable_path='game.a-net-online.lol:513',
            command_line_args=shared_args, rlogin_bbs_tag=''))
        for name, slug in [('Immortal Barons', 'imb.777'),
                           ('LORD', 'lord.777'), ('BRE', 'bre.777')]:
            db.session.add(Game(
                name=name, slug=slug, game_type='door_rlogin', is_active=True,
                executable_path='game.a-net-online.lol:513',
                command_line_args=f'{shared_args} xtrn={slug}',
                rlogin_bbs_tag=''))
        db.session.commit()

        host_port, password, tag = base_server_credentials()
        self.assertEqual(host_port, 'game.a-net-online.lol:513')
        self.assertEqual(password, 'SharedPassword777')
        self.assertEqual(tag, '')


if __name__ == '__main__':
    unittest.main()
