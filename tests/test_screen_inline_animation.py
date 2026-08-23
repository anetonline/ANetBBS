"""Regression tests for the inline '@ANIMSTART@...@FRAME@...@ANIMEND@'
screen-animation marker in anetbbs/core/session.py's _show_ansi_screen().

Real gap this closes: a sysop wanting a small looping flourish (e.g. a
waving mascot) embedded in a welcome screen had no way to do it -- screen
files are static blobs streamed once. The marker lets a screen author
embed N-line frames that get redrawn in place (cursor up N, reprint) with
a short pause between, using nothing but the existing '@CODE@' marker
convention already established by @PAUSE@.

Two things worth testing on purpose:
  - the marker must NOT use a '@NAME:value@' shape -- display_codes.py's
    _AT_PARAM_RE strips those (@BPS:19200@ etc.) BEFORE this code ever
    runs, which would silently delete a parameterized marker. Covered
    indirectly by asserting the literal marker text survives round-trip
    through the real apply_codes.apply() the same way @PAUSE@ does.
  - a frame's line count must be inferred correctly and cursor-up must
    fire before each frame, not just the first.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession
from anetbbs.features import display_codes


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


class PlayScreenAnimationTests(unittest.TestCase):
    def setUp(self):
        self._sleep_patcher = patch(
            'anetbbs.core.session.asyncio.sleep', new=AsyncMock())
        self.mock_sleep = self._sleep_patcher.start()
        self.addCleanup(self._sleep_patcher.stop)

    def test_marker_survives_apply_codes_unchanged(self):
        # The whole reason the marker avoids '@NAME:value@' shape.
        text = 'before @ANIMSTART@AAA\r\n@FRAME@BBB\r\n@ANIMEND@ after'
        out = display_codes.apply(text, bbs_name='X', sysop='Y', version='Z')
        self.assertEqual(out, text)

    def test_frames_played_in_order_with_cursor_up_between(self):
        session, writer = _make_session()
        marker = '@ANIMSTART@AAA\r\nBBB\r\n@FRAME@CCC\r\nDDD\r\n@ANIMEND@'
        asyncio.run(session._play_screen_animation(marker))

        out = bytes(writer.written).decode('latin-1')
        up = '\x1b[2A'
        self.assertEqual(
            out,
            up + 'AAA\r\nBBB\r\n' + up + 'CCC\r\nDDD\r\n')
        self.assertEqual(self.mock_sleep.await_count, 2)

    def test_single_frame_still_plays_once(self):
        session, writer = _make_session()
        marker = '@ANIMSTART@ONLY\r\n@ANIMEND@'
        asyncio.run(session._play_screen_animation(marker))
        self.assertEqual(bytes(writer.written).decode('latin-1'),
                          '\x1b[1AONLY\r\n')

    def test_empty_block_is_a_silent_noop(self):
        session, writer = _make_session()
        asyncio.run(session._play_screen_animation('@ANIMSTART@@ANIMEND@'))
        self.assertEqual(bytes(writer.written), b'')

    def test_frame_with_no_newline_is_a_silent_noop(self):
        # rows would infer to 0 -- must not divide-by-zero or loop forever.
        session, writer = _make_session()
        asyncio.run(session._play_screen_animation(
            '@ANIMSTART@no newline here@ANIMEND@'))
        self.assertEqual(bytes(writer.written), b'')


class ShowAnsiScreenAnimationIntegrationTests(unittest.TestCase):
    """Full path through _show_ansi_screen()'s split loop, not just the
    isolated player -- confirms @PAUSE@ and @ANIMSTART@ can coexist in one
    body and each gets routed to the right handler."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        (self.data_dir / 'mods' / 'text').mkdir(parents=True)

        class _FakeApp:
            config = {'DATA_DIR': str(self.data_dir)}

        self._app_patcher = patch(
            'anetbbs.features.bbs_ui._app', return_value=_FakeApp())
        self._app_patcher.start()
        self.addCleanup(self._app_patcher.stop)

        self._sleep_patcher = patch(
            'anetbbs.core.session.asyncio.sleep', new=AsyncMock())
        self._sleep_patcher.start()
        self.addCleanup(self._sleep_patcher.stop)

    def test_pause_and_animation_both_fire_in_one_screen(self):
        body = (
            'Hello@PAUSE@Wave now:\r\n'
            '@ANIMSTART@X\r\n@FRAME@Y\r\n@ANIMEND@'
            'The end'
        )
        (self.data_dir / 'mods' / 'text' / 'welcome.ans').write_text(body)

        session, writer = _make_session(forced_term_mode='ansi', forced_width=80)
        session.read_key = AsyncMock(return_value='')

        asyncio.run(session._show_ansi_screen('welcome'))

        session.read_key.assert_awaited_once()
        out = bytes(writer.written).decode('latin-1')
        self.assertIn('Hello', out)
        self.assertIn('[Press any key to continue]', out)
        self.assertIn('\x1b[1AX\r\n', out)
        self.assertIn('\x1b[1AY\r\n', out)
        self.assertIn('The end', out)
        # Animation must not have consumed a keypress.
        self.assertEqual(session.read_key.await_count, 1)


if __name__ == '__main__':
    unittest.main()
