"""Regression test for a real bug found on the first-ever live test of
door_mystic_mps: `-x` is not a real Mystic BBS command-line flag -- not
documented anywhere in Mystic's own install guide or changelog, and
empirically the binary just ignores it and falls through to a full
interactive local-login session (`-l`'s documented behavior), not the
door script at all. The real flag for running a compiled MPL script
standalone is `-y<script>`, combined with `-u<username> -p<password>`
for the account it runs under -- confirmed against Mystic's own
documented command-line switches (there is no anonymous/no-login mode).

Reuses command_line_args exactly like door_rlogin already does for its
own remote-login credentials: "USERNAME_OR_@USER@ PASSWORD".
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.games.door_runner import _build_command


class DoorMysticMpsLaunchCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)

        self.mystic_bin = base / 'mystic'
        self.mystic_bin.write_text('#!/bin/sh\necho fake mystic\n')
        self.mystic_bin.chmod(0o755)

        self.script_path = base / 'mrc_client.mpx'
        self.script_path.write_bytes(b'\x00fake-compiled-bytecode')

        self._orig_mystic_env = os.environ.get('MYSTIC_BBS_PATH')
        os.environ['MYSTIC_BBS_PATH'] = str(self.mystic_bin)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._orig_mystic_env is None:
            os.environ.pop('MYSTIC_BBS_PATH', None)
        else:
            os.environ['MYSTIC_BBS_PATH'] = self._orig_mystic_env

    def _make_game(self, command_line_args='@USER@ mypassword', working_directory=''):
        return SimpleNamespace(
            game_type='door_mystic_mps',
            slug='mystic-mrc-test',
            mystic_script_path=str(self.script_path),
            working_directory=working_directory,
            command_line_args=command_line_args,
        )

    def test_uses_dash_y_not_dash_x(self):
        cmd, cwd = _build_command(
            self._make_game(), 1, 'ANetBBS',
            user={'id': 1, 'username': 'StingRay'})
        self.assertEqual(cmd[0], str(self.mystic_bin))
        self.assertNotIn('-x', cmd)
        self.assertTrue(any(a.startswith('-y') for a in cmd),
                        f'expected a -y<script> arg, got {cmd}')

    def test_at_user_token_substitutes_real_caller_username(self):
        cmd, _ = _build_command(
            self._make_game(command_line_args='@USER@ hunter2'), 1, 'ANetBBS',
            user={'id': 1, 'username': 'StingRay'})
        self.assertIn('-uStingRay', cmd)
        self.assertIn('-phunter2', cmd)

    def test_fixed_shared_account_with_no_at_user_token(self):
        cmd, _ = _build_command(
            self._make_game(command_line_args='anetbbs sharedpw'), 1, 'ANetBBS',
            user={'id': 1, 'username': 'StingRay'})
        self.assertIn('-uanetbbs', cmd)
        self.assertIn('-psharedpw', cmd)

    def test_missing_command_line_args_raises_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            _build_command(
                self._make_game(command_line_args=''), 1, 'ANetBBS',
                user={'id': 1, 'username': 'StingRay'})
        self.assertIn('USERNAME', str(ctx.exception))

    def test_working_directory_override_used_as_cwd(self):
        real_mystic_dir = Path(self._tmp.name) / 'mystic_install'
        real_mystic_dir.mkdir()
        _, cwd = _build_command(
            self._make_game(working_directory=str(real_mystic_dir)), 1, 'ANetBBS',
            user={'id': 1, 'username': 'StingRay'})
        self.assertEqual(cwd, str(real_mystic_dir))

    def test_no_working_directory_defaults_to_scripts_own_directory(self):
        _, cwd = _build_command(
            self._make_game(working_directory=''), 1, 'ANetBBS',
            user={'id': 1, 'username': 'StingRay'})
        self.assertEqual(cwd, str(self.script_path.parent))


if __name__ == '__main__':
    unittest.main()
