"""Regression tests for anetbbs/features/archive_meta.py's
decompression-bomb cap.

Real vulnerability found in a security/performance audit:
test_archive_integrity() runs on every uploaded file (web/files.py,
web/file_areas.py) before the upload is accepted, and its zip path used
to hand every member straight to zipfile.testzip() -- which fully
decompresses each member to verify its CRC -- with no size check at
all. Its tar path did the equivalent with a manual read-through loop.
Both are exactly the operation a decompression bomb (a small compressed
file whose header declares/produces a wildly disproportionate amount of
decompressed output) is built to abuse, and any user with upload access
could reach it well within the existing 100MB UPLOAD_MAX_SIZE.

Fixed by checking each format's own declared uncompressed size (a zip's
central directory file_size / a tar member's header size -- both free
to read without decompressing anything, and for tar specifically the
same value that bounds how many bytes extractfile().read() could ever
actually produce for that member) against MAX_ARCHIVE_UNCOMPRESSED_BYTES
before ever touching the compressed data.

These tests patch that cap down to a tiny value rather than fabricating
multi-gigabyte fixtures -- the code path being verified (sum declared
sizes, compare to the cap, bail before decompression) doesn't care what
the actual threshold is, only that a real archive whose declared size
sits above it gets rejected, and one below it is still tested normally.
"""
import io
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features import archive_meta


class DecompressionBombCapTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _path(self, name):
        return os.path.join(self._tmpdir.name, name)

    # -- zip --------------------------------------------------------------

    def test_zip_over_the_declared_size_cap_is_rejected_before_testzip(self):
        path = self._path('big.zip')
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('payload.txt', b'x' * 200)
        with patch.object(archive_meta, 'MAX_ARCHIVE_UNCOMPRESSED_BYTES', 100):
            result = archive_meta.test_archive_integrity(path)
        self.assertFalse(result.ok)
        self.assertIn('decompression bomb', result.message)

    def test_zip_under_the_cap_is_still_tested_normally(self):
        path = self._path('small.zip')
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('payload.txt', b'x' * 10)
        with patch.object(archive_meta, 'MAX_ARCHIVE_UNCOMPRESSED_BYTES', 1000):
            result = archive_meta.test_archive_integrity(path)
        self.assertTrue(result.ok)
        self.assertEqual(result.message, 'clean')

    # -- tar ----------------------------------------------------------------

    def test_tar_over_the_declared_size_cap_is_rejected_before_full_read(self):
        path = self._path('big.tar')
        data = b'y' * 200
        with tarfile.open(path, 'w') as tf:
            info = tarfile.TarInfo(name='payload.bin')
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        with patch.object(archive_meta, 'MAX_ARCHIVE_UNCOMPRESSED_BYTES', 100):
            result = archive_meta.test_archive_integrity(path)
        self.assertFalse(result.ok)
        self.assertIn('decompression bomb', result.message)

    def test_tar_under_the_cap_is_still_tested_normally(self):
        path = self._path('small.tar')
        data = b'y' * 10
        with tarfile.open(path, 'w') as tf:
            info = tarfile.TarInfo(name='payload.bin')
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        with patch.object(archive_meta, 'MAX_ARCHIVE_UNCOMPRESSED_BYTES', 1000):
            result = archive_meta.test_archive_integrity(path)
        self.assertTrue(result.ok)
        self.assertTrue(result.message.startswith('clean'))


if __name__ == '__main__':
    unittest.main()
