"""Regression test for a path-traversal gap found in a full-subsystem
BinkP audit: neither binkp.py's inbound fallback-write path (used for
TIC manifests / hatched binaries that aren't a recognized FTS-0001
packet or ZIP bundle) nor binkp_server.py's equivalent inbound-listener
path ever called os.path.basename() or rejected a traversal/absolute
path on a peer-supplied CMD_FILE filename. os.path.join() with an
absolute second argument silently discards the first, so an
authenticated-but-malicious or misconfigured peer offering a filename
like '/etc/cron.d/x' or '../../etc/passwd' could write outside the
intended inbound directory.

_sanitize_inbound_filename() closes this: reduces to a bare basename,
strips backslashes and leading dots, falls back to a safe default name
if nothing usable remains.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class SanitizeInboundFilenameTests(unittest.TestCase):
    def test_plain_filename_unchanged(self):
        from anetbbs.echomail.binkp import _sanitize_inbound_filename
        self.assertEqual(_sanitize_inbound_filename('normal.tic'), 'normal.tic')

    def test_unix_path_traversal_reduced_to_basename(self):
        from anetbbs.echomail.binkp import _sanitize_inbound_filename
        result = _sanitize_inbound_filename('../../etc/passwd')
        self.assertNotIn('/', result)
        self.assertNotIn('..', result)

    def test_absolute_unix_path_reduced_to_basename(self):
        from anetbbs.echomail.binkp import _sanitize_inbound_filename
        result = _sanitize_inbound_filename('/etc/cron.d/malicious')
        self.assertEqual(result, 'malicious')

    def test_windows_style_backslash_path_stripped(self):
        from anetbbs.echomail.binkp import _sanitize_inbound_filename
        result = _sanitize_inbound_filename('..\\..\\windows\\system32\\evil.dll')
        self.assertNotIn('\\', result)
        self.assertNotIn('..', result)

    def test_empty_or_dot_only_falls_back_to_default(self):
        from anetbbs.echomail.binkp import _sanitize_inbound_filename
        self.assertEqual(_sanitize_inbound_filename(''), 'unnamed.bin')
        self.assertEqual(_sanitize_inbound_filename('.'), 'unnamed.bin')
        self.assertEqual(_sanitize_inbound_filename('..'), 'unnamed.bin')

    def test_binkp_server_reuses_same_helper(self):
        """binkp_server.py must import and use the same sanitizer, not
        a second hand-rolled copy that could drift out of sync."""
        from anetbbs.echomail import binkp_server as mod
        self.assertIn('_sanitize_inbound_filename', dir(mod))


if __name__ == '__main__':
    unittest.main()
