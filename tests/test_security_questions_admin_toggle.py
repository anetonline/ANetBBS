"""Regression tests for a real feature request (2026-09-25): the sysop
had repeated user complaints that the mandatory 3-question security
registration step felt too personal, and wanted (1) the question text
itself to be admin-editable rather than a hardcoded list, (2) an
on/off switch for the whole step now that SMTP-based recovery
(already configured) is a real alternative.

Covers: SecurityQuestion/PasswordRecoverySettings models, the web admin
CRUD route, registration skipping the fields when disabled, and
/forgot skipping the security-question verify step (landing on a
neutral "check your email" page instead, preserving the existing
anti-enumeration posture -- same response regardless of whether the
identifier matches a real account) when disabled.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SecurityQuestionsAdminToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.security_questions_admin_toggle_test.db')
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
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        from anetbbs.models import PasswordRecoverySettings, db
        with self.app.app_context():
            # Reset to the default (on) state before every test -- some
            # tests flip it off.
            PasswordRecoverySettings.get().security_questions_enabled = True
            db.session.commit()

    def _make_admin(self, username):
        from anetbbs.models import User, db
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if u is None:
                u = User(username=username, email=f'{username}@example.com',
                         is_admin=True, is_verified=True)
                u.set_password('adminpass123')
                db.session.add(u)
                db.session.commit()
            return u.id

    def _login(self, client, username, password):
        return client.post('/auth/login', data={
            'username': username, 'password': password}, follow_redirects=True)

    # -- Model / seed --------------------------------------------------

    def test_default_seed_has_ten_active_questions(self):
        from anetbbs.models import SecurityQuestion
        with self.app.app_context():
            active = SecurityQuestion.query.filter_by(is_active=True).count()
            self.assertEqual(active, 10)

    def test_settings_singleton_defaults_to_enabled(self):
        from anetbbs.models import PasswordRecoverySettings
        with self.app.app_context():
            self.assertTrue(PasswordRecoverySettings.get().security_questions_enabled)

    # -- Admin CRUD ------------------------------------------------------

    def test_admin_can_add_edit_toggle_and_delete_a_question(self):
        self._make_admin('sq_admin_one')
        client = self.app.test_client()
        self._login(client, 'sq_admin_one', 'adminpass123')

        resp = client.post('/admin/security-questions',
                           data={'action': 'add', 'text': 'What is your favorite color?',
                                 'sort_order': '99'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import SecurityQuestion
            q = SecurityQuestion.query.filter_by(
                text='What is your favorite color?').first()
            self.assertIsNotNone(q)
            qid = q.id

        client.post('/admin/security-questions',
                    data={'action': 'edit', 'qid': qid,
                          'text': 'What is your favorite hue?'})
        with self.app.app_context():
            from anetbbs.models import SecurityQuestion
            q = SecurityQuestion.query.get(qid)
            self.assertEqual(q.text, 'What is your favorite hue?')

        client.post('/admin/security-questions', data={'action': 'toggle', 'qid': qid})
        with self.app.app_context():
            from anetbbs.models import SecurityQuestion
            self.assertFalse(SecurityQuestion.query.get(qid).is_active)

        client.post('/admin/security-questions', data={'action': 'delete', 'qid': qid})
        with self.app.app_context():
            from anetbbs.models import SecurityQuestion
            self.assertIsNone(SecurityQuestion.query.get(qid))

    def test_admin_can_toggle_the_feature_on_and_off(self):
        self._make_admin('sq_admin_two')
        client = self.app.test_client()
        self._login(client, 'sq_admin_two', 'adminpass123')

        client.post('/admin/security-questions', data={'action': 'toggle_enabled'})
        with self.app.app_context():
            from anetbbs.models import PasswordRecoverySettings
            self.assertFalse(PasswordRecoverySettings.get().security_questions_enabled)

        client.post('/admin/security-questions', data={'action': 'toggle_enabled'})
        with self.app.app_context():
            from anetbbs.models import PasswordRecoverySettings
            self.assertTrue(PasswordRecoverySettings.get().security_questions_enabled)

    def test_non_admin_cannot_reach_the_admin_route(self):
        from anetbbs.models import User, db
        with self.app.app_context():
            if not User.query.filter_by(username='sq_plain_user').first():
                u = User(username='sq_plain_user', email='plain@example.com',
                         is_verified=True)
                u.set_password('plainpass123')
                db.session.add(u); db.session.commit()

        client = self.app.test_client()
        self._login(client, 'sq_plain_user', 'plainpass123')
        resp = client.get('/admin/security-questions', follow_redirects=False)
        self.assertNotEqual(resp.status_code, 200)

    # -- Registration behavior -------------------------------------------

    def test_registration_form_hides_and_does_not_require_questions_when_disabled(self):
        from anetbbs.models import PasswordRecoverySettings, db
        with self.app.app_context():
            PasswordRecoverySettings.get().security_questions_enabled = False
            db.session.commit()

        client = self.app.test_client()
        resp = client.post('/auth/register', data={
            'username': 'nosqreguser', 'email': 'nosqreg@example.com',
            'password': 'password123', 'password2': 'password123',
            # Deliberately no question_*/answer_* fields at all.
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.filter_by(username='nosqreguser').first()
            self.assertIsNotNone(u, 'registration should have succeeded '
                                 'without any security answers')
            self.assertEqual(len(u.security_answers), 0)

    def test_registration_still_requires_questions_when_enabled(self):
        client = self.app.test_client()
        resp = client.post('/auth/register', data={
            'username': 'withsqreguser', 'email': 'withsqreg@example.com',
            'password': 'password123', 'password2': 'password123',
            # No question_*/answer_* fields -- should fail validation.
        })
        self.assertEqual(resp.status_code, 200)   # re-renders the form
        with self.app.app_context():
            from anetbbs.models import User
            self.assertIsNone(User.query.filter_by(username='withsqreguser').first())

    # -- /forgot behavior --------------------------------------------------

    def test_forgot_skips_verify_step_and_shows_neutral_page_when_disabled(self):
        from anetbbs.models import PasswordRecoverySettings, User, db
        with self.app.app_context():
            PasswordRecoverySettings.get().security_questions_enabled = False
            db.session.commit()
            if not User.query.filter_by(username='forgot_nosq_user').first():
                u = User(username='forgot_nosq_user',
                         email='forgot_nosq@example.com', is_verified=True)
                u.set_password('whatever123')
                db.session.add(u); db.session.commit()

        client = self.app.test_client()
        real_resp = client.post('/auth/forgot',
                                data={'identifier': 'forgot_nosq_user'},
                                follow_redirects=True)
        fake_resp = client.post('/auth/forgot',
                                data={'identifier': 'no_such_user_at_all'},
                                follow_redirects=True)

        self.assertEqual(real_resp.status_code, 200)
        self.assertEqual(fake_resp.status_code, 200)
        # Never redirected into the security-question verify flow.
        self.assertNotIn(b'answer', real_resp.data.lower()
                         if b'Your Answer' in real_resp.data else b'')
        self.assertEqual(real_resp.request.path, fake_resp.request.path,
                         'a real vs. nonexistent account must land on the '
                         'exact same page -- any difference is an '
                         'enumeration oracle')

    def test_verify_route_redirects_away_when_disabled(self):
        from anetbbs.models import PasswordRecoverySettings, db
        with self.app.app_context():
            PasswordRecoverySettings.get().security_questions_enabled = False
            db.session.commit()

        client = self.app.test_client()
        resp = client.get('/auth/forgot/verify', follow_redirects=False)
        self.assertIn(resp.status_code, (301, 302, 303, 307, 308))


if __name__ == '__main__':
    unittest.main()
