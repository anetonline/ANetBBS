"""Regression test for a real finding from a security/performance audit
(2026-09-10): three separate call sites across web/auth.py and
web/admin.py built a PrivateMessage with a ``content=...`` keyword
argument. The PrivateMessage model (models.py) has no ``content``
column -- only ``body`` -- so SQLAlchemy's default declarative
constructor raises ``TypeError: 'content' is an invalid keyword
argument for PrivateMessage`` the instant the object is constructed.

Two of the three call sites (auth.py's post-registration welcome PM,
fixed separately, and admin.py's pending_user_action() NUV-approval
welcome PM here) wrapped the construction in a broad
``try/except Exception: db.session.rollback()``, so the TypeError was
silently swallowed -- the welcome PM was simply never sent, with
nothing in the logs to say why.

The third call site, admin.py's inactive_users() "PM selected users"
bulk action, has NO surrounding try/except at all -- every use of that
feature raised an unhandled 500 Internal Server Error instead of
sending anything.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class AdminPrivateMessageBodyKwargTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_pm_body_kwarg_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_admin(self, username):
        from anetbbs.models import db, User
        u = User(username=username, email=f'{username}@example.com',
                is_admin=True, is_active=True, is_verified=True)
        u.set_password('adminpmtestpass123')
        db.session.add(u)
        db.session.commit()
        return u.id

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_pending_user_approval_sends_a_working_welcome_pm(self):
        from datetime import datetime
        from anetbbs.models import db, User, PrivateMessage
        with self.app.app_context():
            admin_id = self._make_admin('pmapprover')
            pending = User(username='pmpendinguser',
                           email='pmpendinguser@example.com',
                           is_active=True, is_verified=False,
                           created_at=datetime.utcnow())
            pending.set_password('pendinguserpass123')
            db.session.add(pending)
            db.session.commit()
            pending_id = pending.id

        client = self._client_as(admin_id)
        resp = client.post(f'/admin/pending-users/{pending_id}/approve',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            u = User.query.filter_by(id=pending_id).first()
            self.assertTrue(u.is_verified, 'approval itself must still work')
            pm = PrivateMessage.query.filter_by(recipient_id=pending_id).first()
            self.assertIsNotNone(
                pm, 'approving a pending user must send the welcome PM '
                    '(was silently failing on a bad '
                    "PrivateMessage(content=...) kwarg -- the model's "
                    'real field is `body`)')
            self.assertIn('approved', pm.body.lower())

    def test_inactive_users_bulk_pm_does_not_500(self):
        from datetime import datetime, timedelta
        from anetbbs.models import db, User, PrivateMessage
        with self.app.app_context():
            admin_id = self._make_admin('pmbulkadmin')
            stale = User(username='pmstaleuser',
                        email='pmstaleuser@example.com',
                        is_active=True, is_verified=True,
                        last_login=datetime.utcnow() - timedelta(days=200))
            stale.set_password('staleuserpass123')
            db.session.add(stale)
            db.session.commit()
            stale_id = stale.id

        client = self._client_as(admin_id)
        resp = client.post('/admin/inactive-users', data={
            'action': 'pm',
            'user_id': [str(stale_id)],
            'subject': 'We miss you!',
            'body': 'Come back and visit the boards!',
        }, follow_redirects=True)
        self.assertEqual(
            resp.status_code, 200,
            'bulk-PM to inactive users must not 500 (was raising an '
            "unhandled TypeError from PrivateMessage(content=...) -- "
            'the model field is `body`, and this call site has no '
            'surrounding try/except at all)')

        with self.app.app_context():
            pm = PrivateMessage.query.filter_by(recipient_id=stale_id).first()
            self.assertIsNotNone(pm, 'the PM must actually be sent')
            self.assertEqual(pm.body, 'Come back and visit the boards!')


if __name__ == '__main__':
    unittest.main()
