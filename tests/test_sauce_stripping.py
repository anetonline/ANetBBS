"""Tests for SAUCE-trailer stripping (anetbbs/features/sauce.py's new
strip() function) and the file-based screen/menu-art loaders that now
use it.

Real report from Jerry: leaving a SAUCE record on an .ans file used as
a menu/screen override showed the raw SAUCE metadata as literal
garbage text on screen -- e.g. on the main menu. SAUCE stripping only
ever existed inline inside the ANSI Editor's own import route
(anetbbs/web/ansi_editor.py); every OTHER place that read a raw .ans
file straight from disk (main-menu art via menu_engine.py, and
welcome/goodbye/newuser/custom screens via core/session.py's
_load_display_screens) never stripped it at all. Fixed by extracting
the cut logic into a shared sauce.strip(), and calling it from all
three places.
"""
import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _build_sauce_record(title='Test Art', author='Tester', group='ANetBBS'):
    """A real, spec-shaped 128-byte SAUCE record -- same field layout
    ansi_editor.py's own _build_sauce() uses."""
    title_b = title[:35].ljust(35).encode('cp437')
    author_b = author[:20].ljust(20).encode('cp437')
    group_b = group[:20].ljust(20).encode('cp437')
    date_b = b'20260101'
    record = (
        b'SAUCE00' +
        title_b + author_b + group_b + date_b +
        struct.pack('<I', 0) +
        struct.pack('<BB', 1, 1) +
        struct.pack('<HH', 80, 25) +
        struct.pack('<HH', 0, 0) +
        b'\x00\x00' +
        (b' ' * 22)
    )
    return record[:128].ljust(128, b'\x00')


def _sauce_tagged_file(body_bytes, **sauce_kw):
    """A realistic on-disk .ans layout: body, then EOF/SUB byte, then
    the 128-byte SAUCE record."""
    return body_bytes + b'\x1a' + _build_sauce_record(**sauce_kw)


class SauceStripFunctionTests(unittest.TestCase):
    def test_strips_a_real_sauce_record(self):
        from anetbbs.features.sauce import strip
        body = b'\x1b[1;31mHello, world!\x1b[0m\r\n'
        data = _sauce_tagged_file(body, title='My Art')
        result = strip(data)
        self.assertEqual(result, body)
        self.assertNotIn(b'SAUCE00', result)
        self.assertNotIn(b'My Art', result)

    def test_noop_when_no_sauce_present(self):
        from anetbbs.features.sauce import strip
        body = b'\x1b[1;31mHello, world!\x1b[0m\r\n' * 5
        self.assertEqual(strip(body), body)

    def test_noop_on_short_data(self):
        from anetbbs.features.sauce import strip
        self.assertEqual(strip(b'short'), b'short')
        self.assertEqual(strip(b''), b'')

    def test_noop_on_data_that_merely_ends_in_something_sauce_like(self):
        """A file whose last 128 bytes don't actually start with the
        'SAUCE' magic (just happens to contain '\\x1a' somewhere) must
        be left completely alone -- strip() must not misfire on
        coincidental EOF bytes in ordinary CP437 art."""
        from anetbbs.features.sauce import strip
        body = b'ordinary art with an eof-looking byte \x1a in the middle\r\n' * 3
        self.assertEqual(strip(body), body)

    def test_multiple_calls_are_idempotent(self):
        """Stripping already-stripped content must be a no-op, not an
        error or further truncation -- matters because screen loaders
        may reasonably strip content that was already clean."""
        from anetbbs.features.sauce import strip
        body = b'clean content\r\n'
        once = strip(_sauce_tagged_file(body))
        twice = strip(once)
        self.assertEqual(once, body)
        self.assertEqual(twice, body)


