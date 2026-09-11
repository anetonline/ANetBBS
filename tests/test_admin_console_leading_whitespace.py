"""Regression test for a real Low-severity finding from a security/
performance audit (2026-09-10): admin.py's _run_sysop_command() (backs
the Admin -> Sysop Console page) matched the `tail `/`journalctl `
command prefixes against cmd_low = cmd.strip().lower(), but then sliced
the argument out of the ORIGINAL, un-stripped `cmd` using a fixed
offset (cmd[5:] / cmd[len('journalctl '):]).

Any command with 2+ leading whitespace characters (easy to produce by
pasting) shifted `cmd` out of alignment with the offset computed
against the stripped string, chopping the wrong characters off the
front of the path/unit argument -- a syntactically valid, allow-listed
command like "  tail /tmp/anetbbs_dos_dosbox.log" was rejected with
"tail: only allowed paths: ..." even though the path was correct,
because the parsed argument came out as "l /tmp/anetbbs_dos_dosbox.log"
instead.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class AdminConsoleLeadingWhitespaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_console_ws_test.db')
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

    def test_tail_with_leading_whitespace_still_parses_the_real_path(self):
        from anetbbs.web.admin import _run_sysop_command
        with self.app.app_context():
            out_clean = _run_sysop_command('tail /tmp/anetbbs_dos_dosbox.log')
            out_padded = _run_sysop_command('  tail /tmp/anetbbs_dos_dosbox.log')
        # Whether or not the file exists on this box, the two must fail
        # (or succeed) the SAME way -- the allow-list rejection message
        # must never appear just because of leading whitespace.
        self.assertNotIn('only allowed paths', out_padded,
                         'leading whitespace must not corrupt the parsed '
                         'tail path')
        self.assertEqual(
            ('only allowed paths' in out_clean),
            ('only allowed paths' in out_padded),
            'padded and unpadded commands must parse to the same path')

    def test_journalctl_with_leading_whitespace_still_parses_the_real_unit(self):
        from anetbbs.web.admin import _run_sysop_command
        with self.app.app_context():
            out_padded = _run_sysop_command('  journalctl anetbbs-web')
        self.assertNotIn('only anetbbs* units allowed', out_padded,
                         'leading whitespace must not corrupt the parsed '
                         'journalctl unit name (it starts with anetbbs)')


if __name__ == '__main__':
    unittest.main()
