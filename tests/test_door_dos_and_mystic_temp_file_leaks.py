"""Regression test for a real High-severity finding from a security/
performance audit (2026-08-31): _build_dos_command() (door_dos) and
_build_mystic_python_command() (door_mystic) each write a temp file
with delete=False and never registered it anywhere for cleanup -- the
exact same leak class already found and fixed once in this file for
the Synchronet-JS compat path (see test_door_synchronet_temp_cleanup.py
and _build_command()'s own temp_files_out docstring: "hundreds had
accumulated on a real install... contributing to a disk-full
incident"). door_dos in particular is one of the most heavily-used
game types in this file, so this was likely the highest-volume of the
file leaks found this round.

DoorSession.close() already generically deletes every path in
session.temp_files regardless of which game type populated it (see
test_door_synchronet_temp_cleanup.py's DoorSessionTempFileCleanupTests
for that side, unchanged by this fix) -- this file only needs to
confirm the two builder functions now actually populate temp_files_out
when given one, the same way the Synchronet path already does.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class BuildDosCommandTempFilesTests(unittest.TestCase):
    @unittest.skipUnless(
        __import__('shutil').which('dosbox')
        or __import__('shutil').which('dosbox-staging')
        or __import__('shutil').which('dosbox-x')
        or os.environ.get('DOSBOX_PATH'),
        'requires a real DOSBox binary')
    def test_dosbox_conf_is_tracked_when_temp_files_out_given(self):
        from anetbbs.games.door_runner import _build_dos_command
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = os.path.join(tmpdir, 'DOOR.EXE')
            with open(exe_path, 'w') as f:
                f.write('not a real dos exe, just needs to exist')
            game = SimpleNamespace(
                slug='test-dos-game',
                executable_path=exe_path,
                working_directory=tmpdir,
                command_line_args='',
            )
            collected = []
            cmd, cwd = _build_dos_command(
                game, node_number=1, cwd=tmpdir,
                bridge_port=12345, temp_files_out=collected)
            self.assertEqual(len(collected), 1,
                             f'expected exactly 1 tracked temp file, got: {collected}')
            conf_path = collected[0]
            self.assertTrue(os.path.isfile(conf_path))
            self.assertIn('dosbox.conf', os.path.basename(conf_path))
            # The conf must be one of the args DOSBox is actually invoked
            # with -- otherwise cleanup would delete a file the door still
            # needs to run.
            self.assertIn(conf_path, cmd)
            os.unlink(conf_path)

    def test_temp_files_out_not_touched_when_none(self):
        """Default behavior (no collector passed) must not raise."""
        import shutil
        if not (shutil.which('dosbox') or shutil.which('dosbox-staging')
                or shutil.which('dosbox-x') or os.environ.get('DOSBOX_PATH')):
            self.skipTest('requires a real DOSBox binary')
        from anetbbs.games.door_runner import _build_dos_command
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = os.path.join(tmpdir, 'DOOR.EXE')
            with open(exe_path, 'w') as f:
                f.write('placeholder')
            game = SimpleNamespace(
                slug='test-dos-game-2', executable_path=exe_path,
                working_directory=tmpdir, command_line_args='')
            cmd, cwd = _build_dos_command(game, node_number=1, cwd=tmpdir,
                                          bridge_port=12345)
            try:
                os.unlink(cmd[cmd.index('-conf') + 1])
            except (ValueError, OSError):
                pass


class BuildMysticPythonCommandTempFilesTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.script_path = os.path.join(self._tmpdir.name, 'door.mpy')
        with open(self.script_path, 'w') as f:
            f.write('def main():\n    write("hi")\n\nmain()\n')

    def test_runner_wrapper_is_tracked_when_temp_files_out_given(self):
        from anetbbs.games.door_runner import _build_mystic_python_command
        game = SimpleNamespace(mystic_script_path=self.script_path)
        collected = []
        cmd, cwd = _build_mystic_python_command(
            game, cwd=self._tmpdir.name, temp_files_out=collected)
        self.assertEqual(len(collected), 1,
                         f'expected exactly 1 tracked temp file, got: {collected}')
        runner_path = collected[0]
        self.assertTrue(os.path.isfile(runner_path))
        self.assertIn('mystic_runner.py', os.path.basename(runner_path))
        self.assertEqual(cmd[-1], runner_path,
                         'the tracked file must be the one actually executed')
        os.unlink(runner_path)

    def test_temp_files_out_not_touched_when_none(self):
        from anetbbs.games.door_runner import _build_mystic_python_command
        game = SimpleNamespace(mystic_script_path=self.script_path)
        cmd, cwd = _build_mystic_python_command(game, cwd=self._tmpdir.name)
        try:
            os.unlink(cmd[-1])
        except OSError:
            pass


if __name__ == '__main__':
    unittest.main()
