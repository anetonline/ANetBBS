"""Regression test: security/performance audit finding (2026-09-10).

register()'s auto-login path (web/auth.py) sends a "welcome" PM from the
sysop to every brand-new account that logs straight into a session (no
NUV/email-verification gate). It built the PrivateMessage with a
``content=content`` keyword argument, but the PrivateMessage model has no
``content`` column -- only ``body`` (see models.py's PrivateMessage class).
Instantiating with an unknown kwarg raises a TypeError, which the
surrounding ``except Exception: db.session.rollback()`` silently
swallowed -- the welcome PM was never sent, for every single
auto-logged-in registration, with no visible error anywhere.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class RegistrationWelcomePmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.registration_welcome_pm_test.db')
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

    def test_new_user_receives_a_welcome_pm_from_the_sysop(self):
        from anetbbs.models import SECURITY_QUESTIONS, db, User

        with self.app.app_context():
            # Seed a sysop account so register()'s welcome-PM branch has
            # someone to send from (it no-ops with no admin on file).
            sysop = User.query.filter_by(is_admin=True).order_by(User.id).first()
            if sysop is None:
                sysop = User(username='sysop', email='sysop@example.com', is_admin=True)
                sysop.set_password('sysoppassword123')
                db.session.add(sysop)
                db.session.commit()

        client = self.app.test_client()
        resp = client.post('/auth/register', data={
            'username': 'newbie', 'email': 'newbie@example.com',
            'password': 'password12345', 'password2': 'password12345',
            'question_1': SECURITY_QUESTIONS[0], 'answer_1': 'answer one',
            'question_2': SECURITY_QUESTIONS[1], 'answer_2': 'answer two',
            'question_3': SECURITY_QUESTIONS[2], 'answer_3': 'answer three',
        }, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303), resp.get_data(as_text=True))

        with self.app.app_context():
            from anetbbs.models import PrivateMessage
            newbie = User.query.filter_by(username='newbie').first()
            self.assertIsNotNone(newbie, 'registration itself did not succeed')

            pm = PrivateMessage.query.filter_by(recipient_id=newbie.id).first()
            self.assertIsNotNone(
                pm, 'a brand-new auto-logged-in account must receive the '
                    'sysop welcome PM (was silently failing on a bad '
                    "PrivateMessage(content=...) kwarg -- the model's real "
                    'field is `body`)')
            self.assertIn('Welcome', pm.subject)
            self.assertIn('newbie', pm.body)


if __name__ == '__main__':
    unittest.main()
