"""Regression tests for terminal color compatibility, added 2026-07-04.

The sysop was told by users that several BBS terminal clients --
MagiTerm, NetRunner, and PuTTY (in ANSI-BBS emulation mode) -- showed
no color at all on some screens (graffiti wall, file areas, message
boards) while others (the main menu) rendered fine. SyncTerm showed
color everywhere.

Root cause: the main menu is rendered from a static .ans art file
(anetbbs/screens/menus/main.ans) using the classic ANSI.SYS convention
for bright colors -- bold attribute + base color code (e.g. \\x1b[1;36m
for bright cyan). Several Python UI modules instead hardcoded bright
colors using the bare aixterm/xterm-256 extended range 90-97 directly
(e.g. \\x1b[96m), with no bold attribute. That range isn't recognized
by MagiTerm, NetRunner, or PuTTY -- the escape sequence is silently
dropped, so no color shows at all. SyncTerm happens to support both
conventions, which is why the bug never showed up there.

Fixed by converting every bare \\x1b[9Xm / \\x1b[10Xm (aixterm bright
fg/bg) literal in the affected modules to the classic \\x1b[1;3Xm
(bold + base color) form, matching what the shipped .ans art already
does. This test scans the SOURCE of each affected module for the bug
pattern, so a future edit that reintroduces a bare bright code (easy
to do by habit, since 90-97 "looks like" a normal color code and works
fine when tested against SyncTerm) gets caught automatically.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_ANETBBS_ROOT = Path(__file__).resolve().parents[1] / 'anetbbs'

# Bare aixterm bright codes: \x1b[9Xm or \x1b[10Xm with NO preceding
# bold/other parameter (i.e. not part of a \x1b[1;3Xm-style combo).
_BARE_BRIGHT_RE = re.compile(r'\\x1b\[(?:9[0-7]|10[0-7])m')

# Files known to intentionally render pre-parsed/incoming SGR codes
# for a DIFFERENT purpose (HTML/CSS color mapping, not raw terminal
# emission) -- these are correctly out of scope, see
# anetbbs/web/render_msg.py and anetbbs/features/ansi_html.py.
_EXCLUDED = {
    'web/render_msg.py',
    'features/ansi_html.py',
}

# The specific modules this bug was found and fixed in. Listed
# explicitly (rather than scanning the whole tree) so this test fails
# loudly and specifically if any of THESE regress, without being a
# broad lint rule for the entire codebase (some future new module
# might have a deliberate reason to use 90-97, e.g. a web-only path).
_CHECKED_MODULES = [
    'features/ansi_ui.py',
    'features/menu_engine.py',
    'core/session.py',
    'features/games.py',
    'features/bbs_ui.py',
    'features/wall.py',
    'features/mrc_chat.py',
    'features/anetirc_door.py',
    'features/anedit.py',
    'installer/wizard.py',
]


class NoBareAixtermBrightCodesTests(unittest.TestCase):
    def test_no_bare_bright_codes_in_fixed_modules(self):
        for rel_path in _CHECKED_MODULES:
            path = _ANETBBS_ROOT / rel_path
            self.assertTrue(path.is_file(), f'{rel_path} not found')
            content = path.read_text(encoding='utf-8')
            matches = _BARE_BRIGHT_RE.findall(content)
            self.assertEqual(
                matches, [],
                f'{rel_path} has bare aixterm bright SGR code(s) {matches} -- '
                f'MagiTerm/NetRunner/PuTTY silently drop these. Use bold + '
                f'base color instead (e.g. \\x1b[1;36m, not \\x1b[96m).')

    def test_ansi_ui_fg_dict_uses_bold_plus_base_for_bright_colors(self):
        from anetbbs.features.ansi_ui import FG
        for name, code in FG.items():
            if name == 'dim':
                continue  # intentionally the non-bright base color (37)
            self.assertIn(
                '1;3', code,
                f"FG['{name}'] = {code!r} should be bold+base ('...1;3X...')")

    def test_wall_pipe_fg_bright_entries_use_bold_plus_base(self):
        from anetbbs.features.wall import _PIPE_FG
        for code in ('08', '09', '10', '11', '12', '13', '14', '15'):
            self.assertTrue(
                _PIPE_FG[code].startswith('1;'),
                f"_PIPE_FG['{code}'] = {_PIPE_FG[code]!r} should start with '1;'")

    def test_mrc_chat_pipe_colors_bright_entries_use_bold_plus_base(self):
        # anetbbs.features.mrc_chat <-> anetbbs.core.session circular
        # import -- importing anetbbs.core first resolves it, same as
        # elsewhere in this project's tests.
        import anetbbs.core  # noqa: F401
        from anetbbs.features.mrc_chat import _PIPE_COLORS
        for code in ('08', '09', '10', '11', '12', '13', '14', '15'):
            self.assertTrue(
                _PIPE_COLORS[code].startswith('1;'),
                f"_PIPE_COLORS['{code}'] = {_PIPE_COLORS[code]!r} should start with '1;'")


if __name__ == '__main__':
    unittest.main()
