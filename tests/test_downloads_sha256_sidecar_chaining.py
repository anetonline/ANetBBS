"""Regression test for a real, live bug: the Release Downloads page
(anetbbs/web/downloads.py) listed EVERY file matching
DOWNLOADS_EXTENSIONS -- including a `.sha256` checksum sidecar it had
already generated for another file -- as its own top-level row, each
with its own "generate SHA-256" shield button
(templates/downloads/index.html links every row's shield button to
`sha256_for(filename=e.name)`).

Clicking the shield button on an already-listed `release.tar.gz.sha256`
row hashed the SIDECAR FILE'S OWN BYTES and wrote a new file,
`release.tar.gz.sha256.sha256` -- which then got listed as its own row
with its own button, so repeated clicks piled up an unbounded
`.sha256.sha256.sha256...` chain. Confirmed live: a real install
accumulated 10 of these before being noticed.

Fixed two ways: (1) the directory scan behind the listing now excludes
checksum/signature sidecar extensions (.sha256, .md5, .sig, .asc)
entirely, so a sidecar never gets its own row/button in the first
place; (2) the sha256_for() route itself now refuses to generate a
checksum of a file that is itself a sidecar, regardless of how the URL
is reached (a stale bookmark, a typed URL), not just when reached by
clicking a listing row.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class DownloadsSha256SidecarChainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.downloads_sidecar_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix='anetbbs_downloads_test_')
        self.app.config['DOWNLOADS_DIR'] = self._tmpdir
        self.app.config['DOWNLOADS_ENABLED'] = True
        self.app.config['DOWNLOADS_REQUIRE_LOGIN'] = False
        # The listing cache is a module-level dict keyed by DOWNLOADS_DIR
        # string; a fresh tmpdir path per test naturally busts it, but
        # clear it explicitly for isolation anyway.
        from anetbbs.web import downloads as downloads_mod
        with downloads_mod._cache_lock:
            downloads_mod._cache['ts'] = 0
            downloads_mod._cache['dir'] = ''
            downloads_mod._cache['entries'] = []

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write(self, name, content=b'hello world'):
        path = os.path.join(self._tmpdir, name)
        with open(path, 'wb') as fh:
            fh.write(content)
        return path

    def test_sidecar_file_is_not_independently_listed(self):
        """The actual regression guard: a .sha256 file sitting in the
        downloads directory must never appear as its own row -- that's
        the row/button that starts the chaining bug."""
        self._write('release.tar.gz')
        self._write('release.tar.gz.sha256', b'deadbeef  release.tar.gz\n')

        client = self.app.test_client()
        resp = client.get('/downloads/')
        html = resp.get_data(as_text=True)

        self.assertIn('release.tar.gz', html)
        self.assertNotIn('release.tar.gz.sha256<', html)
        self.assertNotIn('release.tar.gz.sha256\n', html)

    def test_hashing_a_sidecar_file_directly_is_refused(self):
        """Defense in depth: even a direct/typed URL requesting a
        checksum-of-a-checksum must be refused, not just hidden from
        the listing."""
        self._write('release.tar.gz')
        self._write('release.tar.gz.sha256', b'deadbeef  release.tar.gz\n')

        client = self.app.test_client()
        resp = client.get('/downloads/release.tar.gz.sha256.sha256')
        self.assertEqual(resp.status_code, 404)

        # And confirm no new file was written to disk as a side effect
        # of the attempt.
        self.assertFalse(os.path.exists(
            os.path.join(self._tmpdir, 'release.tar.gz.sha256.sha256')))

    def test_hashing_the_real_release_file_still_works(self):
        """Functional correctness: the fix must not break the real,
        intended use -- generating a checksum for an actual release
        artifact."""
        import hashlib
        content = b'hello world'
        self._write('release.tar.gz', content)
        expected = hashlib.sha256(content).hexdigest()

        client = self.app.test_client()
        resp = client.get('/downloads/release.tar.gz.sha256')
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        self.assertIn(expected, text)
        self.assertTrue(os.path.isfile(
            os.path.join(self._tmpdir, 'release.tar.gz.sha256')))

    def test_repeated_clicks_on_the_real_file_do_not_pile_up_sidecars(self):
        """The exact scenario reported live: clicking the shield button
        many times must never create more than the one sidecar file."""
        self._write('release.tar.gz', b'hello world')

        client = self.app.test_client()
        for _ in range(10):
            resp = client.get('/downloads/release.tar.gz.sha256')
            self.assertEqual(resp.status_code, 200)

        matches = [n for n in os.listdir(self._tmpdir)
                  if n.startswith('release.tar.gz.sha256')]
        self.assertEqual(matches, ['release.tar.gz.sha256'],
                         f'expected exactly one sidecar file, got: {matches}')


if __name__ == '__main__':
    unittest.main()
