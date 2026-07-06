"""Regression test for the ANView/_ViewerScreen.draw_text() signature bug.

ANEdit._redraw() (shared/inherited by ANView, the read-only message
viewer used by read_echo_area/read_message/etc.) always calls
self._scr.draw_text(..., self._visible_misspellings()) -- 6 positional
args -- because the base editor's own _Screen.draw_text() gained a
`misspell` parameter when spell-check was added. _ViewerScreen's own
draw_text() override (used only by the read-only viewer, where spell-
checking makes no sense) was never updated to match, so every read-only
message view crashed with a TypeError the instant _redraw() ran. Caught
live on the production server: reading any echomail message failed with
"Menu action failed (see server log)" / "_ViewerScreen.draw_text() takes
6 positional arguments but 7 were given".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anetbbs.features.anedit import ANView, _ViewerScreen, _THEMES


class FakeSession:
    def __init__(self):
        self.window_size = (80, 24)
        self.encoding = 'cp437'
        self.written = []

    async def write(self, text):
        self.written.append(text)


class ANViewDrawTextTests(unittest.IsolatedAsyncioTestCase):
    def test_viewer_screen_draw_text_accepts_misspell_arg(self):
        # Direct signature check: this is exactly the call _redraw() makes.
        scr = _ViewerScreen(_THEMES[0], width=80)
        result = scr.draw_text(["hello", "world"], 0, None, 0, 0, {})
        self.assertIsInstance(result, str)

    def test_viewer_screen_draw_text_misspell_defaults_to_none(self):
        scr = _ViewerScreen(_THEMES[0], width=80)
        # Callers other than _redraw() might not pass it at all.
        result = scr.draw_text(["hello"], 0, None, 0, 0)
        self.assertIsInstance(result, str)

    async def test_anview_redraw_does_not_raise(self):
        # End-to-end: this is the actual call chain that crashed on the
        # live server (read_echo_area -> launch_aneview -> viewer.run()
        # -> _redraw()).
        session = FakeSession()
        viewer = ANView(session, ["line one", "line two", "line three"],
                         subject="Test Subject")
        await viewer._redraw()


if __name__ == '__main__':
    unittest.main()
