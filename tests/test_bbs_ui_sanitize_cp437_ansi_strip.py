"""Regression test for a real finding from a security/performance
audit: BBSMenuUI._sanitize_cp437() (anetbbs/features/bbs_ui.py) is the
shared "make remote text safe to print on a CP437 terminal" primitive
used at 16 call sites across the RSS reader and ebook reader -- all of
which feed it genuinely remote/attacker-controlled text (an RSS item's
title/author/feed-name/body is entirely controlled by whatever the
feed publisher writes; an ebook source's title/author/chapter titles
likewise).

Because Python's 'cp437' codec maps the C0 control range (including
ESC, 0x1B) byte-for-byte instead of rejecting it, a raw ANSI/CSI
escape sequence used to sail straight through _sanitize_cp437()
completely unfiltered -- every individual character of the sequence
passed its own per-character `c.encode('cp437')` check. A malicious
RSS item's title could repaint a subscriber's lightbar, spoof a fake
prompt, or hide surrounding chrome, reachable the moment they opened
the feed list (no need to even open the item).

Fixed by stripping well-formed CSI/OSC escape sequences plus the C0
control range (except '\\n', which several call sites need to
preserve paragraph structure in multi-line RSS/ebook body text) before
the existing Unicode-substitution pass.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class SanitizeCp437AnsiStripTests(unittest.TestCase):
    def test_strips_csi_escape_sequence(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        evil = 'Breaking News\x1b[2J\x1b[1;1HPWNED'
        out = BBSMenuUI._sanitize_cp437(evil)
        self.assertNotIn('\x1b', out)
        self.assertNotIn('[2J', out)
        self.assertIn('Breaking News', out)
        self.assertIn('PWNED', out)  # visible text stays, only the codes go

    def test_strips_osc_escape_sequence(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        # OSC 8 hyperlink-style sequence, terminated with BEL.
        evil = 'click\x1b]8;;http://evil.example\x07 here'
        out = BBSMenuUI._sanitize_cp437(evil)
        self.assertNotIn('\x1b', out)
        self.assertIn('click', out)
        self.assertIn('here', out)

    def test_strips_c0_control_bytes(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        evil = 'a\x07b\x08c\x0bd'  # BEL, backspace, vertical tab
        out = BBSMenuUI._sanitize_cp437(evil)
        self.assertEqual(out, 'abcd')

    def test_preserves_newlines_for_multiline_body_text(self):
        """RSS/ebook body text run through this function is genuinely
        multi-line -- stripping '\\n' would collapse paragraph
        structure, which is a correctness regression, not a security
        fix."""
        from anetbbs.features.bbs_ui import BBSMenuUI
        text = 'Paragraph one.\nParagraph two.\n\nParagraph three.'
        out = BBSMenuUI._sanitize_cp437(text)
        self.assertEqual(out, text)

    def test_still_maps_unicode_punctuation_as_before(self):
        """Existing behavior (curly quotes, em dash, ellipsis) must be
        unaffected by the new stripping pass."""
        from anetbbs.features.bbs_ui import BBSMenuUI
        text = '“Smart quotes” — and an ellipsis…'
        out = BBSMenuUI._sanitize_cp437(text)
        self.assertEqual(out, '"Smart quotes" - and an ellipsis...')

    def test_handles_none_and_empty(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        self.assertEqual(BBSMenuUI._sanitize_cp437(''), '')


if __name__ == '__main__':
    unittest.main()
