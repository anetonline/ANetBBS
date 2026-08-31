"""Regression test for a real Low-severity/cosmetic finding from a
security/performance audit (2026-08-31): web/wiki.py's rename() route
had a classic check-then-write TOCTOU race -- it queried whether
new_slug was already taken, and only proceeded to overwrite page.slug
if not. Two concurrent renames to the SAME new_slug (an admin double-
clicking, or two admins renaming different pages to the same target)
can both pass that check before either commits, since WikiPage.slug's
own UniqueConstraint (see models.py) only prevents the duplicate row
at commit time -- the loser's commit() used to raise an unhandled
IntegrityError (a 500) instead of the same friendly "a page already
exists at /wiki/X" message a sequential collision gets.

Admin-only and needs precise double-submission timing to hit for
real, so this is deterministic (forces the exact interleaving rather
than racing real threads) using the same pattern already established
in test_polls_vote_race.py for an identical bug shape: patch
WikiPage.query for exactly the first filter_by(...).first() call in
the request (simulating the stale "not taken yet" read), letting every
subsequent call hit the real DB.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WikiRenameRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.wiki_rename_race_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        cfg_mod.TestingConfig.WTF_CSRF_ENABLED = False

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='wikirenameadmin', email='wra@example.com',
                        is_active=True, is_admin=True)
            admin.set_password('wikirenameadminpass123')
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_toctou_rename_collision_never_500s(self):
        from anetbbs.models import db, WikiPage

        with self.app.app_context():
            # The "concurrent winner" -- a page that already, genuinely
            # occupies the target slug by the time our (patched, stale)
            # availability check runs.
            winner = WikiPage(slug='taken-slug', title='Winner Page',
                              body='already here', is_locked=False)
            loser = WikiPage(slug='original-slug', title='Loser Page',
                             body='trying to rename', is_locked=False)
            db.session.add_all([winner, loser])
            db.session.commit()

        call_count = {'n': 0}

        class _StaleFirstCallQuery:
            def filter_by(self, **kwargs):
                call_count['n'] += 1
                # Call #1 is rename()'s own "find the page being
                # renamed" lookup (needs the real row, .first_or_404()
                # included) -- only call #2, the "is new_slug already
                # taken?" availability check, is the one to force stale.
                if call_count['n'] == 2:
                    class _StaleResult:
                        def first(self):
                            return None
                    return _StaleResult()
                return db.session.query(WikiPage).filter_by(**kwargs)

        client = self._client_as(self.admin_id)
        with self.app.app_context(), \
             patch.object(WikiPage, 'query', _StaleFirstCallQuery()):
            resp = client.post('/wiki/original-slug/rename',
                              data={'new_slug': 'taken-slug'},
                              follow_redirects=False)

        self.assertLess(
            resp.status_code, 500,
            f'a forced check-then-write rename race must not 500 -- got '
            f'{resp.status_code}. The existence check went stale (forced '
            'None) while a real page already held that slug, so the '
            "commit collided with the DB UniqueConstraint and the "
            "resulting IntegrityError wasn't caught")

        with self.app.app_context():
            self.assertIsNotNone(
                WikiPage.query.filter_by(slug='original-slug').first(),
                'the losing rename must leave the original page at its '
                'original slug, not partially renamed or lost')
            self.assertEqual(
                WikiPage.query.filter_by(slug='taken-slug').count(), 1,
                "exactly one page must hold 'taken-slug' -- the DB "
                'UniqueConstraint guarantees this regardless of the fix, '
                'so this just confirms no crash left things inconsistent')


if __name__ == '__main__':
    unittest.main()
