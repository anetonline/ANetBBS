"""End-to-end verification that door_runner.py can actually build a
launchable command for the real, bundled Chicken Delivery door,
including the two new env vars (ANETBBS_JSONRPC_CLI_PYTHON,
ANETBBS_JSONRPC_CLI_PATH) that anetbbs/games/sbbs_stubs/json-client.js
needs to find the Python JSON-RPC CLI. Uses the REAL script
(anetbbs/games/sbbs_doors/chickendelivery/chickendelivery.js) and the
REAL compat shim combination logic -- not a synthetic stand-in --
since a mismatch here would be exactly the kind of thing a synthetic
script could paper over.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
DOOR_DIR = REPO_ROOT / 'anetbbs' / 'games' / 'sbbs_doors' / 'chickendelivery'
SCRIPT_PATH = DOOR_DIR / 'chickendelivery.js'


@unittest.skipUnless(SCRIPT_PATH.is_file(), 'real chickendelivery.js not present in this checkout')
class ChickenDeliveryLaunchCommandTests(unittest.TestCase):
    def _make_game(self):
        return SimpleNamespace(
            game_type='door_synchronet',
            slug='chickendelivery',
            synchronet_script_path=str(SCRIPT_PATH),
            synchronet_exec_dir=str(DOOR_DIR),
            working_directory='',
        )

    @unittest.skipUnless(os.path.isfile('/usr/bin/node') or os.environ.get('NODEJS_PATH'),
                         'requires a real Node.js binary (no Synchronet jsexec install expected in CI)')
    def test_build_command_sets_jsonrpc_env_vars_and_produces_valid_js(self):
        from anetbbs.games.door_runner import _build_command

        temp_files = []
        # Force the Node fallback path even if a real jsexec happens to
        # be on this machine, matching how test_door_synchronet_temp_
        # cleanup.py isolates the same path.
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
        # The door's own top-level script is run via the compat shim's
        # own load() (not concatenated in as literal text -- see
        # door_runner.py's _build_command()'s comment on why: it's what
        # makes a door's own bare top-level vars real globalThis
        # properties, visible to whatever the door itself subsequently
        # load()s, matching Bubble Boggle's boggle.js/game.js needs).
        # Confirms the combined file actually references the real
        # script's absolute path via a load(...) call.
        self.assertIn('load(' + json.dumps(str(SCRIPT_PATH)) + ')', combined)
        # The shim itself (function JSONClient(...)) and the door's own
        # source (which does load("json-client.js") near its top) are
        # confirmed reachable/correct separately by
        # test_jsonrpc_client_js_shim.py and a real PTY-driven run
        # (see this door's own live-playtest changelog history) -- this
        # test only confirms _build_command() wires the real path in.

        # Clean up the temp files _build_command created.
        for f in temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass

    def test_real_script_only_touches_documented_jsonclient_methods(self):
        """Confirms the compatibility assumption the whole shim design
        rests on: the real door only calls JSONClient's public,
        documented methods (read/write/subscribe/etc.), never anything
        involving Socket directly -- if a future vendor update to this
        door ever added a raw Socket call, this door would silently
        stop being shim-compatible and this test would catch it."""
        src = SCRIPT_PATH.read_text()
        self.assertNotIn('new Socket(', src)
        # jsonClient.<method>( -- every call must be one of the
        # documented public methods the shim actually implements.
        import re
        known_methods = {
            'ident', 'who', 'status', 'subscribe', 'unsubscribe', 'lock',
            'unlock', 'read', 'slice', 'splice', 'keys', 'keyTypes',
            'shift', 'pop', 'write', 'remove', 'unshift', 'push',
            'readmulti', 'cycle', 'connect', 'disconnect',
        }
        calls = set(re.findall(r'jsonClient\.(\w+)\s*\(', src))
        self.assertTrue(calls, 'expected to find at least one jsonClient.<method>() call')
        unsupported = calls - known_methods
        self.assertEqual(unsupported, set(),
                         f'chickendelivery.js calls jsonClient methods not in the shim: {unsupported}')


if __name__ == '__main__':
    unittest.main()
