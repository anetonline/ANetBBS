"""Regression tests for the second round of real Pi3 Enhanced Client
bugs Jerry found (2026-10-02): arrow-key navigation on the Login/New
User Registration/Exit lightbar menu still didn't work after the
client-side key-mapping fix, and MRC chat rendered as scrambled
garbage on entry.

Root cause for both: anetbbs/core/session.py's 'enhanced' write()
branch originally parsed each write() call as an independent one-shot
ANSI render (anetbbs.features.ansi_html._run_vt()) and sent either a
flat scrolling-text message or a one-shot art screen. Real ANSI UIs
don't work that way -- _login_lightbar_menu()'s _redraw_item() sends a
cursor-position escape (ESC[row;1H) plus ONE line's content, expecting
an in-place overwrite; MRC chat's status bar/frame/ticker do the same
at fixed rows, with the scrolling chat area living inside a DECSTBM
scroll region (ESC[top;bottomr). Parsing each write() in isolation
loses the cursor position and scroll-region state between calls, so a
partial redraw came out as a brand new appended/scrambled line instead
of an in-place update.

Fixed with a PERSISTENT per-session virtual-screen buffer
(_run_vt()'s new `state=` parameter, continued across every write()
call via session._enhanced_feed()) plus real DECSTBM scroll-region
support in _run_vt() itself. See test_enhanced_client_live_bugfixes.py
for the more granular write()-level tests; this file verifies the real
consumer functions end to end.
"""
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / 'tests'))

from _deadconn_fixtures import make_session, run  # noqa: E402

from anetbbs.features.ansi_html import _run_vt  # noqa: E402


class _ScriptedReader:
    """Feeds pre-loaded bytes one at a time, matching how
    read_key_arrow()/_read_byte_maybe_spinning() always call
    self.reader.read(1). Raises AssertionError (not a silent hang) if
    the script runs out -- a test bug (under-scripted input), not a
    real dead-connection scenario, so it shouldn't look like one."""

    def __init__(self, script: bytes):
        self._buf = bytearray(script)

    def exception(self):
        return None

    async def read(self, n=1):
        if not self._buf:
            raise AssertionError('_ScriptedReader exhausted -- script needs more input')
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk


class LoginLightbarMenuEnhancedModeTests(unittest.TestCase):
    """Drives the REAL anetbbs.core.session.BBSSession._login_lightbar_menu()
    -- not a synthetic reimplementation.

    Superseded design note: this function originally rendered an ANSI
    lightbar box for 'enhanced' mode too (Up/Down moves a highlight,
    Enter selects), fixed via the persistent-VT-buffer redesign so its
    cursor-positioned partial redraws rendered correctly. Jerry then
    asked (more than once) for real clickable buttons here instead of
    an ANSI box, matching the main menu's own graphical treatment --
    'enhanced' mode now sends a structured 'menu' message (same
    mechanism menu_engine.py's own 'enhanced' _draw_menu() branch
    already uses) and has no "current selection" concept at all: a
    click (synthesized server-side into the matching hotkey keystroke
    before it ever reaches read_key_arrow(), see
    _WSReaderAdapter.read() in enhanced_server.py) or a direct typed
    hotkey (1/2/3, L/N/E) picks an item; arrow keys/bare Enter are
    just ignored since there's nothing to navigate."""

    def test_direct_hotkey_selects_without_any_navigation(self):
        session, writer = make_session(_ScriptedReader(b'2'))
        session._forced_term_mode = 'enhanced'
        result = run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '2')

    def test_letter_hotkeys_still_work(self):
        session, writer = make_session(_ScriptedReader(b'N'))
        session._forced_term_mode = 'enhanced'
        result = run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '2')

    def test_esc_selects_exit_same_as_keyboard_terminals(self):
        session, writer = make_session(_ScriptedReader(b'\x1b'))
        session._forced_term_mode = 'enhanced'
        result = run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '3')

    def test_arrow_keys_and_bare_enter_are_ignored_not_crashed_on(self):
        """No "current selection" exists in the button UI -- arrows/
        Enter must be harmlessly ignored (looped past), not crash and
        not accidentally select something. A real hotkey after them
        must still work, proving the loop didn't get stuck either."""
        script = b'\x1b[B' + b'\x1b[A' + b'\r' + b'3'
        session, writer = make_session(_ScriptedReader(script))
        session._forced_term_mode = 'enhanced'
        result = run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '3')

    def test_sends_a_structured_menu_message_with_the_three_items(self):
        import json
        session, writer = make_session(_ScriptedReader(b'1'))
        session._forced_term_mode = 'enhanced'
        run(session._login_lightbar_menu('Test BBS'))

        msg = json.loads(bytes(writer.written).decode('utf-8'))
        self.assertEqual(msg['type'], 'menu')
        self.assertIn('Test BBS', msg['title'])
        hotkeys = {item['hotkey']: item['label'] for item in msg['items']}
        self.assertEqual(hotkeys, {
            '1': 'Login', '2': 'New User Registration', '3': 'Exit'})


