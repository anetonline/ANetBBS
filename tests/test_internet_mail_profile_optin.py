"""Regression tests for the Internet Mail self-service opt-in on
anetbbs/web/profile.py's edit() -- gating by MailConfig.enabled and
min_access_level (can_enable_mail()), the "already-enabled user can
always turn it back off" carve-out, and the mailbox-name uniqueness
check (a duplicate would otherwise silently steal another user's
mail).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _BaseProfileMailTestCase(unittest.TestCase):
    _db_name = '.internet_mail_profile_optin_test.db'

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / cls._db_name)
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

    def _set_mail_config(self, enabled=True, min_access_level=10, domain='bbs.a-net.fyi'):
        from anetbbs.models import db, MailConfig
        with self.app.app_context():
            cfg = MailConfig.get()
            cfg.enabled = enabled
            cfg.min_access_level = min_access_level
            cfg.domain = domain
            db.session.commit()

    def _make_user(self, username, access_level=10, email_enabled=False,
                   email_local_part=None):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username=username, email=f'{username}@example.com',
                    password_hash='x', access_level=access_level,
                    email_enabled=email_enabled, email_local_part=email_local_part)
            db.session.add(u)
            db.session.commit()
            return u.id

    def _login(self, client, user_id):
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

    def _post_profile(self, client, email_enabled, email_local_part='', user_id=None, **extra):
        # Must post the user's OWN existing (unique) email -- a shared
        # hardcoded fake email across different users/tests collides
        # with the form's real email-uniqueness validator, which is a
        # test-helper bug, not anything to do with Internet Mail.
        email = extra.pop('email', None)
        if email is None and user_id is not None:
            with self.app.app_context():
                from anetbbs.models import User
                email = User.query.get(user_id).email
        data = {
            'email': email or 'someone@example.com',
            'email_enabled': 'y' if email_enabled else '',
            'email_local_part': email_local_part,
        }
        data.update(extra)
        return client.post('/profile/edit', data=data, follow_redirects=False)


class MailOptInGatingTests(_BaseProfileMailTestCase):
    _db_name = '.internet_mail_profile_gate_test.db'

    def test_below_access_bar_cannot_newly_enable(self):
        self._set_mail_config(enabled=True, min_access_level=50)
        user_id = self._make_user('lowaccess', access_level=10)
        client = self.app.test_client()
        self._login(client, user_id)
        self._post_profile(client, email_enabled=True, email_local_part='lowaccess', user_id=user_id)

        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(user_id)
            self.assertFalse(u.email_enabled,
                             "a user below min_access_level must not be able to self-enable")

    def test_meeting_access_bar_can_enable(self):
        self._set_mail_config(enabled=True, min_access_level=10)
        user_id = self._make_user('okaccess', access_level=10)
        client = self.app.test_client()
        self._login(client, user_id)
        self._post_profile(client, email_enabled=True, email_local_part='okaccess', user_id=user_id)

        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(user_id)
            self.assertTrue(u.email_enabled)
            self.assertEqual(u.email_local_part, 'okaccess')

    def test_whole_feature_disabled_blocks_enabling_regardless_of_access(self):
        self._set_mail_config(enabled=False, min_access_level=0)
        user_id = self._make_user('featureoff', access_level=100)
        client = self.app.test_client()
        self._login(client, user_id)
        self._post_profile(client, email_enabled=True, email_local_part='featureoff', user_id=user_id)

        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(user_id)
            self.assertFalse(u.email_enabled)

    def test_already_enabled_user_can_still_turn_it_off_after_bar_raised(self):
        self._set_mail_config(enabled=True, min_access_level=10)
        user_id = self._make_user('wasEnabled', access_level=10,
                                  email_enabled=True, email_local_part='wasenabled')
        # Sysop raises the bar after the fact.
        self._set_mail_config(enabled=True, min_access_level=100)

        client = self.app.test_client()
        self._login(client, user_id)
        self._post_profile(client, email_enabled=False, email_local_part='wasenabled', user_id=user_id)

        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(user_id)
            self.assertFalse(u.email_enabled,
                             "an already-opted-in user must still be able to "
                             "turn it back off even after the bar is raised")


class MailboxNameUniquenessTests(_BaseProfileMailTestCase):
    _db_name = '.internet_mail_profile_uniqueness_test.db'

    def test_duplicate_explicit_local_part_is_rejected(self):
        self._set_mail_config(enabled=True, min_access_level=0)
        self._make_user('existingowner', access_level=10,
                        email_enabled=True, email_local_part='stingray')
        user_id = self._make_user('challenger', access_level=10)

        client = self.app.test_client()
        self._login(client, user_id)
        self._post_profile(client, email_enabled=True, email_local_part='stingray', user_id=user_id)

        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(user_id)
            self.assertFalse(u.email_enabled,
                             "a duplicate mailbox name must not silently steal "
                             "another user's mail")

    def test_duplicate_via_username_slug_fallback_is_also_rejected(self):
        """Blank local_part falls back to a slugified username -- the
        uniqueness check must catch a collision against that computed
        value too, not just an explicit email_local_part."""
        self._set_mail_config(enabled=True, min_access_level=0)
        self._make_user('stingray', access_level=10,
                        email_enabled=True, email_local_part=None)
        # slug_local_part() keeps dots/underscores/hyphens but strips
        # anything else (e.g. a space) -- 'Sting Ray' and 'stingray'
        # both slugify to 'stingray'.
        user_id = self._make_user('Sting Ray', access_level=10)

        client = self.app.test_client()
        self._login(client, user_id)
        self._post_profile(client, email_enabled=True, email_local_part='', user_id=user_id)

        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(user_id)
            self.assertFalse(u.email_enabled,
                             "'Sting Ray' slugifies to the same local part as "
                             "the existing 'stingray' user and must be rejected")

    def test_no_conflict_when_names_actually_differ(self):
        self._set_mail_config(enabled=True, min_access_level=0)
        self._make_user('existingowner2', access_level=10,
                        email_enabled=True, email_local_part='alpha')
        user_id = self._make_user('challenger2', access_level=10)

        client = self.app.test_client()
        self._login(client, user_id)
        self._post_profile(client, email_enabled=True, email_local_part='beta', user_id=user_id)

        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(user_id)
            self.assertTrue(u.email_enabled)
            self.assertEqual(u.email_local_part, 'beta')


if __name__ == '__main__':
    unittest.main()
