"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): games_admin.py's tw2_reset_universe()
route was the only one of 13 admin routes in that file missing
@login_required above @_admin_required. Not an actual authorization
bypass -- access_control.py's require_admin_or_403() (aliased
_admin_required) already checks `current_user.is_authenticated` before
`.is_admin` and aborts either way -- but it IS an observable behavior
difference: every other admin route in this file redirects an
anonymous request to the login page (Flask-Login's own
@login_required, since login_manager.login_view is configured), while
this one alone 403'd anonymous requests directly. Fixed by adding the
missing decorator for consistency with every sibling route.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class Tw2ResetUniverseLoginRequiredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tw2_reset_login_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_anonymous_request_redirects_to_login_not_bare_403(self):
        """The observable behavior change: an anonymous POST must now
        be intercepted by Flask-Login's own @login_required (a 302
        redirect to the configured login_view) before ever reaching
        _admin_required's abort(403) -- matching every sibling admin
        route in this file (e.g. clear_stale_sessions)."""
        client = self.app.test_client()
        resp = client.post('/admin/games/tw2/reset-universe',
                           follow_redirects=False)
        self.assertEqual(
            resp.status_code, 302,
            'an anonymous request must be redirected to login '
            '(matching every other admin route in games_admin.py), not '
            f'reach _admin_required and 403 directly -- got {resp.status_code}')
        self.assertIn('/login', resp.headers.get('Location', ''))

    def test_matches_sibling_admin_route_behavior_for_anonymous_requests(self):
        """Direct comparison: this route and a known-consistent sibling
        (clear_stale_sessions) must behave identically for the same
        anonymous request."""
        client = self.app.test_client()
        resp_tw2 = client.post('/admin/games/tw2/reset-universe',
                               follow_redirects=False)
        resp_sibling = client.post('/admin/games/sessions/clear-stale',
                                   follow_redirects=False)
        self.assertEqual(resp_tw2.status_code, resp_sibling.status_code)


if __name__ == '__main__':
    unittest.main()
