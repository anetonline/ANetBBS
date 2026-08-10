"""Direct unit tests for anetbbs/features/mrc_chat.py's module-level
_word_wrap() helper -- shared by both the ANSI split-screen client and
PetsciiMRCChat/AsciiMRCChat's plain-scroll _emit().

Regression for a real bug found live on the Pi at 40 columns: a
multi-line MOTD/banner from the MRC bridge arrives as ONE string with
its own embedded '\\n' line breaks (intentional formatting -- separate
lines, blank lines between paragraphs). The tokenizer only charged a
whitespace token 1 column against the width budget, but the terminal
itself resets to column 0 at the embedded '\\n' -- so the algorithm's
internal column count and the real cursor position diverged, and
whatever word came right after the embedded newline in the source text
ended up stranded alone at the left margin on the next physical row
(e.g. "at", "!list", "or", a URL -- all seen in the actual screenshots).
Fixed by treating '\\n'/'\\r\\n' in the input as hard breaks, word-wrapped
independently, before the normal width-based reflow ever runs.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import _word_wrap


class WordWrapEmbeddedNewlineTests(unittest.TestCase):
    def test_short_text_with_no_newline_is_unaffected(self):
        self.assertEqual(_word_wrap('hi Sting!', 40), ['hi Sting!'])

    def test_embedded_newline_becomes_a_real_line_break_not_a_stray_word(self):
        # This is the exact shape of bug seen live: two short "lines" of
        # source text joined by a literal '\n', each well under width on
        # its own. Before the fix, the fast-path
        # `_visible_len(text) <= width` check counted the '\n' as just 1
        # more character, so if the WHOLE thing barely exceeded width the
        # tokenizer path ran and embedded the raw '\n' as if it were
        # ordinary reflowable whitespace.
        out = _word_wrap('for MRC Users at\ntelnet.holdfastbbs.ca:2002 (Game I)', 40)
        self.assertEqual(out, ['for MRC Users at',
                                'telnet.holdfastbbs.ca:2002 (Game I)'])
        for line in out:
            self.assertNotIn('\n', line, f'a returned line must never itself contain a raw newline: {line!r}')

    def test_blank_line_paragraph_break_is_preserved(self):
        out = _word_wrap('Try the new MRC helpers, type\n!list\n\nWeb Dashboard', 20)
        # "!list" must land on its own clean line, never appended after
        # a wrapped fragment of the previous sentence.
        self.assertIn('!list', out)
        self.assertFalse(any('type!list' in line for line in out),
                         f'"!list" got glued onto the previous line: {out!r}')

    def test_reproduces_the_full_banner_from_the_pi_screenshot_at_40_cols(self):
        banner = (
            'Join TRADEWARS 2002 for MRC Users at\n'
            'telnet.holdfastbbs.ca:2002 (Game I)\n'
            '\n'
            '30k sectors and 10k turns daily\n'
            '\n'
            'Try the new MRC helpers, type !list\n'
            '\n'
            'Web Dashboard can be found at:\n'
            'https://www.relaychat.net\n'
            'or\n'
            'https://status-xx-multi.relaychat.net\n'
            'xx = Region (NA, EU, AU)'
        )
        out = _word_wrap(banner, 40)
        joined = '\r\n'.join(out)
        # Every real word from the source must appear intact, and every
        # physical line respects the 40-column width.
        for word in ('Join', 'TRADEWARS', 'Users', 'at', 'telnet.holdfastbbs.ca:2002',
                     '(Game', 'I)', '30k', 'sectors', 'daily', 'Try', '!list',
                     'Web', 'Dashboard', 'https://www.relaychat.net', 'or',
                     'https://status-xx-multi.relaychat.net', 'xx', 'Region'):
            self.assertIn(word, joined, f'{word!r} missing or mangled in wrapped output')
        for line in out:
            self.assertLessEqual(len(line), 40, f'line exceeds 40 columns: {line!r}')
        # "at" must never be stranded alone with nothing before it on its
        # own physical line unless the source itself put it there -- the
        # real bug produced isolated single-word lines like "at" that
        # were supposed to be the tail of the previous sentence.
        self.assertNotIn('at', out, "'at' must stay attached to \"...MRC Users at\", "
                          'not appear as its own isolated line')

    def test_width_le_zero_still_returns_original_text_unsplit(self):
        self.assertEqual(_word_wrap('a\nb', 0), ['a\nb'])


if __name__ == '__main__':
    unittest.main()
