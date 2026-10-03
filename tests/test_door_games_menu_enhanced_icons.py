"""Regression test for the Enhanced Client's door-games category menu
(anetbbs/features/games.py, GameManager.show_door_menu()) -- Jerry's
explicit ask, same treatment the main menu and login screen already
got.

This menu is read_line()-driven (a choice can be multi-digit, e.g.
"16"), unlike every other menu already converted to real clickable
buttons (single-keystroke, read_key()/read_key_arrow()-based). Proves
the enhanced_protocol.encode_menu() 'send' override (see that
function's own docstring) correctly lets a click submit a multi-digit
choice -- "16\\r", not just "16" -- so the EXISTING read_line()/
numbered[] dispatch logic below needs no changes to support it.

Reuses tests/test_door_games_menu_layout.py's own _fresh_app()/
_FakeSession()/_FakeWriter() fixture shapes (real seeded DB, since
show_door_menu() builds its category grouping from real SQLAlchemy
query results) rather than importing them, matching this repo's
existing convention of self-contained per-file fixtures.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class _FakeWriter:
    def __init__(self, sink):
        self._sink = sink

    def write(self, data):
        self._sink.append(data.decode('utf-8'))

    async def drain(self):
        pass


class _FakeSession:
    def __init__(self, responses):
        self.user = None
        self._responses = list(responses)
        self.written = []
        self.window_size = (80, 24)
        self.term_mode = 'enhanced'
        self.writer = _FakeWriter(self.written)
        self._enhanced_vt_state = 'should get reset by show_door_menu()'

    async def write(self, text):
        self.written.append(text)

    async def _drain_protected(self):
        pass

    async def clear_screen(self):
        await self.write('\x1b[2J\x1b[H\x1b[0m')

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    def transcript(self):
        return ''.join(self.written)


class DoorGamesMenuEnhancedIconsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        from anetbbs.web_app import _create_default_data
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()
        _create_default_data()
        self._orig_flask_env = os.environ.get('FLASK_ENV')
        os.environ['FLASK_ENV'] = 'testing'
        self.addCleanup(self._restore_flask_env)

        from anetbbs.models import Game, GameCategory
        if not GameCategory.query.filter_by(slug='arcade').first():
            db.session.add(GameCategory(slug='arcade', name='Arcade', as_submenu=True))
        db.session.add_all([
            Game(name='Synthetic Arcade One', slug='synthetic-arcade-one',
                category='arcade', game_type='door_native', is_active=True),
            Game(name='Synthetic Puzzle', slug='synthetic-puzzle',
                category='puzzle', game_type='door_native', is_active=True),
        ])
        db.session.commit()

    def _restore_flask_env(self):
        if self._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = self._orig_flask_env

    def test_sends_a_structured_menu_not_ansi_text(self):
        # Note: _FakeSession.read_line() is a mock that returns the
        # already-parsed line directly, same as a REAL read_line()
        # would (it consumes raw bytes up to Enter and strips the
        # terminator itself before returning) -- scripted responses
        # here are what read_line() RETURNS, not the raw "send" bytes
        # a click synthesizes onto the wire (see
        # test_category_item_send_value_is_the_number_plus_enter for
        # that distinction, which is the actual point of this feature).
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())

        msg = json.loads(session.written[0])
        self.assertEqual(msg['type'], 'menu')
        self.assertEqual(msg['title'], 'Door Games')
        # No raw ANSI escape ever reached the transcript -- confirms
        # the ansi-rendering branch (clear_screen()/write() calls) was
        # genuinely skipped, not just that a menu message ALSO got sent
        # alongside it.
        self.assertNotIn('\x1b[', session.transcript())

    def test_resets_the_persistent_vt_state(self):
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        self.assertIsNone(session._enhanced_vt_state)

    def test_category_item_send_value_is_the_number_plus_enter(self):
        """The real point of this whole feature: a click on the
        'arcade' category button must submit exactly like typing its
        number then pressing Enter would -- not just the bare number,
        which would leave read_line() still waiting."""
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())

        msg = json.loads(session.written[0])
        items_by_label = {it['label']: it for it in msg['items']}
        arcade_items = [it for lbl, it in items_by_label.items() if lbl.startswith('Arcade')]
        self.assertEqual(len(arcade_items), 1)
        arcade_item = arcade_items[0]
        self.assertTrue(arcade_item['send'].endswith('\r'))
        self.assertEqual(arcade_item['send'], arcade_item['hotkey'] + '\r')

    def test_return_item_also_submits_with_a_trailing_enter(self):
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())

        msg = json.loads(session.written[0])
        q_item = next(it for it in msg['items'] if it['label'] == 'Return')
        self.assertEqual(q_item['send'], 'Q\r')

    def test_a_synthesized_click_actually_navigates_into_the_submenu(self):
        """End-to-end: a click on the Arcade category button sends
        "<N>\\r" onto the wire (confirmed by the previous test); a
        REAL read_line() consumes those raw bytes and returns just
        "<N>" (the Enter terminates the line, same as any other typed
        choice) -- that's what this test scripts as the response,
        confirming show_door_menu() actually drills into the right
        category submenu once a click's choice arrives, not just that
        the right 'send' bytes were computed for it."""
        from anetbbs.features.games import GameManager
        probe = _FakeSession(['Q'])
        asyncio.run(GameManager(probe).show_door_menu())
        first_msg = json.loads(probe.written[0])
        arcade_number = next(
            it['hotkey'] for it in first_msg['items'] if it['label'].startswith('Arcade'))

        session = _FakeSession([arcade_number, 'B', 'Q'])
        asyncio.run(GameManager(session).show_door_menu())
        msg_structs = [json.loads(w) for w in session.written if w.strip().startswith('{')]
        self.assertGreaterEqual(len(msg_structs), 2)
        # Second message (after drilling into the submenu) must be a
        # DIFFERENT menu (the category's own games), not a repeat of
        # the top-level one.
        self.assertNotEqual(msg_structs[0]['title'], msg_structs[1]['title'])
        self.assertIn('Arcade', msg_structs[1]['title'])


if __name__ == '__main__':
    unittest.main()
