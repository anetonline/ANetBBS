"""Regression test for the new admin "web users online" kick feature
(anetbbs/web/control.py's who_kick(), anetbbs/web_app.py's
track_user_session() kick check, UserSession.kick_requested/
kick_reason).

Real gap this closes: NodeSpy already lets a sysop force-disconnect a
terminal (telnet/SSH) session, but there was no equivalent for a web
(browser) session at all -- only a UserSession row's presence, no way
to act on it. Unlike a terminal session (a live socket a background
asyncio task can proactively watch and close), a web session is a
plain signed cookie with no server-side handle to close -- so the kick
is enforced on that browser's NEXT request instead, via the same
before_request hook (track_user_session()) that already runs on every
request.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AdminWebSessionKickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.web_session_kick_test.db')
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

    _counter = 0

    def _make_user(self, is_admin=False):
        from anetbbs.models import db, User
        AdminWebSessionKickTests._counter += 1
        n = AdminWebSessionKickTests._counter
        with self.app.app_context():
            u = User(username=f'kicktest{n}', email=f'kicktest{n}@example.com',
                     is_active=True, is_admin=is_admin)
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            return u.id

    def _logged_in_client(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _establish_web_session(self, client):
        """A real request through track_user_session() -- creates the
        UserSession row and stashes presence_session_key, exactly like
        a real browser's first page load. Returns that UserSession's id."""
        from anetbbs.models import UserSession
        resp = client.get('/messages/')
        self.assertNotEqual(resp.status_code, 401)
        with client.session_transaction() as sess:
            key = sess.get('presence_session_key')
        self.assertIsNotNone(key, 'track_user_session() must stash a session key')
        with self.app.app_context():
            row = UserSession.query.filter_by(session_key=key).first()
            self.assertIsNotNone(row)
            return row.id

    def test_admin_kick_endpoint_sets_the_flag_and_logs_the_target_out_next_request(self):
        victim_id = self._make_user()
        admin_id = self._make_user(is_admin=True)
        victim = self._logged_in_client(victim_id)
        admin = self._logged_in_client(admin_id)

        session_id = self._establish_web_session(victim)

        # Victim is still fine right now (kick hasn't happened yet).
        resp = victim.get('/messages/', follow_redirects=False)
        self.assertNotIn(resp.status_code, (302, 401, 403))

        # Admin kicks that specific session via the real endpoint.
        resp = admin.post(f'/admin/control/who/{session_id}/kick',
                          data={'reason': 'testing'},
                          headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])

        # Same victim client, same cookies, no re-login -- must be
        # logged out on its very next request.
        resp = victim.get('/messages/', follow_redirects=False)
        self.assertIn(resp.status_code, (302, 401, 403))
        if resp.status_code == 302:
            self.assertIn('/auth/login', resp.headers.get('Location', ''))

    def test_kicked_session_row_is_removed_not_left_dangling(self):
        from anetbbs.models import UserSession
        victim_id = self._make_user()
        admin_id = self._make_user(is_admin=True)
        victim = self._logged_in_client(victim_id)
        admin = self._logged_in_client(admin_id)

        session_id = self._establish_web_session(victim)
        admin.post(f'/admin/control/who/{session_id}/kick',
                  data={'reason': ''},
                  headers={'X-Requested-With': 'XMLHttpRequest'})
        victim.get('/messages/')  # triggers the kick check + row delete

        with self.app.app_context():
            self.assertIsNone(UserSession.query.get(session_id))

    def test_non_admin_cannot_kick(self):
        victim_id = self._make_user()
        bystander_id = self._make_user(is_admin=False)
        victim = self._logged_in_client(victim_id)
        bystander = self._logged_in_client(bystander_id)

        session_id = self._establish_web_session(victim)
        resp = bystander.post(f'/admin/control/who/{session_id}/kick',
                              data={'reason': 'nope'},
                              headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertIn(resp.status_code, (302, 401, 403))

        # Victim must be completely unaffected.
        resp = victim.get('/messages/', follow_redirects=False)
        self.assertNotIn(resp.status_code, (302, 401, 403))

    def test_kicking_a_terminal_protocol_session_is_refused(self):
        """NodeSpy already owns terminal kicks -- a UserSession.
        kick_requested flag would silently never be checked by a
        terminal process (it never runs web_app.py's before_request
        hooks), so who_kick() must refuse rather than pretend it worked."""
        from anetbbs.models import db, UserSession
        admin_id = self._make_user(is_admin=True)
        admin = self._logged_in_client(admin_id)
        term_user_id = self._make_user()

        with self.app.app_context():
            row = UserSession(user_id=term_user_id, session_key='term-sess-key',
                              page='[ssh]boards')
            db.session.add(row)
            db.session.commit()
            row_id = row.id

        resp = admin.post(f'/admin/control/who/{row_id}/kick',
                          data={'reason': 'x'},
                          headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()['ok'])

        with self.app.app_context():
            row = db.session.get(UserSession, row_id)
            self.assertFalse(row.kick_requested,
                             'a refused kick must not set the flag anyway')

    def test_who_json_marks_web_rows_kickable_and_terminal_rows_not(self):
        from anetbbs.models import db, UserSession
        admin_id = self._make_user(is_admin=True)
        admin = self._logged_in_client(admin_id)
        web_user_id = self._make_user()
        term_user_id = self._make_user()

        with self.app.app_context():
            db.session.add(UserSession(user_id=web_user_id, session_key='web-key',
                                       page='/boards/'))
            db.session.add(UserSession(user_id=term_user_id, session_key='term-key',
                                       page='[telnet]main menu'))
            db.session.commit()

        data = admin.get('/admin/control/who.json').get_json()
        by_page = {d['page']: d for d in data}
        self.assertTrue(by_page['/boards/']['kickable'])
        self.assertEqual(by_page['/boards/']['protocol'], 'web')
        self.assertFalse(by_page['[telnet]main menu']['kickable'])
        self.assertEqual(by_page['[telnet]main menu']['protocol'], 'telnet')

    def test_ordinary_session_unaffected_when_nobody_is_kicked(self):
        """The fix must not accidentally log anyone out on its own."""
        victim_id = self._make_user()
        victim = self._logged_in_client(victim_id)
        self._establish_web_session(victim)
        resp = victim.get('/messages/', follow_redirects=False)
        self.assertNotIn(resp.status_code, (302, 401, 403))

    def test_mocked_query_returning_a_non_boolean_kick_requested_is_not_treated_as_kicked(self):
        """Real regression found live by this exact test suite: another
        test elsewhere (test_pulse_dashboard.py's
        test_status_api_survives_user_session_failure) mocks
        UserSession.query wholesale for an unrelated resilience check.
        track_user_session()'s user_session = UserSession.query...first()
        then becomes a MagicMock, whose auto-generated .kick_requested
        attribute is truthy by default -- a bare `if user_session.
        kick_requested:` treated that as a real kick and called
        db.session.delete() on a non-mapped object, corrupting
        SQLAlchemy's unit of work for the rest of that test. Must check
        `is True` specifically, matching the fact that a real Boolean
        column can only ever legitimately be True/False/None."""
        from unittest.mock import MagicMock, patch
        from anetbbs.models import UserSession
        victim_id = self._make_user()
        victim = self._logged_in_client(victim_id)

        with self.app.app_context():
            with patch.object(UserSession, 'query') as mock_query:
                mock_query.filter_by.return_value.first.return_value = MagicMock()
                resp = victim.get('/messages/', follow_redirects=False)
        # Must behave like an ordinary request -- NOT get treated as a
        # kick (which would 302 to /auth/login and blow up trying to
        # db.session.delete() the mock).
        self.assertNotIn(resp.status_code, (302, 401, 403))


if __name__ == '__main__':
    unittest.main()
