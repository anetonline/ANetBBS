"""Regression test for a real finding from a security/performance
audit, the same bug shape already found and fixed in
menu_engine.py's run_menu() (a broken `goto` target used to kill the
whole session instead of just failing that one navigation -- see that
module's own comment, and the try/except it wraps around its action()
call).

BBSMenuUI.show_main() (aliased from the module-level _show_main_v2()
in anetbbs/features/bbs_ui.py -- the hardcoded fallback main menu used
whenever an install has no configured BbsMenu rows) dispatched each
menu choice with no equivalent protection: an unhandled exception
raised from ANY dispatch target (a DB hiccup in list_boards(), a bad
RSS feed, a corrupt echomail row) propagated straight out of the whole
menu loop, ending the caller's entire session instead of just failing
that one menu choice and returning them to the main menu.

Fixed by wrapping the dispatch in a try/except that logs + shows a
friendly error and continues the loop for ordinary exceptions, while
still re-raising CarrierLost/asyncio.CancelledError (a real dead
connection, or task teardown, must still unwind cleanly rather than
hang on a dead socket).
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, user, responses):
        self.user = user
        self._responses = list(responses)
        self.written = []

    async def write(self, text):
        self.written.append(text)

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


class BbsUiMainMenuErrorIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.bbs_ui_menu_error_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def test_ordinary_exception_in_a_menu_action_does_not_kill_the_session(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = _FakeSession({'id': 1, 'username': 'x', 'access_level': 10,
                                'is_admin': False},
                               responses=['M', 'Q'])
        ui = BBSMenuUI(session)

        with patch.object(BBSMenuUI, 'list_boards',
                          side_effect=RuntimeError('boom: DB hiccup')), \
             self._patched_app():
            # Must return normally (the 'Q' branch), NOT propagate the
            # RuntimeError from list_boards() out of show_main().
            asyncio.run(ui.show_main())

        self.assertIn('Menu action failed', session.transcript())
        # The loop must have kept going and shown the main menu again
        # (it consumed the second scripted response, 'Q', to quit).
        self.assertEqual(session._responses, [])

    def test_carrier_lost_still_propagates_out_of_the_menu_loop(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        from anetbbs.core.session import CarrierLost

        session = _FakeSession({'id': 1, 'username': 'x', 'access_level': 10,
                                'is_admin': False},
                               responses=['M'])
        ui = BBSMenuUI(session)

        with patch.object(BBSMenuUI, 'list_boards',
                          side_effect=CarrierLost('connection reset')), \
             self._patched_app():
            with self.assertRaises(CarrierLost):
                asyncio.run(ui.show_main())


if __name__ == '__main__':
    unittest.main()
