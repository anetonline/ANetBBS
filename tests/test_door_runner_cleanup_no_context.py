"""Regression test for a real live bug found testing door_mystic_mps
(the first real end-to-end test of that door type): five separate
cleanup call sites in anetbbs/games/door_runner.py all used the same
`if app is not None: with app.app_context(): _cleanup_session(...)
else: _cleanup_session(...)` pattern -- when the earlier
`current_app._get_current_object()` capture came back None for
whatever reason, cleanup ran with NO Flask app context at all.
_cleanup_session's own DB access then raised "Working outside of
application context", silently caught and merely logged -- meaning the
GameSession was never marked completed and the node was never
released.

Fixed by _cleanup_session_safe(), which builds a fresh throwaway app
context (same pattern used elsewhere in this codebase for terminal-side
code with no ambient Flask context) whenever the caller's own capture
came back empty, guaranteeing cleanup always has a context to work
with. This reproduces the exact failure condition -- app=None, no
ambient Flask context active -- and confirms the session actually gets
marked completed instead of silently failing.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _fresh_app  # noqa: E402


class CleanupSessionSafeNoAmbientContextTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game, User, GameSession
        self._ctx = self.app.app_context()
        self._ctx.push()
        db.create_all()

        # _cleanup_session_safe(app=None) -- exactly what this test
        # exercises -- builds its OWN throwaway Flask app internally
        # (`Flask(__name__)` + `get_config(os.environ.get('FLASK_ENV',
        # 'production'))`, see door_runner.py) rather than reusing this
        # test's app context. Same real gotcha test_door_games_menu_
        # layout.py's own setUp() already documents and fixes for
        # show_door_menu()'s identical pattern: without FLASK_ENV=
        # testing set here, that internal get_config() call resolves to
        # ProductionConfig instead of the SAME TestingConfig.
        # SQLALCHEMY_DATABASE_URI _fresh_app() just patched -- pointing
        # cleanup at a completely different (real, un-created) database
        # and making "no such table" failures depend entirely on
        # whatever FLASK_ENV happened to be left set by an earlier test
        # in the same process, rather than this test's own setup.
        self._orig_flask_env = os.environ.get('FLASK_ENV')
        os.environ['FLASK_ENV'] = 'testing'
        self.addCleanup(self._restore_flask_env)

        user = User(username='tester', email='t@example.com')
        user.set_password('x')
        db.session.add(user)
        db.session.flush()

        game = Game(name='Mystic MRC', slug='mystic-mrc-test',
                    game_type='door_mystic_mps')
        db.session.add(game)
        db.session.flush()

        gs = GameSession(game_id=game.id, user_id=user.id, node_number=1,
                         status='active')
        db.session.add(gs)
        db.session.commit()
        self.session_id = gs.id
        self.game_id = game.id

        self._ctx.pop()  # No ambient context from here on -- the real bug condition.
        self.addCleanup(self._pop_if_needed)

    def _pop_if_needed(self):
        pass  # already popped in setUp; nothing to clean up

    def _restore_flask_env(self):
        if self._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = self._orig_flask_env

    def test_cleanup_with_app_none_still_marks_session_completed(self):
        from anetbbs.games import door_runner
        # No ambient Flask context is active right now (popped above) --
        # this reproduces the real bug's exact condition (reader_app
        # ended up None, no context anywhere in the call stack).
        door_runner._cleanup_session_safe(self.session_id, app=None)

        with self.app.app_context():
            from anetbbs.models import GameSession
            gs = GameSession.query.get(self.session_id)
            self.assertEqual(gs.status, 'completed')
            self.assertIsNotNone(gs.ended_at)

    def test_cleanup_with_app_none_does_not_raise(self):
        from anetbbs.games import door_runner
        try:
            door_runner._cleanup_session_safe(self.session_id, app=None)
        except RuntimeError as e:
            self.fail(f'_cleanup_session_safe raised with app=None: {e}')


if __name__ == '__main__':
    unittest.main()
