"""Regression tests for a real path-traversal bug found in a security
audit: anetbbs/web/gallery.py's image() route and
anetbbs/web/gallery_admin.py's delete_file() route both confined a
requested path to a gallery's configured root directory via

    str(safe_path).startswith(str(root.resolve()))

which is the classic missing-separator bypass -- a resolved path under
a SIBLING directory whose name happens to start with the root
directory's own name (e.g. root ".../nasa", sibling ".../nasa-secret")
passes the check, since "nasa-secret" is a string-prefix match for
"nasa" even though it isn't a path-prefix match. anetbbs/web/
file_areas.py's equivalent download() route already guards against
this correctly (`+ os.sep`), confirming this was a regression/
inconsistency rather than an accepted pattern.
"""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

_TINY_PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010802000000907753'
    'de0000000c4944415478da6360000002000155caf9250000000049454e44ae42'
    '6082')


class GalleryPathTraversalFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.gallery_traversal_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            u = User(username='gallery_trav_user', email='gtu@example.com',
                     password_hash='x', is_admin=False, access_level=10)
            admin = User(username='gallery_trav_admin', email='gta@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add_all([u, admin])
            db.session.commit()
            cls.user_id = u.id
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self.parent = tempfile.mkdtemp()
        # Sibling directories sharing a name prefix -- the shape the
        # bug needs: "nasa-secret" starts with "nasa" as a raw string.
        self.root = os.path.join(self.parent, 'nasa')
        self.sibling = os.path.join(self.parent, 'nasa-secret')
        os.makedirs(self.root)
        os.makedirs(self.sibling)
        Path(self.sibling, 'flag.txt').write_bytes(b'SECRET DATA OUTSIDE GALLERY ROOT')
        # Regular (non-archive) files under a gallery root are also
        # served via send_from_directory(), which has its OWN,
        # independent traversal guard (Werkzeug's safe_join) --
        # unaffected by this bug either way. The .zip branch is
        # different: it reads the resolved path directly
        # (_read_first_image_from_zip()), so the startswith() check
        # being fixed here is the ONLY guard on that path. Use a zip
        # so this test actually exercises the vulnerable branch.
        with zipfile.ZipFile(Path(self.sibling, 'secret.zip'), 'w') as zf:
            zf.writestr('flag.jpg', _TINY_PNG)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.parent, ignore_errors=True)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _fake_gallery(self):
        return {'slug': 'nasa', 'label': 'NASA', 'path': self.root, 'is_active': True}

    def test_image_route_rejects_sibling_directory_traversal(self):
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            resp = self._client_as(self.user_id).get(
                '/gallery/nasa/img/../nasa-secret/secret.zip')
        self.assertNotEqual(resp.status_code, 200)
        self.assertNotEqual(resp.data, _TINY_PNG)

    def test_image_route_still_serves_a_real_file_inside_root(self):
        Path(self.root, 'real.png').write_bytes(_TINY_PNG)
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            resp = self._client_as(self.user_id).get('/gallery/nasa/img/real.png')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, _TINY_PNG)

    def test_delete_file_admin_route_rejects_sibling_directory_traversal(self):
        with patch('anetbbs.web.gallery_admin._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            self._client_as(self.admin_id).post(
                '/admin/galleries/nasa/files/../nasa-secret/flag.txt/delete')
        # Whatever status code came back, the file outside the
        # configured root must still exist afterward.
        self.assertTrue(Path(self.sibling, 'flag.txt').is_file())


if __name__ == '__main__':
    unittest.main()
