"""Feature test for the terminal side of the taglines feature: ANEdit
calls a caller-supplied `tagline_picker` callback exactly once, at
actual send time (Ctrl+W/`/send`), and appends whatever text it
returns (or nothing, if it returns None/falsy). Reported live in two
stages: first that the terminal side never asked about a tagline at
all, then -- after a prompt was added before the editor opened -- that
"it should not ask you about a tag line until you send, when you send
it should bring up the tagline." This is why the picker is now a
deferred callback rather than a pre-fetched value: anedit.py has no
DB/Flask access (by design), so the actual pool lookup + browsable
picker UI lives in the caller (bbs_ui.py) and is only invoked when
ANEdit itself decides it's time to send.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anetbbs.features.anedit import ANEdit


class FakeSession:
    def __init__(self):
        self.window_size = (80, 24)
        self.encoding = 'cp437'
        self.written = []

    async def write(self, text):
        self.written.append(text)


class AneditTaglinePickerTests(unittest.IsolatedAsyncioTestCase):
    async def _run_and_send(self, editor):
        """Drive run() just enough to hit the 'done' return path without
        a real keyboard loop -- mirrors how a real Ctrl+W/`/send` sets
        editor.done = True and lets run()'s main loop exit naturally."""
        editor.done = True
        return await editor.run()

    async def test_picker_is_not_called_until_send(self):
        calls = []

        async def picker():
            calls.append(1)
            return "A witty remark."

        editor = ANEdit(FakeSession(), ["Hello there."], tagline_picker=picker)
        self.assertEqual(calls, [], 'must not call the picker just from constructing the editor')
        await self._run_and_send(editor)
        self.assertEqual(len(calls), 1, 'must call the picker exactly once, at send time')

    async def test_picked_tagline_is_appended_on_send(self):
        async def picker():
            return "A witty remark."

        editor = ANEdit(FakeSession(), ["Hello there."], tagline_picker=picker)
        result = await self._run_and_send(editor)
        self.assertIn("Hello there.", result)
        self.assertIn("-- ", result)
        self.assertIn("A witty remark.", result)

    async def test_no_append_when_picker_returns_none(self):
        async def picker():
            return None

        editor = ANEdit(FakeSession(), ["Hello there."], tagline_picker=picker)
        result = await self._run_and_send(editor)
        self.assertEqual(result, "Hello there.")

    async def test_no_picker_call_at_all_when_none_given(self):
        """No tagline_picker at all (e.g. the pool was empty and the
        caller never bothered constructing a closure) must not crash
        and must not append anything."""
        editor = ANEdit(FakeSession(), ["Hello there."], tagline_picker=None)
        result = await self._run_and_send(editor)
        self.assertEqual(result, "Hello there.")

    async def test_picker_not_called_on_abort(self):
        """Aborting (self.done stays False, self.aborted becomes True)
        must never invoke the picker -- only an actual send should."""
        calls = []

        async def picker():
            calls.append(1)
            return "Should not be used."

        editor = ANEdit(FakeSession(), ["Hello there."], tagline_picker=picker)
        editor.aborted = True  # done stays False
        result = await editor.run()
        self.assertIsNone(result)
        self.assertEqual(calls, [], 'abort must not trigger the tagline picker')


if __name__ == '__main__':
    unittest.main()
