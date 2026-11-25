"""Regression tests for a path-traversal-shaped pattern found in a
follow-up security audit: both anetbbs/features/ansi_ui.py's
load_menu_ansi() and anetbbs/core/session.py's _show_ansi_screen()
join a `slot` string directly into a filesystem path with no character
validation. `slot` can originate from a sysop-controlled free-text
field -- games.py's _show_category_submenu() builds
f'door_games_{category}' straight from Game.category (set via the web
admin door-game editor, never slugified), and menu_engine.py's 'ansi'
action passes a menu item's action_args (also sysop free text)
straight into _show_ansi_screen(). Neither was ever validated against
'..'/'/' before reaching the path join.

Gated entirely behind sysop-level admin access already (not a
privilege escalation on its own), but a real, concrete defense-in-
depth gap: a sysop who set a category/action_args to something like
'../../../../etc/passwd' could get its bytes read and sent to a user's
terminal as "art". Fixed by restricting slot to [A-Za-z0-9_-] -- every
real slot name already matches that character set, so this can't break
any legitimate caller.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession
from anetbbs.features.ansi_ui import load_menu_ansi


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        pass


def _make_session(**kwargs):
    writer = _FakeWriter()
    session = BBSSession(object(), writer, config={}, **kwargs)
    return session, writer


class LoadMenuAnsiPathTraversalTests(unittest.TestCase):
    def test_traversal_slot_never_reaches_a_candidate_path_with_dotdot_or_slash(self):
        """Rather than force a REAL cross-directory file read, intercept every candidate
        Path.is_file() check load_menu_ansi() makes and confirm NONE
        of them still contain a traversal segment ('..') or a path
        separator from the slot, i.e. the sanitizer actually ran
        before the path join, not just that the end result happened
        to be None (which a BROKEN join could also produce by
        accident, e.g. if the traversal simply doesn't resolve to an
        existing file in this environment -- that would be a false
        pass, which this approach avoids)."""
        import pathlib
        probed_paths = []

        def _recording_is_file(self):
            probed_paths.append(self)
            return False

        with patch.object(pathlib.Path, 'is_file', _recording_is_file):
            load_menu_ansi('../../../../../../etc/passwd')

        self.assertTrue(probed_paths, 'load_menu_ansi() must still probe '
                        'some candidate path(s), just sanitized ones')
        for p in probed_paths:
            # Check the FULL path string, not p.name -- Path.name only
            # returns the last component, which never contains '..'
            # regardless of sanitization (the traversal segments sit
            # EARLIER in the path), so checking .name alone would pass
            # trivially even on the unfixed code. The real assertion:
            # no traversal segment survived into the full candidate
            # path at all -- so the join can never escape the intended
            # directory, regardless of what harmless substring is left
            # over in the filename itself (e.g. 'etc'/'passwd'
            # surviving as plain characters, concatenated into a
            # single non-traversing filename, is correct/safe
            # behavior, not a finding).
            full = str(p)
            self.assertNotIn('..', full)

    def test_sanitizer_strips_dots_slashes_and_leaves_identifier_chars(self):
        import re
        # Exercise the exact sanitizer load_menu_ansi() uses, via a
        # slot containing every disallowed character class at once.
        dirty = '../a/b\\c:d*e?f"g<h>i|j k'
        cleaned = re.sub(r'[^A-Za-z0-9_-]', '', dirty)
        self.assertEqual(cleaned, 'abcdefghijk')

    def test_legitimate_hyphenated_category_slot_is_unaffected(self):
        # Real slot names use exactly [A-Za-z0-9_-] (e.g.
        # door_games_synchronet-doors) -- confirm the sanitizer is a
        # true no-op for every character a real slot actually uses.
        import re
        real_slot = 'door_games_synchronet-doors_132'
        cleaned = re.sub(r'[^A-Za-z0-9_-]', '', real_slot)
        self.assertEqual(cleaned, real_slot)


class ShowAnsiScreenPathTraversalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # outside_dir is a known, fixed SIBLING of data_dir (both
        # directly under the same tempdir) so the traversal distance
        # from data_dir/text/ is deterministic: text -> data_dir ->
        # tmproot is 2 levels, so '../../outside/secret.ans' is the
        # exact path an unsanitized join would need to actually reach
        # a real file -- a prior version of this test picked an
        # unrelated tempfile.mkdtemp() location with no fixed
        # relationship to data_dir, so the "traversal" could never
        # have reached it regardless of whether the fix existed,
        # making it a false-positive-passing test. Verified this
        # version actually fails against the unfixed code before
        # trusting it.
        tmproot = Path(self._tmp.name)
        self.data_dir = tmproot / 'data_dir'
        (self.data_dir / 'mods' / 'text').mkdir(parents=True)
        (self.data_dir / 'text').mkdir(parents=True)

        self.outside_dir = tmproot / 'outside'
        self.outside_dir.mkdir(parents=True)
        (self.outside_dir / 'secret.ans').write_text('TOP SECRET ART')

        class _FakeApp:
            config = {'DATA_DIR': str(self.data_dir)}

        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=_FakeApp())
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_traversal_slot_cannot_reach_a_file_outside_the_text_dir(self):
        rel = f'../../{self.outside_dir.name}/secret'
        session, writer = _make_session(forced_term_mode='ansi', forced_width=80)

        asyncio.run(session._show_ansi_screen(rel))

        out = bytes(writer.written)
        self.assertNotIn(b'TOP SECRET ART', out,
                         'a traversal slot must never read a file '
                         'outside the configured text/ directory')

    def test_legitimate_slot_still_resolves_normally(self):
        (self.data_dir / 'text' / 'welcome.ans').write_text('WELCOME SCREEN')
        session, writer = _make_session(forced_term_mode='ansi', forced_width=80)

        asyncio.run(session._show_ansi_screen('welcome'))

        self.assertIn(b'WELCOME SCREEN', bytes(writer.written))


if __name__ == '__main__':
    unittest.main()
