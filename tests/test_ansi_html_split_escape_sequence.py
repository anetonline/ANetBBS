"""Regression test for a real live bug (Pi3 Enhanced Client test,
2026-10-02, a door game -- Blackjack): a PTY read chunk boundary split
one logical ANSI SGR escape sequence across two separate
BBSSession.write() calls (session.py's 'enhanced' branch feeds each
one into ansi_html._run_vt() via the new persistent `state=`
continuation). The original stateful design, on hitting the end of
`text` mid-sequence, gave up and skipped past only the ESC + '['
bytes, leaving the remaining digits/semicolons to render as literal
visible text -- confirmed live, a screenshot showed the literal string
"[1;37;40m" sitting in the middle of a "Enter wager (...)" prompt.

Fixed by stashing any incomplete escape sequence (including a bare
trailing ESC with nothing after it yet) in the returned state's
`pending` field and prepending it to the next _run_vt() call's text,
so a split sequence parses correctly as one continuous stream -- the
same way a real terminal's own parser handles chunked input.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.ansi_html import _run_vt


def _row_text(cells, row, width=80):
    return ''.join(
        (cells.get((row, c)) or (' ',))[0] for c in range(width)).rstrip()


class SplitEscapeSequenceTests(unittest.TestCase):
    def test_sgr_sequence_split_mid_params_does_not_leak_as_text(self):
        # "\x1b[1;37;40" in one call, "m" + the real content in the next
        # -- exactly the live shape (bold white-on-blue before "1400 Max").
        cells, max_row, state = _run_vt('Enter wager (\x1b[1;37;40')
        cells, max_row, state = _run_vt('m1400 Max', state=state)
        line = _row_text(cells, 0)
        self.assertEqual(line, 'Enter wager (1400 Max')
        self.assertNotIn('[1;37;40m', line)
        self.assertNotIn(';37;40', line)

    def test_split_sequence_still_applies_its_color(self):
        cells, max_row, state = _run_vt('\x1b[1;3')
        cells, max_row, state = _run_vt('1mRED', state=state)
        # col 0 ('R') must have picked up the completed SGR (bright red,
        # bold) -- proving the sequence was actually interpreted, not
        # just silently discarded without visible garbage either.
        ch, fg, bg, bold = cells[(0, 0)]
        self.assertEqual(ch, 'R')
        self.assertTrue(bold)

    def test_bare_trailing_esc_with_nothing_after_is_not_dropped_silently(self):
        # A lone ESC as the very last byte of a chunk -- the following
        # byte (which decides CSI-vs-something-else) hasn't arrived.
        cells, max_row, state = _run_vt('AB\x1b')
        self.assertEqual(state['pending'], '\x1b')
        cells, max_row, state = _run_vt('[33mC', state=state)
        line = _row_text(cells, 0)
        self.assertEqual(line, 'ABC')

    def test_normal_unsplit_sequences_still_work_unchanged(self):
        cells, max_row, state = _run_vt('\x1b[32mGREEN\x1b[0m plain')
        line = _row_text(cells, 0)
        self.assertEqual(line, 'GREEN plain')
        self.assertEqual(state['pending'], '')

    def test_one_shot_callers_without_state_drop_a_truncated_sequence_cleanly(self):
        """_to_html_vt()/to_ansi_lines() never pass state= and never
        feed a returned state back -- a genuinely truncated/malformed
        ANSI source (not a chunking artifact) must render whatever
        came before the broken sequence with no garbage text, not
        leak the partial escape as visible characters either."""
        cells, max_row, state = _run_vt('Hello \x1b[1;3')
        line = _row_text(cells, 0)
        self.assertEqual(line, 'Hello')
        self.assertNotIn('[1;3', line)

    def test_pathological_never_terminated_sequence_does_not_grow_pending_forever(self):
        cells, max_row, state = _run_vt('\x1b[' + ('1;' * 40))
        self.assertLessEqual(len(state['pending']), 32)


if __name__ == '__main__':
    unittest.main()
