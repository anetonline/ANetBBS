"""Regression test for a real Medium finding from a security/
performance audit (2026-09-10): anetbbs/features/darkforces_term.py's
detect_sixel_support() called web_app.create_app() directly and pushed
its app_context() without ever disposing the fresh SQLAlchemy engine
that db.init_app() registers on every call -- the exact same
per-call-fresh-app-and-engine shape as the real live incident
documented in features/bbs_ui.py's _app() docstring (RAM 14.5GB ->
19.8GB in ~12 minutes with only 2-3 sessions connected). Throttled
compared to that incident (detect_sixel_support() only runs once per
session, cached via session._sixel_ok, not on a 5-second watchdog
loop), but still one leaked engine per distinct login session that
ever launches this door, unboundedly over server uptime.

bbs_ui.py's own near-identical _detect_sixel_support() (this function
is explicitly a port of it, per its own module docstring) already gets
this right by using its module-level cached _app() instead. Fixed by
routing darkforces_term.py's version through the same cached _app().

Verified via patching web_app.create_app() to raise if called at all
-- proof the leaky path is genuinely gone, not just "also" calling the
cached helper alongside it.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FakeSession:
    def __init__(self, uid):
        self.user = {'id': uid}
        self.written = []

    async def write(self, text):
        self.written.append(text)


class DarkforcesSixelAppLeakTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.darkforces_sixel_leak_test.db')
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

    def setUp(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            User.query.filter_by(username='dfsixeltest').delete()
            db.session.commit()

    def _make_user(self, sixel_mode='forced_on'):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username='dfsixeltest', email='dfsixel@example.test',
                     sixel_mode=sixel_mode)
            u.set_password('whatever123')
            db.session.add(u)
            db.session.commit()
            return u.id

    async def test_detect_sixel_support_never_calls_create_app_directly(self):
        import anetbbs.web_app as web_app_mod
        import anetbbs.features.darkforces_term as df

        uid = self._make_user(sixel_mode='forced_on')
        session = FakeSession(uid)

        def _boom(*a, **kw):
            raise AssertionError(
                'detect_sixel_support() must reuse bbs_ui._app() (a '
                'cached, shared Flask app), not build a fresh one via '
                'web_app.create_app() -- that leaks a new, never-'
                'disposed SQLAlchemy engine on every call')

        with patch.object(web_app_mod, 'create_app', side_effect=_boom), \
             patch('anetbbs.features.bbs_ui._app', lambda: self.app):
            result = await df.detect_sixel_support(session)

        self.assertTrue(result, 'forced_on sixel_mode must resolve to True')

    async def test_sixel_mode_preference_is_still_honored_via_cached_app(self):
        import anetbbs.features.darkforces_term as df

        uid = self._make_user(sixel_mode='forced_off')
        session = FakeSession(uid)

        with patch('anetbbs.features.bbs_ui._app', lambda: self.app):
            result = await df.detect_sixel_support(session)

        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
