"""Regression test for a real leak found while investigating a disk-full
incident: every Synchronet-JS door launch (the Node.js compat fallback
path, used when no real Synchronet jsexec binary is installed) created
two temp files -- a generated synchronet_compat.js and a combined
synchronet_run.js -- that were never tracked anywhere for cleanup.
_cleanup_session(), the one place a door session's resources get
released, had no knowledge of them at all. Confirmed live: a real
install had accumulated well over a hundred orphaned files in /tmp from
this, contributing directly to a disk-full corruption incident.

Fixed by having _build_command() append the paths to an optional
temp_files_out list the caller provides, and DoorSession.close()
(already responsible for cleaning up the analogous door_dosemu pts
symlink) delete them the same way.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class BuildCommandSynchronetTempFilesTests(unittest.TestCase):
    """Exercises the real Node.js-compat code path in _build_command()
    directly -- no mocking of the temp-file creation itself, just a
    minimal stand-in `game` object with the handful of attributes that
    path actually reads (confirmed by grepping synchronet_compat.py and
    door_runner.py for every `game.<attr>` access in this path)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # A minimal, syntactically-valid Synchronet-style door script.
        self.script_path = os.path.join(self._tmpdir.name, 'door.js')
        with open(self.script_path, 'w') as f:
            f.write('function main() { print("hi"); }\nmain();\n')

    def _make_game(self):
        return SimpleNamespace(
            game_type='door_synchronet',
            synchronet_script_path=self.script_path,
            synchronet_exec_dir='',  # empty -> _find_jsexec() falls through
            working_directory='',
        )

    @unittest.skipUnless(os.path.isfile('/usr/bin/node') or os.environ.get('NODEJS_PATH'),
                         'requires a real Node.js binary (no Synchronet jsexec install expected in CI)')
    def test_temp_files_out_populated_with_two_real_files(self):
        from anetbbs.games.door_runner import _build_command
        game = self._make_game()
        collected = []
        cmd, cwd = _build_command(game, node_number=1, bbs_name='TestBBS',
                                  user=None, temp_files_out=collected)
        self.assertEqual(len(collected), 2,
                         f'expected exactly 2 temp files tracked, got: {collected}')
        for path in collected:
            self.assertTrue(os.path.isfile(path), f'{path} should exist on disk')
            self.assertIn('synchronet', os.path.basename(path))
        # The actual combined run script (cmd[-1]) must be one of the
        # tracked files -- otherwise cleanup would delete a file still
        # needed to run the door, or fail to delete the one that matters.
        self.assertIn(cmd[-1], collected)
        # Cleanup for the test itself, since nothing else will.
        for path in collected:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_temp_files_out_not_touched_when_none(self):
        """Default behavior (no collector passed) must not raise -- every
        existing caller of _build_command() before this fix didn't pass
        one, and none should break."""
        from anetbbs.games.door_runner import _build_command
        game = self._make_game()
        if not (os.path.isfile('/usr/bin/node') or os.environ.get('NODEJS_PATH')):
            self.skipTest('requires a real Node.js binary')
        cmd, cwd = _build_command(game, node_number=1, bbs_name='TestBBS', user=None)
        # Still creates the temp files (the door needs them to run) --
        # just doesn't report them anywhere. Clean up manually.
        try:
            os.unlink(cmd[-1])
        except OSError:
            pass


class DoorSessionTempFileCleanupTests(unittest.TestCase):
    """DoorSession.close() is the one place a session's resources get
    released (via _cleanup_session()) regardless of how the session
    ended -- PTY EOF, bridge close, forced termination, or the waitpid
    watcher reaping a crashed child. Testing close() directly, not the
    full fork/PTY lifecycle, which isn't practical to exercise in a
    unit test."""

    def test_close_deletes_tracked_temp_files(self):
        from anetbbs.games.door_runner import DoorSession
        f1 = tempfile.NamedTemporaryFile(delete=False, suffix='_synchronet_compat.js')
        f2 = tempfile.NamedTemporaryFile(delete=False, suffix='_synchronet_run.js')
        f1.close()
        f2.close()
        self.assertTrue(os.path.isfile(f1.name))
        self.assertTrue(os.path.isfile(f2.name))

        # A fake pid that's already reaped / doesn't exist -- close()
        # must not raise just because the kill() target is gone.
        fake_pid = 999999
        session = DoorSession(session_id=1, master_fd=-1, pid=fake_pid)
        session.temp_files = [f1.name, f2.name]
        session.close()

        self.assertFalse(os.path.isfile(f1.name), 'compat.js should be deleted on close()')
        self.assertFalse(os.path.isfile(f2.name), 'run.js should be deleted on close()')
        self.assertEqual(session.temp_files, [])

    def test_close_with_no_temp_files_does_not_raise(self):
        """Every non-Synchronet game type (native binaries, Mystic,
        DOSBox) never populates temp_files -- close() must be a no-op
        for them, not error out on an empty list."""
        from anetbbs.games.door_runner import DoorSession
        session = DoorSession(session_id=2, master_fd=-1, pid=999999)
        session.close()  # should not raise
        self.assertEqual(session.temp_files, [])

    def test_close_tolerates_already_deleted_temp_file(self):
        """A file already gone (e.g. deleted manually, or double-close())
        must not raise -- matches the existing defensive pattern used
        for pts_path cleanup in the same method."""
        from anetbbs.games.door_runner import DoorSession
        f = tempfile.NamedTemporaryFile(delete=False)
        f.close()
        os.unlink(f.name)
        session = DoorSession(session_id=3, master_fd=-1, pid=999999)
        session.temp_files = [f.name]
        session.close()  # should not raise despite the file being gone


if __name__ == '__main__':
    unittest.main()
