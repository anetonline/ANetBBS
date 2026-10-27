"""Regression tests for a real live bug reported by Jerry: sending a
private message to another user on the BBS was case-sensitive on the
recipient's username, so typing "stingray" failed with "user not found"
for an account actually registered as "StingRay". Login (web and
terminal, via web/auth.py's login() and core/user_manager.py's
authenticate()) was already correctly case-insensitive on username via
the `func.lower(User.username) == value.lower()` idiom -- only a
handful of OTHER username-typed-by-a-human lookup sites had never
adopted that same convention.

A sweep for the same shape found it in several more places, all fixed
here the same way (matching the pre-existing login convention exactly,
not inventing a new one):

  - features/bbs_ui.py's _send_pm() (terminal "Send Private Message")
  - web/pm.py's compose() (web PM compose)
  - web/profile.py's view_user() (/profile/<username>)
  - web/gemini.py's view()/raw_gemtext() (/gemini/<username>[.gmi])
  - core/finger_server.py's _user_info() (finger protocol)
  - web/auth.py's forgot_password() (username-or-email identifier)
  - web/admin.py's AddUserForm/EditUserForm username-uniqueness checks

Password case sensitivity is untouched and intentionally still exact --
only the username lookup itself was ever the bug.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class UsernameLookupCaseInsensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.username_case_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, GeminiCapsule
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            mixed_case = User(username='StingRay', email='stingray@example.com',
                              is_admin=False, access_level=100, is_active=True)
            mixed_case.set_password('correcthorsebatterystaple')
            db.session.add(mixed_case)
            db.session.commit()
            cls.stingray_id = mixed_case.id

            sender = User(username='pmsendercase', email='pmsendercase@example.com',
                          password_hash='x', access_level=100)
            db.session.add(sender)
            db.session.commit()
            cls.sender_id = sender.id

            db.session.add(GeminiCapsule(
                user_id=cls.stingray_id, title="StingRay's capsule",
                content='# Hello\n\nUNIQUE_CAPSULE_MARKER\n', is_published=True))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    # ---- terminal PM compose (features/bbs_ui.py _send_pm) ----

    def test_terminal_send_pm_finds_recipient_regardless_of_case(self):
        from anetbbs.models import PrivateMessage
        from anetbbs.features.bbs_ui import BBSMenuUI

        class _FakeSession:
            def __init__(self, user, responses):
                self.user = user
                self._responses = list(responses)
                self.written = []
                # Mirrors the real Session._log_activity() -- needed
                # since _send_pm() now logs a 'pm_sent' activity row on
                # success (logging-audit gap found live, 2026-10-26).
                self._caller_log_id = None
                self._proto = 'telnet'

            async def write(self, text):
                self.written.append(text)

            async def read_line(self, prompt=''):
                if prompt:
                    await self.write(prompt)
                return self._responses.pop(0) if self._responses else ''

            def transcript(self):
                return ''.join(self.written)

            def _log_activity(self, activity_type, details=None):
                from anetbbs.models import db, UserActivity
                from anetbbs.features.bbs_ui import _app
                if not self.user:
                    return
                with _app().app_context():
                    db.session.add(UserActivity(
                        user_id=self.user.get('id'),
                        activity_type=activity_type, details=details,
                        service=self._proto,
                        caller_log_id=self._caller_log_id))
                    db.session.commit()

        session = _FakeSession(
            user={'id': self.sender_id, 'username': 'pmsendercase', 'access_level': 100},
            responses=['stingray', 'Case test subject'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_anedit',
                  new_callable=AsyncMock, return_value='Case test body'):
            asyncio.run(ui.send_pm())

        self.assertNotIn("No such user", session.transcript())
        self.assertIn('[OK]', session.transcript())

        with self.app.app_context():
            pm = PrivateMessage.query.filter_by(
                sender_id=self.sender_id, subject='Case test subject').first()
            self.assertIsNotNone(pm)
            self.assertEqual(pm.recipient_id, self.stingray_id)

    # ---- web PM compose (web/pm.py compose()) ----

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_web_pm_compose_finds_recipient_regardless_of_case(self):
        from anetbbs.models import PrivateMessage
        client = self._client_as(self.sender_id)
        resp = client.post('/messages/compose', data={
            'recipient': 'stingray',
            'subject': 'Web case test subject',
            'content': 'Web case test body',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'User not found', resp.data)

        with self.app.app_context():
            pm = PrivateMessage.query.filter_by(
                sender_id=self.sender_id, subject='Web case test subject').first()
            self.assertIsNotNone(pm)
            self.assertEqual(pm.recipient_id, self.stingray_id)

    # ---- /profile/<username> (web/profile.py view_user()) ----

    def test_profile_view_finds_user_regardless_of_case(self):
        client = self.app.test_client()
        resp = client.get('/profile/stingray')
        self.assertEqual(resp.status_code, 200)

    # ---- /gemini/<username> and /gemini/<username>.gmi ----

    def test_gemini_view_finds_capsule_regardless_of_case(self):
        client = self.app.test_client()
        resp = client.get('/gemini/stingray')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'UNIQUE_CAPSULE_MARKER', resp.data)

    def test_gemini_raw_gemtext_finds_capsule_regardless_of_case(self):
        client = self.app.test_client()
        resp = client.get('/gemini/stingray.gmi')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'UNIQUE_CAPSULE_MARKER', resp.data)

    # ---- finger protocol (core/finger_server.py _user_info()) ----

    def test_finger_user_info_finds_user_regardless_of_case(self):
        from anetbbs.core.finger_server import _user_info
        with self.app.app_context():
            info = _user_info('stingray')
        self.assertIsNotNone(info)
        self.assertEqual(info['username'], 'StingRay')

    # ---- /forgot (web/auth.py forgot_password()) ----

    def test_forgot_password_finds_account_regardless_of_case(self):
        from anetbbs.models import PasswordResetToken
        client = self.app.test_client()
        resp = client.post('/auth/forgot', data={'identifier': 'stingray'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            token = PasswordResetToken.query.filter_by(
                user_id=self.stingray_id).first()
            self.assertIsNotNone(token)

    # ---- admin add/edit user uniqueness (web/admin.py) ----

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='usercaseadmin').first()
            if not admin:
                admin = User(username='usercaseadmin', is_admin=True,
                             access_level=255,
                             email='usercaseadmin@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_admin_add_user_rejects_case_variant_duplicate(self):
        client = self._admin_client()
        resp = client.post('/admin/users/add', data={
            'username': 'stingray',
            'email': 'another-stingray@example.com',
            'password': 'somepassword',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'already taken', resp.data)

        from anetbbs.models import User
        with self.app.app_context():
            dupes = User.query.filter(
                User.username.in_(['stingray', 'StingRay'])).count()
            self.assertEqual(dupes, 1)

    def test_admin_edit_user_rejects_case_variant_duplicate(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            other = User.query.filter_by(username='usercaseother').first()
            if not other:
                other = User(username='usercaseother', is_admin=False,
                             access_level=10,
                             email='usercaseother@example.com')
                other.set_password('x')
                db.session.add(other)
                db.session.commit()
            other_id = other.id

        client = self._admin_client()
        resp = client.post(f'/admin/users/{other_id}/edit', data={
            'username': 'stingray',
            'email': 'usercaseother@example.com',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'already taken', resp.data)

        with self.app.app_context():
            unchanged = User.query.get(other_id)
            self.assertEqual(unchanged.username, 'usercaseother')


if __name__ == '__main__':
    unittest.main()
