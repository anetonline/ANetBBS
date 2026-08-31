"""Regression test for a real High-severity finding from a security/
performance audit (2026-08-31): web/wiki.py's revert() route (which
writes a new WikiRevision and overwrites page.body/page.title, exactly
like a normal save) only checked page.is_locked -- it never enforced
the WIKI_MIN_POSTS/WIKI_MIN_DAYS edit gate edit() applies. A brand-new
account with zero posts, which edit() correctly blocks, could still
vandalize any unlocked page by POSTing straight to
/wiki/<slug>/revert/<rev_num> with any existing revision number,
discarding every later legitimate edit with no reputation gate
stopping them.

Fixed by extracting the gate into a shared _edit_gate_message() helper
both edit() and revert() now call, so the two routes can't drift out
of sync again.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WikiRevertEditGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.wiki_revert_gate_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        cfg_mod.TestingConfig.WTF_CSRF_ENABLED = False

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, WikiPage, WikiRevision, Board, Post
        from datetime import datetime, timedelta
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            veteran = User(username='wikirevertveteran',
                           email='wrv@example.com', is_active=True,
                           created_at=datetime.utcnow() - timedelta(days=365))
            veteran.set_password('veteranpassword123')
            newbie = User(username='wikirevertnewbie',
                          email='wrn@example.com', is_active=True,
                          created_at=datetime.utcnow())
            newbie.set_password('newbiepassword123')
            db.session.add_all([veteran, newbie])
            db.session.commit()
            cls.veteran_id = veteran.id
            cls.newbie_id = newbie.id

            # WIKI_MIN_POSTS defaults to 5 -- give the veteran enough
            # real posts to actually satisfy the gate (a genuinely
            # qualified account), rather than disabling the config.
            board = Board(name='Test Board')
            db.session.add(board)
            db.session.commit()
            for i in range(5):
                db.session.add(Post(board_id=board.id, author_id=veteran.id,
                                    subject=f'Post {i}', content='content'))
            db.session.commit()

            page = WikiPage(slug='test-revert-gate', title='Test Page',
                            body='Current legitimate content', is_locked=False)
            db.session.add(page)
            db.session.commit()
            rev1 = WikiRevision(page_id=page.id, rev_num=1, title='Test Page',
                                body='Original stub content')
            db.session.add(rev1)
            db.session.commit()
            cls.page_slug = page.slug
            cls.rev_num = rev1.rev_num

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

    def _current_body(self):
        from anetbbs.models import WikiPage
        with self.app.app_context():
            return WikiPage.query.filter_by(slug=self.page_slug).first().body

    def test_new_account_cannot_revert_a_page_it_could_not_edit(self):
        original_body = self._current_body()
        client = self._client_as(self.newbie_id)
        client.post(f'/wiki/{self.page_slug}/revert/{self.rev_num}',
                    follow_redirects=True)
        self.assertEqual(self._current_body(), original_body,
                         'a brand-new account (blocked from edit()) must not '
                         'be able to vandalize the page via revert() either')

    def test_veteran_account_can_still_revert(self):
        client = self._client_as(self.veteran_id)
        resp = client.post(f'/wiki/{self.page_slug}/revert/{self.rev_num}',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._current_body(), 'Original stub content',
                         'a qualified account must still be able to revert')


if __name__ == '__main__':
    unittest.main()
