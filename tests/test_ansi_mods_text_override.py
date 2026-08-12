"""Regression tests for the v1.0.36 correction: data/mods/text/ (and
data/mods/text/menus/) as an ADDITIONAL, higher-priority override
location layered on top of the existing data/text/ (and
data/text/menus/) file-based screen overrides -- matching real
Synchronet's own unified mods/ tree (wiki.synchro.net/dir:mods), not
just the door/script-only scope the feature originally shipped with.

Covers both halves of the correction:
  - anetbbs/core/session.py's _show_ansi_screen() (lifecycle/custom
    ANSI screens: welcome, goodbye, newuser, custom slots)
  - anetbbs/features/ansi_ui.py's load_menu_ansi() (built-in terminal
    menu art overrides: chat, sysop tools, door games, etc.)

Neither directory's existence is required -- both are only consulted
if present, and data/text/ must keep working unchanged for anyone not
using data/mods/text/ yet.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession
from anetbbs.features.ansi_ui import load_menu_ansi


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        pass


def _make_session(**kwargs):
    writer = _FakeWriter()
    session = BBSSession(object(), writer, config={}, **kwargs)
    return session, writer


class ShowAnsiScreenModsTextOverrideTests(unittest.TestCase):
    """anetbbs/core/session.py's _show_ansi_screen()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        (self.data_dir / 'mods' / 'text').mkdir(parents=True)
        (self.data_dir / 'text').mkdir(parents=True)

        class _FakeApp:
            config = {'DATA_DIR': str(self.data_dir)}

        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=_FakeApp())
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _write(self, rel, text):
        path = self.data_dir / rel
        path.write_text(text)
        return path

    def test_mods_text_wins_over_text_when_both_exist(self):
        self._write('mods/text/welcome.ans', 'MODS OVERRIDE')
        self._write('text/welcome.ans', 'OLDER TEXT OVERRIDE')

        session, writer = _make_session(forced_term_mode='ansi', forced_width=80)
        asyncio.run(session._show_ansi_screen('welcome'))

        out = bytes(writer.written)
        self.assertIn(b'MODS OVERRIDE', out)
        self.assertNotIn(b'OLDER TEXT OVERRIDE', out)

    def test_text_still_works_when_mods_text_absent(self):
        self._write('text/welcome.ans', 'OLDER TEXT OVERRIDE')

        session, writer = _make_session(forced_term_mode='ansi', forced_width=80)
        asyncio.run(session._show_ansi_screen('welcome'))

        self.assertIn(b'OLDER TEXT OVERRIDE', bytes(writer.written))

    def test_neither_present_is_a_silent_noop_or_stock_fallback(self):
        # No file in either location for a made-up slot with no bundled
        # stock screen and no DB row -- must not raise.
        session, writer = _make_session(forced_term_mode='ansi', forced_width=80)
        asyncio.run(session._show_ansi_screen('totally_made_up_slot_xyz'))
        self.assertEqual(bytes(writer.written), b'')


class LoadMenuAnsiModsTextMenusOverrideTests(unittest.TestCase):
    """anetbbs/features/ansi_ui.py's load_menu_ansi().

    load_menu_ansi() computes its data dir from its OWN module
    `__file__` (Path(__file__).resolve().parent.parent.parent / 'data'),
    not from Flask config -- so rather than writing into the real,
    live repo data/ directory (which a running production BBS reads
    from), a fake module `__file__` pointed at a throwaway temp
    directory laid out with the same anetbbs/features/ansi_ui.py
    nesting depth redirects that computation harmlessly.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        fake_repo_root = Path(self._tmp.name)
        fake_module_path = fake_repo_root / 'anetbbs' / 'features' / 'ansi_ui.py'
        fake_module_path.parent.mkdir(parents=True)
        fake_module_path.touch()

        self.data_dir = fake_repo_root / 'data'
        (self.data_dir / 'mods' / 'text' / 'menus').mkdir(parents=True)
        (self.data_dir / 'text' / 'menus').mkdir(parents=True)

        import anetbbs.features.ansi_ui as ansi_ui_mod
        self._patcher = patch.object(ansi_ui_mod, '__file__', str(fake_module_path))
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _write(self, rel, data):
        path = self.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def _load(self, slot, mode='ansi'):
        return load_menu_ansi(slot, mode)

    def test_mods_text_menus_wins_over_text_menus(self):
        self._write('mods/text/menus/chat.ans', b'MODS MENU ART')
        self._write('text/menus/chat.ans', b'OLDER MENU ART')

        result = self._load('chat')
        self.assertEqual(result, b'MODS MENU ART')

    def test_text_menus_still_works_when_mods_text_menus_absent(self):
        self._write('text/menus/chat.ans', b'OLDER MENU ART')

        result = self._load('chat')
        self.assertEqual(result, b'OLDER MENU ART')

    def test_absent_from_both_falls_back_to_none_or_stock(self):
        result = self._load('totally_made_up_menu_slot_xyz')
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
