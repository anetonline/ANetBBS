"""Tests for the /data/mods/ file-override mechanism (Jerry: "we really
need to finish wiring in the /data/mods/ directory I have mentioned a
few times now... the BBS should check the mods folder first... just
like Synchronet has a /sbbs/mods/ directory").

Real finding: this was already half-built before this change --
synchronet_compat.py already computed a `mods_dir` value and threaded
it through as `bbs.mods_dir`/`system.mods_dir` (real Synchronet API
surface some doors read directly), but the internal `js.mods_dir` used
by the compat shim's OWN load() resolver never existed, so load()
never actually consulted it regardless of what mods_dir pointed at --
and mods_dir itself pointed at a per-door sibling directory, not
ANetBBS's own central data/ tree. This file covers the Python-side
half: _apply_mods_override()/_mods_dir() in door_runner.py, which
covers a Synchronet door's own top-level entry-point script (the one
file that never goes through load() at all, since it's the file handed
directly to jsexec/node as the process entry point).

See test_synchronet_compat_mods_dir_load.py for the JS-side half
(files loaded via load() at runtime, e.g. stub/library files or a
door's own sibling scripts).
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ModsDirHelperTests(unittest.TestCase):
    def test_mods_dir_is_under_data_directory(self):
        from anetbbs.games.door_runner import _mods_dir
        result = _mods_dir()
        self.assertTrue(result.endswith(os.path.join('data', 'mods')))

    def test_no_override_present_returns_original_path(self):
        from anetbbs.games.door_runner import _apply_mods_override
        with patch('anetbbs.games.door_runner._mods_dir',
                   return_value='/nonexistent/mods/dir/xyz'):
            result = _apply_mods_override('/some/stock/path/door.js')
        self.assertEqual(result, '/some/stock/path/door.js')

    def test_falsy_path_returned_unchanged(self):
        from anetbbs.games.door_runner import _apply_mods_override
        self.assertEqual(_apply_mods_override(''), '')
        self.assertIsNone(_apply_mods_override(None))

    def test_override_present_wins_over_stock_path(self):
        from anetbbs.games.door_runner import _apply_mods_override
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            override_path = os.path.join(tmpdir, 'door.js')
            with open(override_path, 'w') as f:
                f.write('// sysop override')
            with patch('anetbbs.games.door_runner._mods_dir', return_value=tmpdir):
                result = _apply_mods_override('/some/stock/path/door.js')
            self.assertEqual(result, override_path)

    def test_match_is_by_basename_not_full_relative_structure(self):
        """The entry-point script path is normally already absolute
        (Game.synchronet_script_path), so there's no meaningful
        relative-subdirectory structure to preserve the way there is
        for synchronet_compat.py's own load()-driven files -- a sysop
        overriding /opt/anetbbs/anetbbs/games/sbbs_doors/lord/lord.js
        just drops data/mods/lord.js, regardless of the original's
        deep package-tree location."""
        from anetbbs.games.door_runner import _apply_mods_override
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            override_path = os.path.join(tmpdir, 'lord.js')
            with open(override_path, 'w') as f:
                f.write('// sysop override')
            with patch('anetbbs.games.door_runner._mods_dir', return_value=tmpdir):
                result = _apply_mods_override(
                    '/opt/anetbbs/anetbbs/games/sbbs_doors/lord/lord.js')
            self.assertEqual(result, override_path)


class BuildCommandUsesModsOverrideTests(unittest.TestCase):
    """Confirms _build_command() actually applies the override for a
    real door_synchronet game, not just that the helper function works
    in isolation."""

    def _make_game(self, script_path):
        return SimpleNamespace(
            game_type='door_synchronet',
            synchronet_script_path=script_path,
            synchronet_exec_dir='',
            working_directory='',
            msgbase_area_id=None,
        )

    def test_build_command_applies_override_to_the_resolved_script_path(self):
        """Proves the WIRING, not just that _apply_mods_override() works
        in isolation (covered above): patches _apply_mods_override itself
        and confirms _build_command() actually calls it with the
        resolved stock path, on the real door_synchronet code path."""
        import tempfile
        from anetbbs.games.door_runner import _build_command

        with tempfile.TemporaryDirectory() as stock_dir:
            stock_script = os.path.join(stock_dir, 'mydoor.js')
            with open(stock_script, 'w') as f:
                f.write('// stock version\n')
            fake_compat_path = os.path.join(stock_dir, 'fake_compat.js')
            with open(fake_compat_path, 'w') as f:
                f.write('// fake compat shim\n')

            game = self._make_game(stock_script)
            with patch('anetbbs.games.door_runner._apply_mods_override',
                       side_effect=lambda p: p) as mock_override, \
                 patch('anetbbs.games.door_runner._find_jsexec', return_value=None), \
                 patch.dict(os.environ, {'NODEJS_PATH': '/usr/bin/node'}), \
                 patch('anetbbs.games.synchronet_compat.write_compat_script',
                       return_value=fake_compat_path), \
                 patch('os.path.isfile', side_effect=lambda p: True):
                _build_command(game, node_number=1, bbs_name='TestBBS')

        mock_override.assert_called_once_with(stock_script)


if __name__ == '__main__':
    unittest.main()
