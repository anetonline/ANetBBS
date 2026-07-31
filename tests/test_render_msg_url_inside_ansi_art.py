"""Regression test for a real bug found live: a sysop composed a CP437/
ANSI ad screen (bordered box, centered "https://bbs.a-net.fyi" line
inside it) and posting it corrupted everything at and after that line
-- the box border broke, and the row's intended white padding turned
into default-gray blanks with wrong spacing.

Root cause: _linkify() (anetbbs/web/render_msg.py) used to split the
decoded text into fragments around each matched URL and run the
VT-grid renderer (_vt_to_html/_run_vt) independently on each fragment.
_to_html_vt() always pads every row out to the full 80-column width
with default-color blanks when it reaches the end of its input
mid-row -- it has no way to know the row was cut short by a URL split
rather than genuinely ending there. Whatever row a URL landed in got
its trailing content (and the color that should have applied to it)
replaced with default-gray filler.

Fixed by rendering the full text as ONE continuous VT-grid pass, then
substituting URLs into the already-rendered HTML string afterward --
the grid renderer always sees the complete, uncut text, so a URL
landing mid-row can no longer corrupt that row's layout.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web.render_msg import render_msg_body, render_msg_body_rich


def _bordered_ansi_with_centered_url():
    """A minimal repro of the real bug shape: a bright-white-on-black
    bordered box with a URL centered inside one row, flanked by spaces
    in the SAME color on both sides -- exactly the shape that broke.

    Must include an actual block-shade character (e.g. a shading bar)
    SOMEWHERE before the URL line: to_html()'s dispatch to the
    grid-based VT renderer (_to_html_vt, the one with the padding bug)
    vs. the simpler streaming renderer is decided by _HAS_BLOCK_ART,
    which matches solid block/shade glyphs (█▄▀▌▍▎▏░▒▓) but NOT plain
    box-drawing line characters (║═╔╗╚╝ alone). A box border with no
    shading anywhere never reaches the buggy code path at all -- this
    is exactly why the real ad screen (which has a shading bar above
    its URL line) hit the bug while a border-only repro would not."""
    RESET = '\x1b[0m'
    top = '\x1b[1;96;40m' + '╔' + '═' * 40 + '╗' + RESET
    shading = '\x1b[34;40m' + '░▒▓█' * 10 + RESET
    url_line = ('\x1b[1;97;40m' + '║' + ' ' * 8 + 'https://bbs.a-net.fyi'
               + ' ' * 8 + '║' + RESET)
    bottom = '\x1b[1;96;40m' + '╚' + '═' * 40 + '╝' + RESET
    return '\r\n'.join([top, shading, url_line, bottom])


class UrlInsideAnsiArtTests(unittest.TestCase):
    def test_url_line_stays_one_continuous_span_not_split_by_default_gray(self):
        body = _bordered_ansi_with_centered_url()
        html = str(render_msg_body_rich(body))
        idx = html.index('href=')
        # The chars immediately BEFORE the link must be the single
        # continuous white-colored span carrying the box border and
        # left-padding -- not interrupted by a default-gray (#aaaaaa)
        # span, which is what happened when the row got cut mid-way
        # and the VT renderer padded the remainder with default filler.
        before = html[max(0, idx - 200):idx]
        last_white_open = before.rfind('color:#ffffff')
        self.assertNotEqual(last_white_open, -1,
                            'expected a white span before the link')
        after_white_open = before[last_white_open:]
        self.assertNotIn('color:#aaaaaa', after_white_open,
                         'a default-gray span leaked into the white-colored '
                         'row segment right before the URL -- the row got '
                         f'corrupted: {after_white_open!r}')

    def test_border_after_the_url_line_is_still_present_and_correctly_colored(self):
        body = _bordered_ansi_with_centered_url()
        html = str(render_msg_body_rich(body))
        self.assertIn('╚', html)
        self.assertIn('╝', html)
        bottom_idx = html.rindex('╚')
        preceding = html[max(0, bottom_idx - 200):bottom_idx]
        self.assertIn('#55ffff', preceding,
                      'bottom border lost its bright-cyan color styling')

    def test_link_href_and_text_are_correct(self):
        body = _bordered_ansi_with_centered_url()
        html = str(render_msg_body_rich(body))
        self.assertIn('href="https://bbs.a-net.fyi"', html)
        self.assertIn('>https://bbs.a-net.fyi</a>', html)

    def test_row_count_matches_source_line_count(self):
        body = _bordered_ansi_with_centered_url()
        html = str(render_msg_body_rich(body))
        # 4 source lines -> 3 <br> separators (no trailing <br> after
        # the last row in _to_html_vt's own convention).
        self.assertEqual(html.count('<br'), 3)

    def test_plain_render_msg_body_also_fixed_not_just_the_rich_variant(self):
        body = _bordered_ansi_with_centered_url()
        html = str(render_msg_body(body))
        idx = html.index('href=')
        before = html[max(0, idx - 200):idx]
        last_white_open = before.rfind('color:#ffffff')
        after_white_open = before[last_white_open:]
        self.assertNotIn('color:#aaaaaa', after_white_open)


if __name__ == '__main__':
    unittest.main()
