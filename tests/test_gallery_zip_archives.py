"""Regression tests for zip-archive image galleries (Jerry's request):
Digital Showroom-style TIC-fed photo feeds (e.g. a daily NASA-photo file
area) ship one image per .zip, not as loose image files. Galleries could
previously only browse a directory of raw image files -- this adds
transparent support for .zip entries in that same directory, extracting
the archive's one photo in memory (never written to disk) for both the
thumbnail grid and the full-size view.
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

# Minimal valid 1x1 transparent PNG.
_TINY_PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010802000000907753'
    'de0000000c4944415478da6360000002000155caf9250000000049454e44ae42'
    '6082')


class GalleryZipHelperTests(unittest.TestCase):
    """Pure-function tests -- no Flask app needed."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_zip(self, name, members):
        """members: dict of {archive-internal-name: bytes}"""
        path = os.path.join(self.tmpdir, name)
        with zipfile.ZipFile(path, 'w') as zf:
            for member_name, data in members.items():
                zf.writestr(member_name, data)
        return path

    def test_list_images_includes_zip_files(self):
        from anetbbs.web.gallery import _list_images
        Path(self.tmpdir, 'photo1.jpg').write_bytes(_TINY_PNG)
        self._make_zip('nasa-001.zip', {'apod.jpg': _TINY_PNG})
        result = _list_images(self.tmpdir)
        self.assertEqual(result, ['nasa-001.zip', 'photo1.jpg'])

    def test_first_image_in_zip_finds_the_image_member(self):
        from anetbbs.web.gallery import _first_image_in_zip
        zpath = self._make_zip('one.zip', {
            'FILE_ID.DIZ': b'a description, not an image',
            'earth.jpg': _TINY_PNG,
        })
        found = _first_image_in_zip(zpath)
        self.assertIsNotNone(found)
        member_name, mimetype = found
        self.assertEqual(member_name, 'earth.jpg')
        self.assertEqual(mimetype, 'image/jpeg')

    def test_first_image_in_zip_picks_first_by_name_when_multiple(self):
        from anetbbs.web.gallery import _first_image_in_zip
        zpath = self._make_zip('multi.zip', {
            'z-last.png': _TINY_PNG,
            'a-first.png': _TINY_PNG,
        })
        member_name, _ = _first_image_in_zip(zpath)
        self.assertEqual(member_name, 'a-first.png')

    def test_first_image_in_zip_skips_macosx_junk(self):
        from anetbbs.web.gallery import _first_image_in_zip
        zpath = self._make_zip('junk.zip', {
            '__MACOSX/._real.jpg': b'resource fork junk',
            'real.jpg': _TINY_PNG,
        })
        member_name, _ = _first_image_in_zip(zpath)
        self.assertEqual(member_name, 'real.jpg')

    def test_first_image_in_zip_returns_none_for_no_images(self):
        from anetbbs.web.gallery import _first_image_in_zip
        zpath = self._make_zip('noimages.zip', {'readme.txt': b'hello'})
        self.assertIsNone(_first_image_in_zip(zpath))

    def test_first_image_in_zip_returns_none_for_corrupt_zip(self):
        from anetbbs.web.gallery import _first_image_in_zip
        bad = os.path.join(self.tmpdir, 'corrupt.zip')
        Path(bad).write_bytes(b'not actually a zip file')
        self.assertIsNone(_first_image_in_zip(bad))


class GalleryZipRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.gallery_zip_route_test.db')
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
            u = User(username='gallery_zip_user', email='gzu@example.com',
                     password_hash='x', is_admin=False, access_level=10)
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def _make_zip(self, name, members):
        path = os.path.join(self.tmpdir, name)
        with zipfile.ZipFile(path, 'w') as zf:
            for member_name, data in members.items():
                zf.writestr(member_name, data)
        return path

    def _fake_gallery(self, slug='ziptest'):
        return {'slug': slug, 'label': 'Zip Test', 'path': self.tmpdir,
               'is_active': True}

    def test_image_route_serves_the_photo_inside_a_zip(self):
        self._make_zip('apod-2026-07-28.zip', {'nebula.jpg': _TINY_PNG})
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            resp = self._client().get(
                '/gallery/ziptest/img/apod-2026-07-28.zip')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, _TINY_PNG)
        self.assertEqual(resp.mimetype, 'image/jpeg')

    def test_image_route_sets_caching_headers(self):
        """Real gap found live: zip-sourced images were reported 'VERY
        slow' -- root cause was zero caching headers at all (unlike
        regular files via send_from_directory), so the browser
        re-downloaded every image on every single page view/pagination
        click. Confirm ETag/Last-Modified/Cache-Control are now set."""
        self._make_zip('apod-2026-07-28.zip', {'nebula.jpg': _TINY_PNG})
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            resp = self._client().get(
                '/gallery/ziptest/img/apod-2026-07-28.zip')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.headers.get('ETag'))
        self.assertIsNotNone(resp.headers.get('Last-Modified'))
        self.assertIn('max-age', resp.headers.get('Cache-Control', ''))

    def test_image_route_returns_304_when_etag_matches(self):
        """A repeat request with the ETag the browser already has
        cached must get a 304 with no image body -- the actual fix for
        the reported slowness (nothing gets re-transferred)."""
        self._make_zip('apod-2026-07-28.zip', {'nebula.jpg': _TINY_PNG})
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            client = self._client()
            first = client.get('/gallery/ziptest/img/apod-2026-07-28.zip')
            etag = first.headers.get('ETag')
            self.assertIsNotNone(etag)
            second = client.get('/gallery/ziptest/img/apod-2026-07-28.zip',
                               headers={'If-None-Match': etag})
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.data, b'')

    def test_image_route_304_never_touches_the_zip(self):
        """The whole point of checking is_resource_modified() before
        _read_first_image_from_zip() -- a cache-hit must short-circuit
        without ever opening the archive at all."""
        self._make_zip('apod-2026-07-28.zip', {'nebula.jpg': _TINY_PNG})
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            client = self._client()
            first = client.get('/gallery/ziptest/img/apod-2026-07-28.zip')
            etag = first.headers.get('ETag')
            with patch('anetbbs.web.gallery._read_first_image_from_zip') as m:
                second = client.get(
                    '/gallery/ziptest/img/apod-2026-07-28.zip',
                    headers={'If-None-Match': etag})
        self.assertEqual(second.status_code, 304)
        m.assert_not_called()

    def test_image_route_404s_for_zip_with_no_images(self):
        self._make_zip('empty.zip', {'notes.txt': b'no pictures here'})
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            resp = self._client().get('/gallery/ziptest/img/empty.zip')
        self.assertEqual(resp.status_code, 404)

    def test_browse_lists_zip_entries_alongside_plain_images(self):
        Path(self.tmpdir, 'loose.jpg').write_bytes(_TINY_PNG)
        self._make_zip('archived.zip', {'photo.jpg': _TINY_PNG})
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            resp = self._client().get('/gallery/ziptest/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('archived.zip', body)
        self.assertIn('loose.jpg', body)

    def test_plain_image_files_still_served_directly_not_as_zip(self):
        """Baseline / guard against a too-broad fix."""
        Path(self.tmpdir, 'direct.jpg').write_bytes(_TINY_PNG)
        with patch('anetbbs.web.gallery._get_gallery_by_slug',
                  return_value=self._fake_gallery()):
            resp = self._client().get('/gallery/ziptest/img/direct.jpg')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, _TINY_PNG)


if __name__ == '__main__':
    unittest.main()
