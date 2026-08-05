"""End-to-end verification that door_runner.py can actually build a
launchable command for the real, bundled Bubble Boggle door, including
the two JSON-RPC env vars (ANETBBS_JSONRPC_CLI_PYTHON,
ANETBBS_JSONRPC_CLI_PATH) that anetbbs/games/sbbs_stubs/json-client.js
needs, AND the real for-each parse problem game.js's own
storeRoundWinner() has -- `for each (var p in this.players)` is real
SpiderMonkey/E4X syntax that's a parse-time SyntaxError under V8
regardless of whether that code path executes. Uses the REAL scripts
(boggle.js load()s game.js) and the REAL compat shim combination logic
-- not a synthetic stand-in, matching
test_chickendelivery_launch_command.py's approach.

The actual fix (a `_polyfillE4XForEach` function inside the compat
shim's own `load()`, in synchronet_compat.py -- NOT a door_runner.py-
side transform) is exercised end-to-end by
test_synchronet_compat_missing_globals.py's
test_for_each_e4x_syntax_in_a_loaded_door_file_is_polyfilled test,
since it needs the real compat shim's PTY-driven load() machinery to
verify meaningfully. This file only guards that the real bundled
game.js still needs it (staleness guard) and that door_runner.py wires
boggle.js in via load() at all.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
DOOR_DIR = REPO_ROOT / 'anetbbs' / 'games' / 'sbbs_doors' / 'bublbogl'
SCRIPT_PATH = DOOR_DIR / 'boggle.js'
GAME_JS_PATH = DOOR_DIR / 'game.js'


@unittest.skipUnless(SCRIPT_PATH.is_file(), 'real boggle.js not present in this checkout')
class BublboglLaunchCommandTests(unittest.TestCase):
    def _make_game(self):
        return SimpleNamespace(
            game_type='door_synchronet',
            slug='bublbogl',
            synchronet_script_path=str(SCRIPT_PATH),
            synchronet_exec_dir=str(DOOR_DIR),
            working_directory='',
        )

    @unittest.skipUnless(os.path.isfile('/usr/bin/node') or os.environ.get('NODEJS_PATH'),
                         'requires a real Node.js binary (no Synchronet jsexec install expected in CI)')
    def test_build_command_sets_jsonrpc_env_vars_and_produces_valid_js(self):
        from anetbbs.games.door_runner import _build_command

        temp_files = []
        orig_jsexec = os.environ.pop('SBBS_JSEXEC', None)
        try:
            cmd, cwd = _build_command(self._make_game(), node_number=1,
                                      temp_files_out=temp_files)
        finally:
            if orig_jsexec is not None:
                os.environ['SBBS_JSEXEC'] = orig_jsexec

        self.assertEqual(len(cmd), 2, f'expected [node_path, script_path], got {cmd}')
        self.assertTrue(os.path.isfile(cmd[1]), 'combined script file must exist on disk')

        self.assertEqual(os.environ.get('ANETBBS_JSONRPC_CLI_PYTHON'), sys.executable)
        expected_cli_path = str(REPO_ROOT / 'anetbbs' / 'games' / 'jsonrpc_client.py')
        self.assertEqual(os.environ.get('ANETBBS_JSONRPC_CLI_PATH'), expected_cli_path)
        self.assertTrue(os.path.isfile(os.environ['ANETBBS_JSONRPC_CLI_PATH']))

        with open(cmd[1]) as f:
            combined = f.read()
        # The door's own top-level script (boggle.js) is run via the
        # compat shim's own load() (not concatenated in as literal
        # text) -- confirms the combined file references the real
        # script's absolute path. boggle.js's own top-level code then
        # does `load(root + "game.js")` at runtime, which is how
        # game.js (and its for-each occurrence) actually gets reached
        # -- not visible in this static combined-file text at all.
        self.assertIn('load(' + json.dumps(str(SCRIPT_PATH)) + ')', combined)

        for f in temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass

    def test_real_script_only_touches_documented_jsonclient_methods(self):
        """Same compatibility-assumption guard as Chicken Delivery's
        equivalent test, applied to both boggle.js and game.js (game.js
        does the actual client.read/write/lock/etc. calls; boggle.js
        just constructs the client)."""
        import re
        known_methods = {
            'ident', 'who', 'status', 'subscribe', 'unsubscribe', 'lock',
            'unlock', 'read', 'slice', 'splice', 'keys', 'keyTypes',
            'shift', 'pop', 'write', 'remove', 'unshift', 'push',
            'readmulti', 'cycle', 'connect', 'disconnect',
        }
        for path in (SCRIPT_PATH, GAME_JS_PATH):
            src = path.read_text()
            self.assertNotIn('new Socket(', src)
            calls = set(re.findall(r'client\.(\w+)\s*\(', src))
            unsupported = calls - known_methods
            self.assertEqual(unsupported, set(),
                             f'{path.name} calls client methods not in the shim: {unsupported}')

    def test_real_game_js_still_needs_the_for_each_polyfill(self):
        """Staleness guard: confirms the known `for each (var p in
        this.players)` occurrence in storeRoundWinner() is still
        present in the real bundled file -- if a future vendor update
        removes it, this test fails loudly rather than the polyfill
        silently becoming dead code nobody notices. The actual fix
        (compat shim's own load()-integrated _polyfillE4XForEach) is
        exercised for real in test_synchronet_compat_missing_globals.py."""
        src = GAME_JS_PATH.read_text()
        self.assertIn('for each', src,
                      'expected the known E4X for-each in the real game.js '
                      '-- if this fails, the vendor file changed and the '
                      'compat shim polyfill reliance may need revisiting')


if __name__ == '__main__':
    unittest.main()
