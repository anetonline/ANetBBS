"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): file_areas.py's thumbnail() route
called Image.open(src) (cheap, header-only) followed immediately by
im.thumbnail((256, 256)), which is what actually triggers Pillow's
full pixel decode. Pillow's own built-in decompression-bomb guard
(Image.MAX_IMAGE_PIXELS, ~179 megapixels by default) only *warns*
between 1x and 2x that threshold and only *raises* above 2x -- a
merely huge (not maximally huge) image in the warn-only range would
still fully decode into memory before ever getting downscaled to a
256px thumbnail, for every request until the lazily-generated .thumbs/
cache file exists.

Fixed by checking the header-reported dimensions (im.size, cheap --
does not trigger a decode) against _MAX_THUMBNAIL_SOURCE_PIXELS BEFORE
calling thumbnail(), falling back to serving the raw file (the same
fallback path already used for any other failure) rather than
decoding something this large just to immediately shrink it.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ThumbnailDimensionCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.thumbnail_cap_test.db')
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
            user = User(username='thumbcaptester', email='tct@example.com',
                       password_hash='x', is_admin=False, access_level=100)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self.work_dir = tempfile.mkdtemp(prefix='thumbnail_cap_')
        self.addCleanup(__import__('shutil').rmtree, self.work_dir, ignore_errors=True)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_area(self, tag, storage_path):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            area = FileArea(tag=tag, name=tag, storage_path=storage_path,
                           is_active=True, min_access_level=0)
            db.session.add(area)
            db.session.commit()
            return area.id

    def test_oversized_image_falls_back_to_raw_file_without_calling_thumbnail(self):
        """Deterministic: mocks PIL.Image.open to return a fake image
        reporting oversized header dimensions. The fake's thumbnail()/
        convert()/save() are wired to SUCCEED (not raise) if called,
        producing a distinct, recognizable "thumbnail was generated"
        byte marker -- an AssertionError instead would be
        indistinguishable from success here, since the real route
        wraps this whole block in a broad `except Exception` that
        falls back to the raw file either way, silently swallowing a
        raised assertion along with any real error. Asserting on the
        actual response BODY (raw source bytes vs. the fake-thumbnail
        marker) is what actually proves the size check ran before
        thumbnail() rather than after some exception."""
        storage_dir = os.path.join(self.work_dir, 'storage')
        os.makedirs(storage_dir)
        img_path = os.path.join(storage_dir, 'huge.png')
        raw_bytes = b'not a real png, just needs to exist on disk'
        with open(img_path, 'wb') as f:
            f.write(raw_bytes)

        area_id = self._make_area('THUMBCAP', storage_dir)
        marker = b'FAKE-THUMBNAIL-WAS-GENERATED'

        class _FakeOversizedImage:
            def __init__(self):
                self.size = (10000, 10000)  # 100 megapixels > the 40MP cap
                self.mode = 'RGB'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def thumbnail(self, *a, **k):
                pass  # would succeed silently if the size check didn't run first

            def convert(self, *a, **k):
                return self

            def save(self, buf, **k):
                buf.write(marker)

        client = self._client_as(self.user_id)
        # thumbnail() does `from PIL import Image` INSIDE the function
        # body (a fresh local lookup against the already-imported,
        # cached PIL.Image module on every call) rather than importing
        # it at file_areas module scope -- patching PIL.Image.open
        # directly is what actually takes effect here.
        with patch('PIL.Image.open', return_value=_FakeOversizedImage()):
            resp = client.get(f'/file-areas/{area_id}/thumb/huge.png')

        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(
            resp.data, marker,
            'the fake thumbnail marker must never appear -- that would '
            'mean thumbnail()/convert()/save() ran despite the oversized '
            'declared dimensions, i.e. the size check did not run before '
            'the decode-triggering call')
        self.assertEqual(
            resp.data, raw_bytes,
            'must fall back to serving the raw source file once the '
            'size check rejects it')

    def test_normal_sized_image_still_gets_a_real_thumbnail(self):
        """Sanity check the cap doesn't affect ordinary uploads --
        uses a real, small Pillow-generated image."""
        from PIL import Image as RealImage

        storage_dir = os.path.join(self.work_dir, 'storage')
        os.makedirs(storage_dir)
        img_path = os.path.join(storage_dir, 'small.png')
        im = RealImage.new('RGB', (100, 80), color=(255, 0, 0))
        im.save(img_path, format='PNG')

        area_id = self._make_area('THUMBNORMAL', storage_dir)

        client = self._client_as(self.user_id)
        resp = client.get(f'/file-areas/{area_id}/thumb/small.png')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'image/png')

        out = RealImage.open(__import__('io').BytesIO(resp.data))
        self.assertLessEqual(max(out.size), 256)


if __name__ == '__main__':
    unittest.main()
