"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): web/gallery.py's
_read_first_image_from_zip() read a zip member via ZipFile.read() with
no cap on the declared uncompressed size (a zip bomb). Since galleries
can point at TIC-fed file-area directories, an external network peer
could plant a malicious zip that any logged-in user triggers just by
browsing to the gallery. Fixed using the same shared cap
(echomail.zip_safety.MAX_MEMBER_UNCOMPRESSED/ZipBombError) every other
ZIP-extraction site in this codebase already uses.
"""
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.web.gallery import _read_first_image_from_zip
from anetbbs.echomail.zip_safety import MAX_MEMBER_UNCOMPRESSED


def _make_bomb_zip(member_name, declared_size):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, b'\x00' * declared_size, compresslevel=9)
    return buf.getvalue()


class GalleryZipBombTests(unittest.TestCase):
    def test_oversized_member_is_refused_before_decompression(self):
        import tempfile
        bomb = _make_bomb_zip('photo.jpg', MAX_MEMBER_UNCOMPRESSED + 1024)
        self.assertLess(len(bomb), 200 * 1024,
                        'the archive itself must stay tiny -- proves the '
                        'check fires from the declared-size header')
        with tempfile.NamedTemporaryFile(suffix='.zip') as f:
            f.write(bomb)
            f.flush()
            result = _read_first_image_from_zip(f.name)
        self.assertIsNone(result,
                          'an oversized member must be refused, not decompressed')

    def test_normal_sized_member_still_extracts(self):
        import tempfile
        normal = _make_bomb_zip('photo.jpg', 100)
        with tempfile.NamedTemporaryFile(suffix='.zip') as f:
            f.write(normal)
            f.flush()
            result = _read_first_image_from_zip(f.name)
        self.assertIsNotNone(result)
        data, mimetype = result
        self.assertEqual(len(data), 100)
        self.assertEqual(mimetype, 'image/jpeg')


if __name__ == '__main__':
    unittest.main()
