"""Feature test for the shared random-tagline pool: a real sysop ask for
"most BBSes have" a couple-hundred-entry tagline library, opt-in per
message. Distinct from the existing User.tagline (a single fixed FTN-
style line auto-appended unconditionally to netmail/echomail) and
User.signature (rendered at read time, never stored). Covers the model,
the random-pick helper, and the admin CRUD page (mirrors MotdEntry's
established pattern).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class TaglineModelAndAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.taglines_test.db')
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

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='taglinetestadmin').first()
            if not admin:
                admin = User(username='taglinetestadmin', is_admin=True,
                            access_level=255,
                            email='taglinetestadmin@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_get_random_tagline_returns_none_when_pool_empty(self):
        from anetbbs.models import db, get_random_tagline, Tagline
        with self.app.app_context():
            Tagline.query.delete()
            db.session.commit()
            self.assertIsNone(get_random_tagline())

    def test_get_random_tagline_only_picks_active_entries(self):
        from anetbbs.models import db, Tagline, get_random_tagline
        with self.app.app_context():
            Tagline.query.delete()
            db.session.add(Tagline(text='ACTIVE_ONE', is_active=True))
            db.session.add(Tagline(text='DISABLED_ONE', is_active=False))
            db.session.commit()
            for _ in range(10):
                self.assertEqual(get_random_tagline(), 'ACTIVE_ONE')

    def test_format_tagline_append_uses_classic_signature_separator(self):
        from anetbbs.models import format_tagline_append
        result = format_tagline_append('A witty remark.')
        self.assertEqual(result, '\n\n-- \nA witty remark.\n')

    def test_get_active_taglines_excludes_disabled_and_sorts_by_text(self):
        """Backs the browsable picker (web listbox / terminal lightbar)
        added after Jerry asked to see and choose from the full list
        rather than getting a blind random pick."""
        from anetbbs.models import db, Tagline, get_active_taglines
        with self.app.app_context():
            Tagline.query.delete()
            db.session.add(Tagline(text='Zeta line', is_active=True))
            db.session.add(Tagline(text='Alpha line', is_active=True))
            db.session.add(Tagline(text='Disabled line', is_active=False))
            db.session.commit()

            result = [t.text for t in get_active_taglines()]
            self.assertEqual(result, ['Alpha line', 'Zeta line'],
                             'must exclude disabled entries and sort for a '
                             'stable, browsable listing')

    def test_admin_add_toggle_delete(self):
        from anetbbs.models import db, Tagline
        client = self._admin_client()

        resp = client.post('/admin/taglines', data={'action': 'add', 'text': 'Test Tagline 123'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            t = Tagline.query.filter_by(text='Test Tagline 123').first()
            self.assertIsNotNone(t)
            self.assertTrue(t.is_active)
            tagline_id = t.id

        client.post('/admin/taglines', data={'action': 'toggle', 'tagline_id': str(tagline_id)},
                   follow_redirects=True)
        with self.app.app_context():
            t = Tagline.query.get(tagline_id)
            self.assertFalse(t.is_active)

        client.post('/admin/taglines', data={'action': 'delete', 'tagline_id': str(tagline_id)},
                   follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(Tagline.query.get(tagline_id))

    def test_taglines_admin_page_is_linked_from_the_dashboard(self):
        """The /admin/taglines route existed and worked, but had no link
        anywhere in the admin UI -- a sysop had to already know the URL
        to find it. Reported live ("where did you say you can add/edit
        taglines in admin?"). Fixed by adding it to ADMIN_HUB_SECTIONS
        under Message System, next to MOTD Pool."""
        from anetbbs.web.admin import ADMIN_HUB_SECTIONS

        tools = ADMIN_HUB_SECTIONS['messages']['tools']
        endpoints = [t[0] for t in tools]
        self.assertIn('admin.taglines_admin', endpoints,
                      'Taglines must be reachable from the Message System '
                      'admin hub, not just by knowing the raw URL')

        client = self._admin_client()
        resp = client.get('/admin/hub/messages')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'/admin/taglines', resp.data)

    def test_seed_file_is_well_formed_and_has_a_couple_hundred_entries(self):
        """Sysop's ask was specifically for 'a couple hundred' taglines --
        confirm the bundled seed file actually delivers that, and that
        every non-comment/non-blank line fits the model's 200-char column."""
        seed_path = (Path(__file__).resolve().parents[1] /
                    'anetbbs' / 'data' / 'default_taglines.txt')
        self.assertTrue(seed_path.exists())
        lines = [l.strip() for l in seed_path.read_text(encoding='utf-8').splitlines()]
        entries = [l for l in lines if l and not l.startswith('#')]
        self.assertGreaterEqual(len(entries), 150,
                                'expected roughly a couple hundred taglines')
        for entry in entries:
            self.assertLessEqual(len(entry), 200,
                                 f'tagline too long for the model column: {entry!r}')


if __name__ == '__main__':
    unittest.main()