class ScrollRegionTests(unittest.TestCase):
    """DECSTBM (ESC[top;bottomr) support added to ansi_html._run_vt()
    for MRC chat's fixed-status-bar-over-scrolling-chat-area layout --
    without it, content printed past the scroll region's bottom margin
    just grew cur_row forever instead of scrolling, and a fixed status
    bar drawn OUTSIDE the region (which real terminals never touch
    during an in-region scroll) would eventually scroll out of a
    sliding window along with it."""

    def test_content_within_region_scrolls_instead_of_growing_unbounded(self):
        # Region rows 2-4 (1-indexed ESC[2;4r -> 0-indexed rows 1-3).
        # Print 5 lines into a 3-row region -- the first two must
        # scroll off, leaving only the last three.
        text = '\x1b[2;4r\x1b[2;1Hline1\r\nline2\r\nline3\r\nline4\r\nline5'
        cells, max_row, state = _run_vt(text)
        all_chars_by_row = {}
        for (r, c), (ch, *_rest) in cells.items():
            all_chars_by_row.setdefault(r, []).append((c, ch))
        # Reconstruct each row's text to confirm line1/line2 are gone
        # and line3/line4/line5 remain, scrolled into rows 1-3.
        rendered_rows = {}
        for r, items in all_chars_by_row.items():
            rendered_rows[r] = ''.join(ch for _c, ch in sorted(items))
        combined = ' '.join(rendered_rows.values())
        self.assertNotIn('line1', combined)
        self.assertNotIn('line2', combined)
        self.assertIn('line3', combined)
        self.assertIn('line4', combined)
        self.assertIn('line5', combined)
        # Scrolled content must stay WITHIN the region (rows 1-3,
        # 0-indexed) -- cur_row must never have exceeded scroll_bottom.
        self.assertEqual(state['cur_row'], 3)

    def test_fixed_row_outside_region_survives_scrolling_inside_it(self):
        # A status bar at absolute row 1 (0-indexed row 0, OUTSIDE the
        # 2-4 region) must still be there after the region scrolls --
        # real terminal behavior: a scroll region never touches rows
        # outside it.
        text = ('\x1b[1;1HSTATUS BAR'
                '\x1b[2;4r\x1b[2;1Ha\r\nb\r\nc\r\nd\r\ne\r\nf')
        cells, max_row, state = _run_vt(text)
        row0 = ''.join(ch for (r, c), (ch, *_r) in cells.items() if r == 0)
        self.assertIn('S', row0)  # "STATUS BAR" survived

    def test_default_state_none_passed_is_fully_backward_compatible(self):
        """One-shot callers (to_html/to_ansi_lines, and any code that
        never passes state=) must behave exactly as before -- no
        scroll region in effect until ESC[...r is actually sent."""
        cells, max_row, state = _run_vt('line1\r\nline2\r\nline3')
        self.assertEqual(state['scroll_top'], 0)
        self.assertGreater(state['scroll_bottom'], 100)  # effectively unbounded


if __name__ == '__main__':
    unittest.main()
