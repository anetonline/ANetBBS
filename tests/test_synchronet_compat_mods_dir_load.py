"""JS-side coverage for the /data/mods/ override wiring (see
test_door_runner_mods_override.py's module docstring for the full
context/history of this feature).

This covers the half that lives inside the compat shim's OWN load()
resolver: js.mods_dir now exists (it didn't before) and is checked
FIRST, before js.load_path_list/exec_dir/stubs_dir, for anything a
door loads via load("somefile.js") at runtime -- not just the door's
own top-level entry-point script (that's door_runner.py's
_apply_mods_override, covered separately, since the entry-point file
never goes through load() at all).

Runs the real generated compat-shim JS under plain Node (no PTY --
we're only exercising load()'s own resolution logic, not any
interactive I/O), by cutting the header off the same way
test_synchronet_compat_missing_globals.py's own
test_known_door_fixes_survive_the_e4x_polyfill does. Results are
written to a file via the header's own `_fs` reference rather than
printed with console.log -- the header redefines console.log (and
attempts a real-terminal stty raw-mode setup that fails outside a PTY)
before reaching any appended driver code, exactly the same reason the
existing missing-globals test avoids console.log too.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_NODE_PATH = os.environ.get('NODEJS_PATH', '/usr/bin/node')
_HAVE_NODE = os.path.isfile(_NODE_PATH)


@unittest.skipUnless(_HAVE_NODE, 'requires a real Node.js binary')
class ModsDirLoadResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _compat_header(self, exec_dir=None):
        """Builds a real compat script for a throwaway stub door and
        returns just its header (everything before the door-execution
        section) -- the reusable prefix every driver in this file
        appends its own probe code to."""
        from anetbbs.games.synchronet_compat import write_compat_script
        exec_dir = exec_dir or os.path.join(self._tmpdir.name, 'exec')
        os.makedirs(exec_dir, exist_ok=True)
        script_path = os.path.join(exec_dir, 'door.js')
        if not os.path.isfile(script_path):
            with open(script_path, 'w') as f:
                f.write('// stub entry point, never actually run by this test\n')
        game = SimpleNamespace(synchronet_exec_dir=exec_dir,
                               synchronet_script_path=script_path)
        compat_path = write_compat_script(game, user=None, node_number=1)
        self.addCleanup(lambda: os.unlink(compat_path)
                        if os.path.isfile(compat_path) else None)
        return open(compat_path).read().split(
            '// === Execute the actual game ===')[0]

    def _run_driver(self, header, probe_expr):
        """Appends a driver that writes the result of `probe_expr` to a
        result file (via the header's own `_fs`, since console.log is
        redefined by then) and returns the file's trimmed contents."""
        result_path = os.path.join(self._tmpdir.name, 'result.txt')
        result_path_js = json.dumps(result_path)
        driver = header + (
            "try {\n"
            "  var __result = String(" + probe_expr + ");\n"
            "  _fs.writeFileSync(" + result_path_js + ", __result);\n"
            "} catch (e) {\n"
            "  _fs.writeFileSync(" + result_path_js + ", 'ERROR: ' + e.message);\n"
            "}\n"
        )
        driver_path = os.path.join(self._tmpdir.name, 'driver.js')
        with open(driver_path, 'w') as f:
            f.write(driver)
        result = subprocess.run(
            [_NODE_PATH, driver_path], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(os.path.isfile(result_path),
                        f'driver never wrote a result file:\n{result.stderr}')
        out = open(result_path).read()
        self.assertFalse(out.startswith('ERROR:'), msg=out)
        return out

    def test_js_mods_dir_global_is_populated(self):
        """The most basic regression check: js.mods_dir must exist at
        all -- before this fix it was entirely absent from the js
        object, so load() had nothing to consult regardless of what
        the Python-side mods_dir variable computed."""
        header = self._compat_header()
        out = self._run_driver(header, 'typeof js.mods_dir')
        self.assertEqual(out, 'string')

    def test_mods_dir_resolves_under_data_directory(self):
        header = self._compat_header()
        out = self._run_driver(header, 'js.mods_dir')
        self.assertIn(os.path.join('data', 'mods'), out)

    def test_load_prefers_mods_override_over_exec_dir(self):
        """The real end-to-end proof: a same-named file dropped in the
        real (resolved) mods_dir must win over a same-named file
        sitting in exec_dir -- exec_dir is checked LATER in the same
        search chain, so this also proves ORDERING, not just that
        mods_dir is reachable at all."""
        exec_dir = os.path.join(self._tmpdir.name, 'exec')
        os.makedirs(exec_dir, exist_ok=True)
        stock_file = os.path.join(exec_dir, '__anetbbs_test_probe.js')
        with open(stock_file, 'w') as f:
            # load()'s bare single-arg form (used here, matching how a
            # real door typically calls it) does NOT return the loaded
            # script's completion value at all -- only the 2-arg
            # scope-form does (see load()'s own end-of-function
            # branching in synchronet_compat.py). vm.runInThisContext
            # DOES run the loaded code with globalThis as its scope
            # though, so a plain `var` assignment is directly
            # observable afterwards -- the reliable way to prove which
            # file actually got read.
            f.write("var __anetbbs_probe_result = 'stock';\n")

        header = self._compat_header(exec_dir=exec_dir)
        real_mods_dir = self._run_driver(header, 'js.mods_dir')
        os.makedirs(real_mods_dir, exist_ok=True)
        override_file = os.path.join(real_mods_dir, '__anetbbs_test_probe.js')
        with open(override_file, 'w') as f:
            f.write("var __anetbbs_probe_result = 'override';\n")
        self.addCleanup(lambda: os.unlink(override_file)
                        if os.path.isfile(override_file) else None)

        out = self._run_driver(
            header,
            "(load('__anetbbs_test_probe.js'), __anetbbs_probe_result)")
        self.assertEqual(out, 'override',
                         'load() must prefer the mods/ override over the '
                         'same-named file in exec_dir')


if __name__ == '__main__':
    unittest.main()
