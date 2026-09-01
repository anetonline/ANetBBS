"""Regression test for a real live bug reported by Jerry (2026-09-01):
Who's Online showed a user actively chatting in MRC on page
"/mrc/auth-check" with IP 127.0.0.1 instead of their own page/IP.

Root cause: /mrc/auth-check is nginx's own internal auth_request
sub-request (see anetbbs/web/mrc_web.py's own docstring -- "never
reached by users directly, nginx is the only caller"). nginx forwards
the browser's session cookie when making that sub-request, so Flask
sees a genuinely authenticated request -- but the actual HTTP
connection is nginx-to-Flask on localhost, not the user's browser, so
request.remote_addr is nginx's own loopback address, not the user's
real IP. Every WebSocket (re)connect to MRC re-triggers this check,
silently overwriting the correct page/IP web_app.py's before_request
hook (track_user_session) had already recorded moments earlier.

First fix attempt skipped this endpoint entirely (like the existing
static-asset skip in the same hook -- see
test_theme_default_and_static_who.py's
test_static_asset_request_does_not_update_session_page). That stopped
the wrong page/IP but ALSO stopped last_seen from ever being
refreshed for it -- reported live immediately after that fix shipped:
a user chatting purely over the WebSocket (no other page loads) makes
no further real Flask requests except nginx's own repeated
auth_request re-checks, so without SOME heartbeat there they silently
aged out of who's-online's 5-minute window despite being actively
online the whole time. Real fix: still refresh last_seen for this
endpoint, just don't let it overwrite page/ip_address/user_agent.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


class WhoOnlineMrcAuthCheckSkipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()

        import anetbbs.config as cfg_mod
        cls._dbfile = str(Path(__file__).resolve().parent / '.mrc_auth_check_skip_test.db')
        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._dbfile}'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User

        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

        with cls.app.app_context():
            db.create_all()
            plain = User(username='vanny', email='vanny@example.com', is_admin=False)
            plain.set_password('password123')
            db.session.add(plain)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        import shutil
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        # Each test logs in with a fresh test_client (its own cookie
        # jar -> its own presence_session_key), so without clearing
        # prior UserSession rows here, a later test's `.first()` query
        # can pick up an earlier test's leftover row for the same user
        # instead of the one it just created.
        from anetbbs.models import db, UserSession
        with self.app.app_context():
            UserSession.query.delete()
            db.session.commit()

    def test_mrc_auth_check_request_does_not_clobber_session_page_or_ip(self):
        from anetbbs.models import User, UserSession

        client = self.app.test_client()
        client.post('/auth/login', data={'username': 'vanny', 'password': 'password123'},
                    follow_redirects=True)

        # A real page view first (the MRC chat page itself).
        client.get('/mrc/')
        with self.app.app_context():
            user = User.query.filter_by(username='vanny').first()
            session = UserSession.query.filter_by(user_id=user.id).first()
            self.assertIsNotNone(session)
            self.assertEqual(session.page, '/mrc/')
            real_ip = session.ip_address

        # Then the WebSocket upgrade fires nginx's auth_request check --
        # simulated here as a plain authenticated GET, same as a real
        # browser test client hitting it directly would look from
        # Flask's point of view.
        auth_resp = client.get('/mrc/auth-check')
        self.assertEqual(auth_resp.status_code, 204)

        with self.app.app_context():
            user = User.query.filter_by(username='vanny').first()
            session = UserSession.query.filter_by(user_id=user.id).first()
            self.assertEqual(session.page, '/mrc/',
                             'must still show the real page, not /mrc/auth-check')
            self.assertEqual(session.ip_address, real_ip,
                             "must not overwrite the user's real IP with "
                             "whatever remote_addr this internal request happened "
                             "to arrive from")

    def test_mrc_auth_check_still_refreshes_last_seen(self):
        """Direct regression test for the live bug caught right after
        the first fix shipped: a user idling in MRC over the WebSocket
        (no other page loads) must NOT age out of who's-online's
        5-minute window just because their only remaining Flask
        traffic is nginx's own repeated auth_request re-checks."""
        import time
        from anetbbs.models import User, UserSession, db

        client = self.app.test_client()
        client.post('/auth/login', data={'username': 'vanny', 'password': 'password123'},
                    follow_redirects=True)
        client.get('/mrc/')

        with self.app.app_context():
            user = User.query.filter_by(username='vanny').first()
            session = UserSession.query.filter_by(user_id=user.id).first()
            # Force last_seen artificially stale, as if some real time
            # had passed since the initial /mrc/ page load.
            session.last_seen = session.last_seen.replace(year=2000)
            db.session.commit()
            stale_last_seen = session.last_seen

        time.sleep(0.05)
        auth_resp = client.get('/mrc/auth-check')
        self.assertEqual(auth_resp.status_code, 204)

        with self.app.app_context():
            user = User.query.filter_by(username='vanny').first()
            session = UserSession.query.filter_by(user_id=user.id).first()
            self.assertGreater(
                session.last_seen, stale_last_seen,
                'last_seen must still be refreshed by this endpoint -- '
                'otherwise a user chatting purely over the WebSocket '
                'ages out of who\'s-online despite being actively online')
            # page/IP must still be untouched by this same request.
            self.assertEqual(session.page, '/mrc/')


if __name__ == '__main__':
    unittest.main()
