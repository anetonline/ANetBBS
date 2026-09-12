"""Regression test for a real bug found in a security/performance audit
of anetbbs/games/mystic_compat.py: WriteXY() called `_compat.gotoxy()`,
`_compat.textcolor()`, `_compat.textbackground()`, and `_compat.rwrite()`
directly, instead of going through the module-level helper functions of
the same names that every OTHER function in this file uses (the
standard `if _compat is not None: _compat.foo(...) else: <subprocess
fallback>` pattern).

`_compat` is populated only by `_init_compat()`, which is never called
anywhere in the codebase, and `MysticCompat(...)` is never instantiated
anywhere either (confirmed via `grep -rn "_init_compat\\|MysticCompat("`
across the whole repo) -- door_runner.py's real door_mystic launch path
(`_build_mystic_python_command`) always runs Mystic Python scripts as a
standalone subprocess (`runpy.run_path()` in a forked `python`
process), never in-process. So `_compat` is always None for every real
invocation, and WriteXY crashed with `AttributeError: 'NoneType' object
has no attribute 'gotoxy'` the instant any door script called it (this
module's own docstring names dopewars.mps and RDQ2 as real callers).

Fixed by routing WriteXY through the module-level gotoxy/textcolor/
textbackground/rwrite helpers, which already have the correct
subprocess-mode ANSI fallback every sibling function uses.
"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MysticCompatWriteXYSubprocessModeTests(unittest.TestCase):
    def setUp(self):
        from anetbbs.games import mystic_compat as mc
        self.mc = mc
        # Real invocation condition: _compat is never populated on the
        # actual door_mystic subprocess launch path -- confirm that's
        # genuinely the state under test, not an artifact of import/
        # test ordering leaving a stale value from some other test.
        self.assertIsNone(mc._compat)
        self._orig_stdout = mc._sys.stdout
        self.buf = io.StringIO()
        mc._sys.stdout = self.buf
        self.addCleanup(self._restore_stdout)

    def _restore_stdout(self):
        self.mc._sys.stdout = self._orig_stdout

    def test_writexy_does_not_raise_with_compat_none(self):
        try:
            self.mc.WriteXY(5, 10, 0x1F, 'hello')
        except AttributeError as exc:
            self.fail(f'WriteXY raised with _compat=None: {exc}')

    def test_writexy_emits_expected_ansi_sequence_and_text(self):
        self.mc.WriteXY(5, 10, 0x1F, 'hello')
        out = self.buf.getvalue()
        self.assertIn('\x1b[10;5H', out)  # gotoxy(x=5, y=10)
        self.assertIn('\x1b[97m', out)    # textcolor(0x1F & 0x0F = 15 -> bright white)
        self.assertIn('\x1b[44m', out)    # textbackground((0x1F >> 4) & 0x07 = 1 -> blue bg)
        self.assertIn('hello', out)       # rwrite(text)


if __name__ == '__main__':
    unittest.main()
