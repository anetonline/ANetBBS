"""Regression test for a real finding from a security/performance audit:
sysop_paging.page_sysop() had NO rate limit of its own, and its only
caller (menu_engine.py's _act_page) had none either -- any logged-in
user could spam the "page the sysop" menu action with a scripted
client, each call writing a SysopPage row, firing a webhook POST in a
background thread, and pushing a live socketio toast to every
connected sysop browser tab.

Fixed by gating page_sysop() on a sliding-window rate-limit bucket
(_rate_limited(), 3 pages / 5 minutes per user) via
features/rate_limit.py's _check() -- called directly rather than
through its Flask-route decorator, since this runs from a plain
terminal session with no Flask request context.

This test confirms: the 4th page within the window is rejected (no
SysopPage row written, page_sysop() returns 0), a *different* user is
unaffected, and the same user succeeds again once the window's oldest
entry falls out of the deque, all without touching Flask/webhooks."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class SysopPagingRateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.sysop_paging_rl_test.db')
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

    def _make_user(self, username):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=False, access_level=10,
                          email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            return u.id

    def setUp(self):
        # rate_limit._buckets is a module-level dict shared for the life
        # of the process -- clear any stale entry for the keys this test
        # is about to use so an earlier test run (or test order) can't
        # leave the bucket already primed.
        from anetbbs.features import rate_limit as rl_mod
        self._rl_mod = rl_mod
        self._used_keys = set()

    def tearDown(self):
        for k in self._used_keys:
            self._rl_mod._buckets.pop(k, None)

    def _page(self, user_id, text='help'):
        from anetbbs.features import sysop_paging
        self._used_keys.add(f'sysop_page:{user_id}')
        os.environ['DATABASE_URL'] = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        try:
            with self.app.app_context():
                return sysop_paging.page_sysop(user_id, text, service='telnet')
        finally:
            os.environ.pop('DATABASE_URL', None)

    def _count_pages(self, user_id):
        from anetbbs.models import SysopPage
        with self.app.app_context():
            return SysopPage.query.filter_by(user_id=user_id).count()

    def test_fourth_page_within_window_is_rejected(self):
        user_id = self._make_user('rltest_spammer')

        ids = [self._page(user_id, f'page {i}') for i in range(3)]
        self.assertTrue(all(pid > 0 for pid in ids),
                         'first 3 pages within the limit should all succeed')

        blocked_id = self._page(user_id, 'page 4 (should be blocked)')
        self.assertEqual(blocked_id, 0,
                          'a 4th page within the rate-limit window must be rejected')

        # No 4th row was actually written to the DB.
        self.assertEqual(self._count_pages(user_id), 3)

    def test_rate_limit_is_per_user(self):
        spammer_id = self._make_user('rltest_spammer2')
        other_id = self._make_user('rltest_bystander')

        for i in range(3):
            self.assertGreater(self._page(spammer_id, f'spam {i}'), 0)
        self.assertEqual(self._page(spammer_id, 'spam 4'), 0)

        # A different user's own budget is untouched by the spammer.
        self.assertGreater(self._page(other_id, 'legit page'), 0)


if __name__ == '__main__':
    unittest.main()
