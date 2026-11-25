"""PP-1 (audit H-5): personal pages are user-authored content served from
the BBS origin, so they must be sandboxed and only accepted while the
feature is on.

Before the fix, user .html/.svg were served inline under the site-wide
CSP (script-src 'unsafe-inline'), i.e. same-origin script; and upload()
/delete() worked even with PERSONAL_PAGES_ENABLED=false, with no quota.

These are header/behaviour unit tests. Browser-level isolation is verified
separately (headless Chromium matrix), and the plan treats that as the
done criterion.
"""
import io
import os
import tempfile
import unittest

import anetbbs.config as cfg_mod


class PersonalPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp = tempfile.mkdtemp(prefix='pp1-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                              DATA_DIR=os.path.join(cls._tmp, 'data'))
        with cls.app.app_context():
            db.create_all()
            u = User(username='mallory', email='m@x.test', is_verified=True, is_active=True)
            u.set_password('x-Passw0rd-x')
            db.session.add(u)
            db.session.commit()
            cls.uid = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_uri

    def setUp(self):
        self.app.config['PERSONAL_PAGES_ENABLED'] = True
        self.app.config.pop('PERSONAL_PAGES_MAX_FILES', None)
        self.app.config.pop('PERSONAL_PAGES_MAX_BYTES', None)
        import shutil
        shutil.rmtree(os.path.join(self.app.config['DATA_DIR'], 'personal_pages'),
                      ignore_errors=True)

    def _client(self):
        c = self.app.test_client()
        with self.app.app_context():
            from anetbbs.models import User
            ident = User.query.get(self.uid).get_id()
        with c.session_transaction() as s:
            s['_user_id'] = ident
            s['_fresh'] = True
        return c

    def _upload(self, c, name, data):
        return c.post('/_pages/upload', data={'file': (io.BytesIO(data), name)},
                      content_type='multipart/form-data')

    def _user_dir(self):
        return os.path.join(self.app.config['DATA_DIR'], 'personal_pages', 'users', 'mallory')

    def test_html_is_sandboxed(self):
        c = self._client()
        self._upload(c, 'x.html', b'<script>fetch("/admin/users")</script>')
        r = self.app.test_client().get('/~mallory/x.html')
        self.assertEqual(r.status_code, 200)
        csp = r.headers.get('Content-Security-Policy', '')
        self.assertTrue(csp.startswith('sandbox'), csp)
        self.assertNotIn('allow-same-origin', csp)
        self.assertNotIn('allow-top-navigation', csp)
        self.assertEqual(r.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(r.headers.get('X-Frame-Options'), 'DENY')

    def test_directory_index_is_sandboxed(self):
        c = self._client()
        self._upload(c, 'index.html', b'<h1>hi</h1>')
        r = self.app.test_client().get('/~mallory/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers['Content-Security-Policy'].startswith('sandbox'))

    def test_active_document_types_are_downloaded(self):
        c = self._client()
        self._upload(c, 'a.svg', b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>')
        self._upload(c, 's.js', b'alert(1)')
        for name in ('a.svg', 's.js'):
            with self.subTest(name=name):
                r = self.app.test_client().get(f'/~mallory/{name}')
                self.assertEqual(r.status_code, 200)
                self.assertTrue(r.headers.get('Content-Disposition', '').startswith('attachment'))
                self.assertTrue(r.headers['Content-Security-Policy'].startswith('sandbox'))

    def test_disabled_feature_refuses_upload_and_delete(self):
        c = self._client()
        self._upload(c, 'keep.html', b'k')
        self.app.config['PERSONAL_PAGES_ENABLED'] = False
        r = self._upload(c, 'new.html', b'x')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(os.path.exists(os.path.join(self._user_dir(), 'new.html')))
        r = c.post('/_pages/delete/keep.html')
        self.assertEqual(r.status_code, 404)
        self.assertTrue(os.path.exists(os.path.join(self._user_dir(), 'keep.html')))
        self.assertEqual(self.app.test_client().get('/~mallory/keep.html').status_code, 404)

    def test_quota_and_file_count(self):
        c = self._client()
        self.app.config['PERSONAL_PAGES_MAX_BYTES'] = 100
        self._upload(c, 'a.txt', b'x' * 80)
        self._upload(c, 'b.txt', b'x' * 40)
        self.assertFalse(os.path.exists(os.path.join(self._user_dir(), 'b.txt')))
        self.app.config['PERSONAL_PAGES_MAX_BYTES'] = 10 ** 6
        self.app.config['PERSONAL_PAGES_MAX_FILES'] = 1
        self._upload(c, 'c.txt', b'x')
        self.assertFalse(os.path.exists(os.path.join(self._user_dir(), 'c.txt')))
        # Replacing an existing file doesn't count as a new one.
        self._upload(c, 'a.txt', b'y')
        with open(os.path.join(self._user_dir(), 'a.txt'), 'rb') as f:
            self.assertEqual(f.read(), b'y')

    def test_disallowed_extension_refused(self):
        c = self._client()
        for name in ('x.php', 'x.exe', 'x.xhtml', 'x.xml', 'x'):
            with self.subTest(name=name):
                self._upload(c, name, b'z')
                self.assertFalse(os.path.exists(os.path.join(self._user_dir(), name)))

    def test_rest_of_site_keeps_default_csp(self):
        r = self.app.test_client().get('/about')
        self.assertFalse(r.headers.get('Content-Security-Policy', '').startswith('sandbox'))


if __name__ == '__main__':
    unittest.main()
