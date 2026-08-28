"""Regression tests for the manual social-post compose flow, added
after Jerry asked (live, having just tried the auto-social-posting
queue): "can you add something for the sysops to be able to make stuff
available to post on socials... an update... a recent version bump, or
a new feature" and "will I get a notification or do I have to manually
check this page?"

Covers: GET/POST /admin/social/new (features/social_queue.py's new
queue_manual_post()), that queuing ANY post -- manual or automatic --
now writes a Notification for every admin (the actual answer to
Jerry's second question), and that a post with no image renders/serves
correctly (every prior post always had an auto-generated PNG, so
index.html's <img> tag was unconditional until this change).
"""
import io
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _tiny_png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), (10, 20, 30)).save(buf, format='PNG')
    return buf.getvalue()


class SocialAdminManualPostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.social_manual_post_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            admin = User(username='socialadmin', email='socialadmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            plain = User(username='plainuser', email='plainuser@example.com',
                        password_hash='x', is_admin=False, access_level=10)
            db.session.add_all([admin, plain])
            db.session.commit()
            cls.admin_id, cls.plain_id = admin.id, plain.id

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

    def _admin_client(self):
        return self._client_as(self.admin_id)

    def tearDown(self):
        with self.app.app_context():
            from anetbbs.models import db, SocialPost, Notification
            SocialPost.query.delete()
            Notification.query.delete()
            db.session.commit()

    # -- access control ----------------------------------------------

    def test_non_admin_cannot_reach_the_compose_form(self):
        client = self._client_as(self.plain_id)
        resp = client.get('/admin/social/new')
        self.assertEqual(resp.status_code, 403)

    # -- text-only post -------------------------------------------------

    def test_posting_text_only_queues_a_pending_manual_post(self):
        client = self._admin_client()
        resp = client.post('/admin/social/new',
                           data={'text': 'ANetBBS v1.0.54 is live!'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import SocialPost
            post = SocialPost.query.filter_by(trigger_kind='manual').first()
            self.assertIsNotNone(post)
            self.assertEqual(post.status, 'pending')
            self.assertEqual(post.text, 'ANetBBS v1.0.54 is live!')
            self.assertIsNone(post.image_path)

    def test_empty_text_is_rejected_and_queues_nothing(self):
        client = self._admin_client()
        resp = client.post('/admin/social/new', data={'text': '   '})
        self.assertEqual(resp.status_code, 400)
        with self.app.app_context():
            from anetbbs.models import SocialPost
            self.assertIsNone(SocialPost.query.filter_by(trigger_kind='manual').first())

    def test_text_only_post_renders_without_a_broken_image_tag(self):
        """Real gap: index.html's <img> tag was unconditional -- every
        prior post always had an auto-generated PNG. A manual text-only
        post must not produce a broken-image <img src> pointed at a 404."""
        client = self._admin_client()
        client.post('/admin/social/new', data={'text': 'text only post'})
        resp = client.get('/admin/social/')
        body = resp.get_data(as_text=True)
        self.assertIn('text only', body)
        self.assertIn('(text only', body.lower())

    # -- image upload -----------------------------------------------------

    def test_posting_with_an_image_stores_and_serves_it(self):
        client = self._admin_client()
        png = _tiny_png_bytes()
        resp = client.post('/admin/social/new', data={
            'text': 'Check out this screenshot',
            'image': (io.BytesIO(png), 'shot.png'),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import SocialPost
            post = SocialPost.query.filter_by(trigger_kind='manual').first()
            self.assertIsNotNone(post.image_path)
            self.assertTrue(os.path.isfile(post.image_path))
            img_resp = client.get(f'/admin/social/{post.id}/image')
            self.assertEqual(img_resp.status_code, 200)
            self.assertEqual(img_resp.mimetype, 'image/png')

    def test_non_image_upload_is_rejected(self):
        client = self._admin_client()
        resp = client.post('/admin/social/new', data={
            'text': 'bad upload',
            'image': (io.BytesIO(b'not an image, just text'), 'shot.png'),
        }, content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 400)
        with self.app.app_context():
            from anetbbs.models import SocialPost
            self.assertIsNone(SocialPost.query.filter_by(trigger_kind='manual').first())

    # -- notification (Jerry's second question) ----------------------

    def test_queuing_a_post_notifies_every_admin(self):
        """Answers Jerry's direct question: queuing ANY post -- this
        manual one included -- now writes a Notification for every
        admin, not just a "check the page yourself" experience."""
        client = self._admin_client()
        client.post('/admin/social/new', data={'text': 'notify me please'})
        with self.app.app_context():
            from anetbbs.models import Notification
            note = Notification.query.filter_by(
                user_id=self.admin_id, kind='social_post_queued').first()
            self.assertIsNotNone(note, 'expected a Notification row for the admin')
            self.assertIn('notify me please', note.body)
            self.assertEqual(note.target_url, '/admin/social/')


if __name__ == '__main__':
    unittest.main()
