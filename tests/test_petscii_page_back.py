"""Regression tests for B=prev (go back one page), requested live after
the M=more pagination fix: once a reader paged forward, the only way
"back" was Q, which exits the whole screen rather than returning to the
previous page. Covers both _paginated_pick() (listing/picker screens)
and _paginate() (body-text readers -- messages, file listings, profile).
"""
import asyncio
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.written = []
        self._forced_width = 40

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    async def clear_screen(self):
        self.written.append('[CLR]')

    @property
    def petscii_width(self):
        return self._forced_width

    def transcript(self):
        return ''.join(self.written)


class PaginatedPickBackTests(unittest.TestCase):
    def test_b_option_only_offered_past_page_one(self):
        from anetbbs.features.petscii_ui import _paginated_pick, PAGE_LINES
        rows = list(range(5))  # fits on one page -- no B option expected
        session = _FakeSession(['Q'])
        asyncio.run(_paginated_pick(session, 'Title', rows,
                                    lambda i, r: f'row{r}', 'select'))
        self.assertNotIn('B=prev', session.transcript())

    def test_b_steps_back_to_the_previous_page(self):
        from anetbbs.features.petscii_ui import _paginated_pick, PAGE_LINES
        rows = list(range(PAGE_LINES + 5))  # 2 pages
        # M(advance to page 2) -> B(back to page 1) -> Q(quit)
        session = _FakeSession(['M', 'B', 'Q'])
        result = asyncio.run(_paginated_pick(session, 'Title', rows,
                                             lambda i, r: f'row{i}:{r}', 'select'))
        self.assertEqual(result, 'Q')
        txt = session.transcript()
        self.assertIn('B=prev', txt)
        # Page 1's row 1 content must appear AGAIN after B (not just once,
        # from the initial page-1 render before M was pressed).
        self.assertEqual(txt.count('row1:0'), 2,
                         'B must re-render page 1, not just leave the old '
                         'page-1 content on screen')

    def test_b_on_the_first_page_is_not_treated_as_page_back(self):
        from anetbbs.features.petscii_ui import _paginated_pick, PAGE_LINES
        rows = list(range(PAGE_LINES + 5))
        # On page 1, B isn't offered (not a valid page-back target) --
        # typing it anyway isn't a crash, it's just handled the same as
        # any other unrecognized command: returned as-is to the caller,
        # which decides what to do with it (same contract _pick() has for
        # any command string).
        session = _FakeSession(['B'])
        result = asyncio.run(_paginated_pick(session, 'Title', rows,
                                             lambda i, r: f'row{i}', 'select'))
        self.assertEqual(result, 'B')


class PaginateBackTests(unittest.TestCase):
    def test_b_option_only_offered_past_page_one(self):
        from anetbbs.features.petscii_ui import _paginate, PAGE_LINES
        lines = [f'line{i}' for i in range(5)]  # one page
        session = _FakeSession([])
        result = asyncio.run(_paginate(session, lines, 'Title'))
        self.assertTrue(result)
        self.assertNotIn('B=prev', session.transcript())

    def test_b_re_renders_the_previous_page_with_header(self):
        from anetbbs.features.petscii_ui import _paginate, PAGE_LINES
        lines = [f'line{i}' for i in range(PAGE_LINES * 3)]  # 3 pages
        # page1 -> ''(advance to page2) -> 'B'(back to page1) ->
        # ''(advance to page2 again) -> ''(advance to page3, the last --
        # returns immediately, no further prompt needed)
        session = _FakeSession(['', 'B', '', ''])
        result = asyncio.run(_paginate(session, lines, 'My Title'))
        self.assertTrue(result)
        txt = session.transcript()
        self.assertIn('B=prev', txt)
        self.assertEqual(txt.count('line0'), 2,
                         'B must redraw page 1 (line0) a second time')
        self.assertEqual(txt.count('My Title'), 5,
                         'header must redraw on every page render: page1, '
                         'page2, back-to-page1, page2 again, page3')

    def test_q_still_quits_early_with_header_variant(self):
        from anetbbs.features.petscii_ui import _paginate, PAGE_LINES
        lines = [f'line{i}' for i in range(PAGE_LINES * 2)]
        session = _FakeSession(['Q'])
        result = asyncio.run(_paginate(session, lines, 'Title'))
        self.assertFalse(result)
        self.assertNotIn(f'line{PAGE_LINES}', session.transcript())

    def test_no_header_arg_still_works_legacy_style(self):
        """Callers that don't pass header_title (none remain in
        petscii_ui.py itself, but direct callers/tests still can) must
        keep working -- no clear/redraw, just sequential printing."""
        from anetbbs.features.petscii_ui import _paginate, PAGE_LINES
        lines = [f'line{i}' for i in range(PAGE_LINES * 2)]
        session = _FakeSession(['B', ''])
        result = asyncio.run(_paginate(session, lines))
        self.assertTrue(result)
        self.assertNotIn('[CLR]', session.transcript())


if __name__ == '__main__':
    unittest.main()
