"""Regression test for a real orphaned-file bug found in a security/
performance audit: skip() and approve() never deleted the rendered PNG
a queued SocialPost carries, even once the post reached a terminal
state and would never be approve()'d again. Confirmed live -- 25
leftover PNGs were found in data/social_posts/ with no matching
pending row before this fix existed.
"""
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _tiny_png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), (10, 20, 30)).save(buf, format='PNG')
    return buf.getvalue()


class SocialAdminImageCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.social_image_cleanup_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['BLUESKY_HANDLE'] = 'test.bsky.social'
        cls.app.config['BLUESKY_APP_PASSWORD'] = 'x'
        with cls.app.app_context():
            db.create_all()
            admin = User(username='socialcleanupadmin', email='socialcleanupadmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
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

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def tearDown(self):
        with self.app.app_context():
            from anetbbs.models import db, SocialPost
            for post in SocialPost.query.all():
                if post.image_path and os.path.isfile(post.image_path):
                    os.remove(post.image_path)
            SocialPost.query.delete()
            db.session.commit()

    def _queue_image_post(self, client, text):
        png = _tiny_png_bytes()
        client.post('/admin/social/new', data={
            'text': text,
            'image': (io.BytesIO(png), 'shot.png'),
        }, content_type='multipart/form-data')
        with self.app.app_context():
            from anetbbs.models import SocialPost
            post = SocialPost.query.filter_by(text=text).first()
            return post.id, post.image_path

    def test_skip_removes_the_image_file(self):
        client = self._admin_client()
        post_id, image_path = self._queue_image_post(client, 'skip me please')
        self.assertTrue(os.path.isfile(image_path))

        resp = client.post(f'/admin/social/{post_id}/skip', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.isfile(image_path))

    def test_approve_removes_the_image_file_on_success(self):
        client = self._admin_client()
        post_id, image_path = self._queue_image_post(client, 'approve me please')
        self.assertTrue(os.path.isfile(image_path))

        with patch('anetbbs.features.social_bluesky.post', return_value=(True, 'https://bsky/post/1')):
            resp = client.post(f'/admin/social/{post_id}/approve', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.isfile(image_path))
        with self.app.app_context():
            from anetbbs.models import SocialPost
            self.assertEqual(SocialPost.query.get(post_id).status, 'posted')

    def test_approve_removes_the_image_file_even_on_failure(self):
        """The file is only ever useful while the post is pending -- a
        failed post can't be re-approve()'d either (the route 400s
        unless status == 'pending'), so the image is dead weight in
        the failure case too, not just on success."""
        client = self._admin_client()
        post_id, image_path = self._queue_image_post(client, 'this one will fail')
        self.assertTrue(os.path.isfile(image_path))

        with patch('anetbbs.features.social_bluesky.post', return_value=(False, 'network error')):
            resp = client.post(f'/admin/social/{post_id}/approve', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.isfile(image_path))
        with self.app.app_context():
            from anetbbs.models import SocialPost
            self.assertEqual(SocialPost.query.get(post_id).status, 'failed')
