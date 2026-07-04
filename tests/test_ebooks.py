"""Regression tests for the ebook reader's text-processing logic
(anetbbs/web/ebooks.py), added 2026-07-04.

Covers the two real bugs caught while building this feature against
live Project Gutenberg data:

1. The chapter-splitting regex also matches the Table of Contents
   listing that opens almost every Gutenberg text (each ToC line looks
   identical to a real chapter heading, e.g. "CHAPTER I.  Down the
   Rabbit-Hole") -- confirmed against a real fetch of Alice's
   Adventures in Wonderland (Gutenberg #11), which produced 25 "chapters"
   instead of the real 12 before the fix.
2. PG boilerplate header/footer stripping needs to handle the standard
   "*** START/END OF THE PROJECT GUTENBERG EBOOK ***" markers.

These are pure-function tests (no Flask app context, no network) --
the actual HTTP routes were verified manually against a live dev
server and the real Gutendex/gutenberg.org services instead, since
mocking the two real bugs above away would have hidden them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest
from anetbbs.web.ebooks import (
    _clean_gutenberg_text, _split_chapters, _get_text_format_url,
)

# A trimmed-down but structurally faithful stand-in for a real Gutenberg
# plain-text file: PG header boilerplate, a title page, a Contents
# listing (the ToC-pollution trap), then real chapters with substantial
# body text, then the PG footer boilerplate.
_FAKE_GUTENBERG_TEXT = (
    "The Project Gutenberg eBook of Test Book\n"
    "\n"
    "*** START OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***\n"
    "\n"
    "Test Book\n"
    "by A. Author\n"
    "\n"
    "Contents\n"
    "\n"
    " CHAPTER I.     The Beginning\n"
    " CHAPTER II.    The Middle\n"
    " CHAPTER III.   The End\n"
    "\n"
    "CHAPTER I.\n"
    "\n" + ("Once upon a time, in a real chapter with real body text. " * 40) + "\n\n"
    "CHAPTER II.\n"
    "\n" + ("The middle of the story continues on at some length here. " * 40) + "\n\n"
    "CHAPTER III.\n"
    "\n" + ("And so the story reaches its conclusion at long last here. " * 40) + "\n\n"
    "*** END OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***\n"
    "\n"
    "Some license boilerplate text that should not appear in the book.\n"
)


class CleanGutenbergTextTests(unittest.TestCase):
    def test_strips_header_and_footer_boilerplate(self):
        cleaned = _clean_gutenberg_text(_FAKE_GUTENBERG_TEXT)
        self.assertNotIn('Project Gutenberg eBook of Test Book', cleaned)
        self.assertNotIn('license boilerplate', cleaned)
        self.assertIn('Test Book', cleaned)  # the title page itself is real content
        self.assertIn('Once upon a time', cleaned)

    def test_leaves_text_unchanged_if_no_markers_found(self):
        plain = "Just some text with no Gutenberg markers at all."
        self.assertEqual(_clean_gutenberg_text(plain), plain)


class SplitChaptersTests(unittest.TestCase):
    def test_toc_listing_is_not_mistaken_for_chapter_headings(self):
        cleaned = _clean_gutenberg_text(_FAKE_GUTENBERG_TEXT)
        chapters = _split_chapters(cleaned)
        titles = [c['title'] for c in chapters]
        # Must find exactly the 3 real chapters (+ Front Matter for the
        # title page/ToC preamble) -- NOT 6 (3 ToC entries + 3 real).
        self.assertEqual(len(chapters), 4, f'got {titles!r}')
        self.assertEqual(titles[0], 'Front Matter')
        self.assertEqual(titles[1:], ['CHAPTER I.', 'CHAPTER II.', 'CHAPTER III.'])

    def test_chapter_offsets_point_at_the_real_headings_not_the_toc(self):
        cleaned = _clean_gutenberg_text(_FAKE_GUTENBERG_TEXT)
        chapters = _split_chapters(cleaned)
        # The real "CHAPTER I." heading should be well past the ToC
        # block, not at the tiny offset where the ToC's own "CHAPTER I."
        # line sits.
        toc_line_offset = cleaned.index(' CHAPTER I.     The Beginning')
        real_heading_offset = chapters[1]['start_offset']
        self.assertGreater(real_heading_offset, toc_line_offset + 50)

    def test_falls_back_to_full_text_when_no_chapter_headings_exist(self):
        chapters = _split_chapters("Just a poem or short piece with no chapters.")
        self.assertEqual(chapters, [{'title': 'Full Text', 'start_offset': 0}])

    def test_falls_back_gracefully_when_every_match_looks_like_a_toc_entry(self):
        # A pathological case: nothing but tightly-packed chapter-looking
        # lines (e.g. a book that really is just a table of contents).
        # Should not crash, and should return at least one chapter.
        text = "CHAPTER I. A\nCHAPTER II. B\nCHAPTER III. C\n"
        chapters = _split_chapters(text)
        self.assertGreaterEqual(len(chapters), 1)


class GetTextFormatUrlTests(unittest.TestCase):
    def test_picks_a_text_plain_format_when_present(self):
        formats = {
            'application/epub+zip': 'https://example.org/book.epub',
            'text/plain; charset=us-ascii': 'https://example.org/book.txt',
            'image/jpeg': 'https://example.org/cover.jpg',
        }
        self.assertEqual(_get_text_format_url(formats), 'https://example.org/book.txt')

    def test_returns_none_when_no_text_plain_format_exists(self):
        formats = {'application/epub+zip': 'https://example.org/book.epub'}
        self.assertIsNone(_get_text_format_url(formats))

    def test_handles_empty_or_missing_formats(self):
        self.assertIsNone(_get_text_format_url({}))
        self.assertIsNone(_get_text_format_url(None))


if __name__ == '__main__':
    unittest.main()
