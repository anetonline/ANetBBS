"""Regression tests for multi-variant ANSI display screens (welcome/goodbye/
newuser and any custom slot rendered via core/session.py's
_show_ansi_screen). See docs/04-ansi-screens.md.

Design: numbered variants (welcome.ans, welcome_2.ans, welcome_3.ans, ...)
are ALL shown together, in order, every login -- the classic Synchronet
logon1.ans/logon2.ans/... multi-screen convention. '_ran'-tagged variants
(welcome_ran.ans, welcome_2_ran.ans, ...) pick just ONE at random each
login instead."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import (
    _find_screen_variants,
    _find_random_screen_variants,
    _resolve_display_screens,
    _load_display_screens,
    _stock_screen,
)


class ScreenDisplayTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_no_file_returns_none(self):
        self.assertIsNone(_resolve_display_screens(self.dir, 'welcome.ans'))
        self.assertEqual(_find_screen_variants(self.dir, 'welcome.ans'), [])

    def test_single_file_returns_itself_as_one_item_list(self):
        (self.dir / 'welcome132.ans').write_text('v1')
        for _ in range(5):
            picked = _resolve_display_screens(self.dir, 'welcome132.ans')
            self.assertEqual([p.name for p in picked], ['welcome132.ans'])

    def test_multiple_variants_all_returned_together_every_call(self):
        # The key behavior change: NOT one-per-call rotation. Every call
        # returns the full ordered list, every time.
        (self.dir / 'welcome132.ans').write_text('v1')
        (self.dir / 'welcome132_2.ans').write_text('v2')
        (self.dir / 'welcome132_3.ans').write_text('v3')
        expected = ['welcome132.ans', 'welcome132_2.ans', 'welcome132_3.ans']
        for _ in range(5):
            picked = _resolve_display_screens(self.dir, 'welcome132.ans')
            self.assertEqual([p.name for p in picked], expected)

    def test_load_display_screens_concatenates_in_order(self):
        (self.dir / 'welcome.ans').write_text('AAA')
        (self.dir / 'welcome_2.ans').write_text('BBB')
        picked = _resolve_display_screens(self.dir, 'welcome.ans')
        joined = _load_display_screens(picked)
        self.assertEqual(joined, 'AAABBB')

    def test_no_state_file_is_ever_created(self):
        # There's no more persisted rotation counter at all -- every
        # login shows the same full sequence, so nothing needs to be
        # remembered between calls.
        (self.dir / 'goodbye.ans').write_text('a')
        (self.dir / 'goodbye_2.ans').write_text('b')
        for _ in range(5):
            _resolve_display_screens(self.dir, 'goodbye.ans')
        leftover_files = list(self.dir.glob('.*'))
        self.assertEqual(leftover_files, [])

    def test_non_contiguous_numbering_is_fine(self):
        (self.dir / 'welcome.ans').write_text('a')
        (self.dir / 'welcome_5.ans').write_text('b')
        variants = _find_screen_variants(self.dir, 'welcome.ans')
        self.assertEqual([p.name for p in variants],
                          ['welcome.ans', 'welcome_5.ans'])

    def test_variants_are_independent_per_screen(self):
        (self.dir / 'welcome.ans').write_text('a')
        (self.dir / 'welcome_2.ans').write_text('b')
        (self.dir / 'goodbye.ans').write_text('c')
        w = [p.name for p in _resolve_display_screens(self.dir, 'welcome.ans')]
        g = [p.name for p in _resolve_display_screens(self.dir, 'goodbye.ans')]
        self.assertEqual(w, ['welcome.ans', 'welcome_2.ans'])
        self.assertEqual(g, ['goodbye.ans'])

    def test_missing_directory_is_safe(self):
        ghost = self.dir / 'does-not-exist'
        self.assertIsNone(_resolve_display_screens(ghost, 'welcome.ans'))

    def test_random_naming_picks_one_not_the_whole_sequence(self):
        (self.dir / 'welcome_ran.ans').write_text('a')
        (self.dir / 'welcome_2_ran.ans').write_text('b')
        (self.dir / 'welcome_3_ran.ans').write_text('c')
        names = {p.name for p in _find_random_screen_variants(self.dir, 'welcome.ans')}
        self.assertEqual(names, {'welcome_ran.ans', 'welcome_2_ran.ans', 'welcome_3_ran.ans'})
        picks = set()
        for _ in range(60):
            picked = _resolve_display_screens(self.dir, 'welcome.ans')
            self.assertEqual(len(picked), 1,
                              "random mode must return exactly one screen, not the full sequence")
            picks.add(picked[0].name)
        # 60 random picks across 3 items should hit all 3 with overwhelming
        # probability.
        self.assertEqual(picks, names)

    def test_random_naming_takes_priority_over_sequential_when_both_exist(self):
        (self.dir / 'newuser.ans').write_text('seq-1')
        (self.dir / 'newuser_2.ans').write_text('seq-2')
        (self.dir / 'newuser_ran.ans').write_text('ran-1')
        picked = _resolve_display_screens(self.dir, 'newuser.ans')
        self.assertEqual([p.name for p in picked], ['newuser_ran.ans'])

    def test_single_random_file_returns_itself(self):
        (self.dir / 'goodbye_ran.ans').write_text('only one')
        for _ in range(5):
            picked = _resolve_display_screens(self.dir, 'goodbye.ans')
            self.assertEqual([p.name for p in picked], ['goodbye_ran.ans'])

    def test_no_random_files_falls_back_to_sequential(self):
        (self.dir / 'welcome.ans').write_text('a')
        (self.dir / 'welcome_2.ans').write_text('b')
        self.assertEqual(_find_random_screen_variants(self.dir, 'welcome.ans'), [])
        picked = _resolve_display_screens(self.dir, 'welcome.ans')
        self.assertEqual([p.name for p in picked], ['welcome.ans', 'welcome_2.ans'])

    def test_stock_screen_backward_compatible_for_single_variant(self):
        # Real bundled anetbbs/screens/welcome.ans exists with no numbered
        # siblings today -- must behave exactly as a plain single-file load.
        result = _stock_screen('welcome', 'wide')
        self.assertIsNotNone(result[0])
        self.assertIsInstance(result[0], str)


if __name__ == '__main__':
    unittest.main()
