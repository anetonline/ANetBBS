"""Regression test for a real, severe OOM found live (2026-08-28):
Jerry's dev laptop repeatedly froze for 30+ seconds and the kernel OOM
killer repeatedly killed a `python` process holding 11-12GB of
anon-rss during ANetBBS pytest runs.

Root cause, found by bisecting batches down to a single test file
(tests/test_admin_afk_warning_setting.py) and watching real RSS: that
test's GET /admin/settings hit anetbbs.web.admin.settings(), which did
`f.readlines()[-100:]` on the file named by LOG_FILE -- reading the
ENTIRE file into a list of per-line string objects before slicing.
TestingConfig never overrides LOG_FILE, so this read the SAME real,
unrotated bbs.log every other test run in this repo had been appending
to all session -- 6.1GB / 80,284,907 lines at the time this was found.
CPython's per-object overhead across that many small string objects
pushed actual heap usage well past the file's own raw size, landing in
exactly the 11-12GB range the kernel reported. A second, identical
copy of the same bug existed in the sysop "tail <path>" web-console
command (admin.py, around line 3450), reading real system logs
(/var/log/syslog and friends) the same unbounded way.

Both call sites now share anetbbs.web.admin._tail_lines(), which seeks
near the end of the file and reads a small, fixed-size window instead
of the whole file. This test proves two things about that helper:

1. Correctness -- the returned lines are genuinely the file's last N,
   not an arbitrary window.
2. The actual fix -- that a MUCH larger file than the read window
   still returns correctly and the underlying read() call is bounded
   to the window size, not the file size (proven by wrapping the real
   file object and asserting on what was actually asked of it, not by
   inference from wall-clock time).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.web.admin import _tail_lines


class TailLinesBoundedReadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _make_file(self, line_count, line_body='x' * 50):
        path = os.path.join(self._tmp.name, 'big.log')
        with open(path, 'w') as f:
            for i in range(line_count):
                f.write(f'line {i:08d} {line_body}\n')
        return path

    def test_returns_the_real_last_n_lines_not_an_arbitrary_window(self):
        # Big enough that the 256KB default window can't possibly hold
        # the whole file (each line here is ~64 bytes; 200,000 lines is
        # ~12MB, roughly 48x the read window), so this only passes if
        # the seek-to-near-the-end logic is actually finding the TRUE
        # tail of the file, not just whatever fits in an early window.
        path = self._make_file(200_000)
        tail = _tail_lines(path, 100)
        self.assertEqual(len(tail), 100)
        self.assertTrue(tail[-1].startswith('line 00199999 '),
                        f'last returned line should be the file\'s real last '
                        f'line, got: {tail[-1]!r}')
        self.assertTrue(tail[0].startswith('line 00199900 '),
                        f'first of the last 100 should be line 199900, '
                        f'got: {tail[0]!r}')

    def test_small_file_shorter_than_the_window_still_works(self):
        path = self._make_file(5)
        tail = _tail_lines(path, 100)
        self.assertEqual(len(tail), 5)
        self.assertTrue(tail[-1].startswith('line 00000004 '))

    def test_read_is_bounded_to_the_window_not_the_whole_file(self):
        """The real regression check: wraps the actual file object so
        the test can see exactly how many bytes read() was asked to
        return, and asserts it stays at the requested window size even
        though the file itself is ~50x larger -- the same shape as
        bbs.log being ~23000x larger than the 256KB window it's read
        through live."""
        # ~24000 lines * ~64 bytes/line =~ 1.5MB, well past the 10KB
        # window used below.
        path = self._make_file(24_000)
        real_size = os.path.getsize(path)
        window = 10_000
        self.assertGreater(real_size, window * 10,
                           'test file must be much larger than the read '
                           'window for this assertion to mean anything')

        read_sizes = []
        real_open = open

        def _spying_open(p, mode='r', *a, **kw):
            fh = real_open(p, mode, *a, **kw)
            if p == path and 'b' in mode:
                orig_read = fh.read

                def _spy_read(*a, **kw):
                    data = orig_read(*a, **kw)
                    read_sizes.append(len(data))
                    return data
                fh.read = _spy_read
            return fh

        with patch('anetbbs.web.admin.open', side_effect=_spying_open):
            tail = _tail_lines(path, 100, window_bytes=window)

        self.assertTrue(tail[-1].startswith('line 00023999 '))
        self.assertEqual(len(read_sizes), 1,
                         'expected exactly one read() call against the file')
        self.assertLessEqual(
            read_sizes[0], window,
            f'read {read_sizes[0]} bytes from a {real_size}-byte file with a '
            f'{window}-byte window -- this is the exact shape of the live '
            f'OOM (f.readlines() reading the whole 6.1GB bbs.log)')


if __name__ == '__main__':
    unittest.main()
