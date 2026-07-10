"""Regression tests for anetbbs/web/main.py:search() -- two changes:

1. A confirmed pre-existing leak: /search returned sysop-only boards,
   their posts, and access-gated echomail to ANY user (including
   logged-out), because neither the boards nor echomail result branch
   ever checked min_access_level / is_sysop_only / is_admin. This suite
   proves gated content no longer surfaces in search for a user who
   couldn't otherwise see it.
2. A new 'files' result category, scoped to the DB-backed FileUpload
   gallery (files.py) -- the FTN-style file-area browser has no per-file
   DB rows to search cheaply, see file_areas.py's module docstring.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class SearchAccessLeakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.search_leak_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self, username, access_level=10, is_admin=False):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=is_admin,
                         access_level=access_level,
                         email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def test_sysop_only_board_hidden_from_search_for_regular_user(self):
        from anetbbs.models import db, Board, Post, User

        with self.app.app_context():
            board = Board(name='SysopSearchLeakBoard', is_active=True,
                          min_access_level=100)
            db.session.add(board)
            db.session.flush()
            author = User.query.filter_by(username='searchleaktest_author').first()
            if not author:
                author = User(username='searchleaktest_author', access_level=10,
                              email='searchleaktest_author@example.com')
                author.set_password('x')
                db.session.add(author)
                db.session.flush()
            post = Post(board_id=board.id, author_id=author.id,
                       subject='UniqueLeakTestSubjectXYZ',
                       content='secret gated content')
            db.session.add(post)
            db.session.commit()

        # Search by subject (q gets echoed into the search box's value=
        # attribute and the "Results for" heading regardless of whether
        # anything matched, so assert against the post's *content* field
        # instead -- that only ever renders inside an actual result row).
        low_client = self._client('searchleaktest_low', access_level=10)
        resp = low_client.get('/search?q=UniqueLeakTestSubjectXYZ')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'secret gated content', resp.data,
                         'low-level user should not see a gated board\'s post in search')

        admin_client = self._client('searchleaktest_admin', is_admin=True,
                                    access_level=10)
        resp2 = admin_client.get('/search?q=UniqueLeakTestSubjectXYZ')
        self.assertIn(b'secret gated content', resp2.data,
                     'admin should still see it (bypass_admin=True)')

    def test_gated_echo_area_message_hidden_from_search(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage

        with self.app.app_context():
            net = EchomailNetwork(name='SearchLeakNet', network_type='binkp')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='SEARCHLEAK', name='Search Leak',
                            is_active=True, is_sysop_only=True, min_access_level=0)
            db.session.add(area)
            db.session.flush()
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id,
                from_name='Someone', to_name='All',
                subject='UniqueEchoLeakTestSubjectABC',
                body='gated echomail body', direction='inbound')
            db.session.add(msg)
            db.session.commit()

        low_client = self._client('searchleaktest_echo_low', access_level=10)
        resp = low_client.get('/search?q=UniqueEchoLeakTestSubjectABC')
        # Same reasoning as the board test above: check the body text
        # (only ever rendered inside a matched result row), not the
        # subject (also echoed into the search box regardless of match).
        self.assertNotIn(b'gated echomail body', resp.data,
                         'sysop-only echo area message should not leak into search')


class SearchFileCategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.search_files_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self, username='searchfilestest', access_level=10):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, access_level=access_level,
                         email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def test_top_level_file_found_by_filename(self):
        from anetbbs.models import db, FileUpload, User

        with self.app.app_context():
            uploader = User.query.filter_by(username='searchfilestest').first()
            if not uploader:
                uploader = User(username='searchfilestest', access_level=10,
                                email='searchfilestest@example.com')
                uploader.set_password('x')
                db.session.add(uploader)
                db.session.flush()
            fu = FileUpload(
                uploader_id=uploader.id, filename='abc123.zip',
                original_filename='UniqueFileSearchTargetXYZ.zip',
                file_path='/tmp/nonexistent', file_size=100,
                is_public=True)
            db.session.add(fu)
            db.session.commit()

        resp = self._client().get('/search?q=UniqueFileSearchTargetXYZ')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'UniqueFileSearchTargetXYZ', resp.data)

    def test_gated_area_file_hidden_from_low_level_user(self):
        from anetbbs.models import db, FileUpload, FileArea, User

        with self.app.app_context():
            uploader = User.query.filter_by(username='searchfilestest').first()
            if not uploader:
                uploader = User(username='searchfilestest', access_level=10,
                                email='searchfilestest@example.com')
                uploader.set_password('x')
                db.session.add(uploader)
                db.session.flush()
            area = FileArea(tag='SEARCHGATED', name='Search Gated',
                            storage_path='/tmp/x', is_active=True,
                            min_access_level=100)
            db.session.add(area)
            db.session.flush()
            fu = FileUpload(
                uploader_id=uploader.id, filename='gated123.zip',
                original_filename='UniqueGatedFileSearchTargetDEF.zip',
                description='gated file description marker',
                file_path='/tmp/nonexistent', file_size=100,
                is_public=True, file_area_id=area.id)
            db.session.add(fu)
            db.session.commit()

        low_client = self._client('searchfilestest_low', access_level=10)
        resp = low_client.get('/search?q=UniqueGatedFileSearchTargetDEF')
        # q (the filename) gets echoed into the search box regardless of
        # match -- assert against the description text instead, which
        # only ever renders inside an actual matched result row.
        self.assertNotIn(b'gated file description marker', resp.data)


if __name__ == '__main__':
    unittest.main()
