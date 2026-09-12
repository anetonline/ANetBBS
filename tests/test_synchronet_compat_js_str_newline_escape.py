"""Regression test for a real gap found in a security/performance audit
of anetbbs/games/synchronet_compat.py: `_js_str()` (used by
write_compat_script() to embed user-controlled fields -- display_name,
location, birthdate -- into the generated compat script's JS string
literals) escaped backslashes and quote characters but NOT raw CR/LF.

A raw, unescaped newline is syntactically illegal inside a JS
single-quoted string literal. `display_name` has no charset
restriction at registration (web/profile.py) or in the admin user
editor (web/admin.py) -- unlike `username`, which does -- so a user
setting a display name containing an embedded newline broke the
generated script with a JS SyntaxError, failing that user's own
Synchronet-compat door launches. Same injection class already fixed
for dropfile.py's and node_paths.py's own `_u()` helpers, just not
brought into line here.

Verified two ways: node --check on the actual generated script (the
real consumer), and a check that the offending raw byte is gone.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Same optional-external-tool convention as
# test_synchronet_compat_missing_globals.py's own _HAVE_NODE guard --
# node isn't (and shouldn't need to be) a dependency of this Python
# project; the CI Docker image in particular doesn't have it, so the
# node --check test below must skip rather than fail there, matching
# every other node-dependent test in this file's own package. The
# other test in this file (test_no_raw_newline_lands_inside_the_alias_literal)
# needs no such guard -- it verifies the real fix at the byte level with
# no external tool at all, and always runs.
_NODE_PATH = os.environ.get('NODEJS_PATH', '/usr/bin/node')
_HAVE_NODE = os.path.isfile(_NODE_PATH)


class JsStrNewlineEscapeTests(unittest.TestCase):
    def _write_script(self, display_name):
        from anetbbs.games.synchronet_compat import write_compat_script
        game = SimpleNamespace(
            synchronet_exec_dir='/tmp',
            synchronet_script_path='/tmp/fake_door.js',
        )
        user = {'username': 'tester', 'display_name': display_name,
                'id': 1, 'is_admin': False, 'login_count': 0}
        compat_path = write_compat_script(game, user, node_number=1)
        self.addCleanup(lambda: os.path.isfile(compat_path) and os.unlink(compat_path))
        return compat_path

    @unittest.skipUnless(_HAVE_NODE, 'requires a real Node.js binary')
    def test_display_name_with_embedded_newline_produces_valid_js(self):
        compat_path = self._write_script('Line1\nLine2')
        result = subprocess.run([_NODE_PATH, '--check', compat_path],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(
            result.returncode, 0,
            f'generated compat script has a JS syntax error with an '
            f'embedded-newline display_name:\n{result.stderr}')

    def test_no_raw_newline_lands_inside_the_alias_literal(self):
        compat_path = self._write_script('Line1\nLine2')
        with open(compat_path) as f:
            src = f.read()
        # The raw two-line sequence must not appear verbatim (would mean
        # an unescaped literal newline landed inside the JS string) --
        # it must show up as the escaped \n sequence instead.
        self.assertNotIn('Line1\nLine2', src)
        self.assertIn('Line1\\nLine2', src)


if __name__ == '__main__':
    unittest.main()