class LoadDisplayScreensSauceTests(unittest.TestCase):
    """core/session.py's _load_display_screens() -- feeds welcome/
    goodbye/newuser/custom-slot screens."""

    def test_sauce_stripped_from_a_real_file_on_disk(self):
        import tempfile
        import pathlib as pl
        from anetbbs.core.session import _load_display_screens

        body = b'\x1b[1;32mWelcome to the BBS!\x1b[0m\r\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = pl.Path(tmp) / 'welcome.ans'
            path.write_bytes(_sauce_tagged_file(body, title='Welcome Screen'))
            result = _load_display_screens([path])

        self.assertIn('Welcome to the BBS!', result)
        self.assertNotIn('SAUCE00', result)
        self.assertNotIn('Welcome Screen', result)

    def test_concatenating_multiple_files_strips_each_independently(self):
        import tempfile
        import pathlib as pl
        from anetbbs.core.session import _load_display_screens

        with tempfile.TemporaryDirectory() as tmp:
            p1 = pl.Path(tmp) / 'part1.ans'
            p2 = pl.Path(tmp) / 'part2.ans'
            p1.write_bytes(_sauce_tagged_file(b'Part one\r\n', title='One'))
            p2.write_bytes(_sauce_tagged_file(b'Part two\r\n', title='Two'))
            result = _load_display_screens([p1, p2])

        self.assertIn('Part one', result)
        self.assertIn('Part two', result)
        self.assertNotIn('SAUCE00', result)
        self.assertNotIn('One', result.replace('Part one', ''))
        self.assertNotIn('Two', result.replace('Part two', ''))

    def test_file_with_no_sauce_is_unaffected(self):
        import tempfile
        import pathlib as pl
        from anetbbs.core.session import _load_display_screens

        with tempfile.TemporaryDirectory() as tmp:
            path = pl.Path(tmp) / 'plain.ans'
            path.write_bytes(b'Just plain content, no sauce.\r\n')
            result = _load_display_screens([path])

        self.assertEqual(result, 'Just plain content, no sauce.\r\n')


class MenuArtSauceTests(unittest.TestCase):
    """anetbbs/features/menu_engine.py's _load_menu_art_from_disk() --
    feeds the main menu (and any other data-driven menu's) file-based
    ANSI/ASCII art override. This is the exact path behind Jerry's live
    report ("on my main menu it shows the sauce data")."""

    def test_ansi_mode_strips_sauce(self):
        import tempfile
        from anetbbs.features.menu_engine import _load_menu_art_from_disk

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'main.ans')
            with open(path, 'wb') as f:
                f.write(_sauce_tagged_file(b'MAIN MENU ART\r\n', title='Main Menu'))
            screen, is_plain_text = _load_menu_art_from_disk(tmp, 'main', 'ansi')

        self.assertIn('MAIN MENU ART', screen)
        self.assertNotIn('SAUCE00', screen)
        self.assertNotIn('Main Menu', screen)
        self.assertFalse(is_plain_text)

    def test_wide_mode_prefers_132_variant_and_strips_sauce(self):
        import tempfile
        from anetbbs.features.menu_engine import _load_menu_art_from_disk

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'main132.ans'), 'wb') as f:
                f.write(_sauce_tagged_file(b'WIDE MENU ART\r\n', title='Wide'))
            with open(os.path.join(tmp, 'main.ans'), 'wb') as f:
                f.write(b'narrow fallback, should not be used\r\n')
            screen, _ = _load_menu_art_from_disk(tmp, 'main', 'wide')

        self.assertIn('WIDE MENU ART', screen)
        self.assertNotIn('SAUCE00', screen)
        self.assertNotIn('narrow fallback', screen)

    def test_ascii_mode_strips_sauce_and_reports_plain_text(self):
        import tempfile
        from anetbbs.features.menu_engine import _load_menu_art_from_disk

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'main.asc'), 'wb') as f:
                f.write(_sauce_tagged_file(b'ASCII MENU ART\r\n', title='Ascii'))
            screen, is_plain_text = _load_menu_art_from_disk(tmp, 'main', 'ascii')

        self.assertIn('ASCII MENU ART', screen)
        self.assertNotIn('SAUCE00', screen)
        self.assertTrue(is_plain_text)

    def test_no_override_file_returns_empty_for_db_fallback(self):
        import tempfile
        from anetbbs.features.menu_engine import _load_menu_art_from_disk

        with tempfile.TemporaryDirectory() as tmp:
            screen, is_plain_text = _load_menu_art_from_disk(tmp, 'nonexistent', 'ansi')

        self.assertEqual(screen, '')
        self.assertFalse(is_plain_text)


class AnsiEditorImportStillStripsSauceTests(unittest.TestCase):
    """Confirm the refactor of ansi_editor.py's import route (now
    calling the shared sauce.strip() instead of its own inline copy of
    the same logic) didn't change its own behavior."""

    def test_strip_matches_the_original_inline_cut_logic(self):
        from anetbbs.features.sauce import parse, strip

        body = b'\x1b[1;35mImported Art\x1b[0m\r\n'
        data = _sauce_tagged_file(body, title='Imported')

        sauce = parse(data)
        self.assertIsNotNone(sauce)
        self.assertEqual(sauce['title'], 'Imported')

        stripped = strip(data)
        self.assertEqual(stripped, body)


if __name__ == '__main__':
    unittest.main()
