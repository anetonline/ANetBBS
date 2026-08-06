"""Regression tests for the AFK warning + matrix-rain screensaver
feature (Jerry's ask, mirroring a real Mystic Pascal script,
rcsafk.mps): after AFK_WARNING_SECONDS of no keystrokes, warn with a
live countdown; if nobody responds, run a screensaver; a keystroke at
either stage cancels/dismisses it and is consumed (not treated as the
caller's real next input).

Implementation lives in anetbbs/core/session.py
(_read_byte_maybe_spinning/_run_afk_sequence/_AFKInterrupted) and
anetbbs/features/afk_screensaver.py (MatrixRain, pure frame logic).

Reuses the exact test-double infrastructure already established in
test_cursor_style_spinning.py (_FakeWriter/_QueueReader/_SlowReader/
_make_session) for the async session-level tests.
"""
import asyncio
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession, CarrierLost
from anetbbs.features.afk_screensaver import MatrixRain


class _FakeWriter:
    def __init__(self, peer=('1.2.3.4', 1234)):
        self._peer = peer
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return self._peer if key == 'peername' else None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass


class _QueueReader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class _SlowReader:
    """Blocks until `delay` seconds have elapsed since the FIRST read()
    call (an absolute deadline, not a per-call relative sleep -- the
    caller wraps each read in asyncio.wait_for(), which cancels on
    timeout, so a naive relative sleep would never complete under
    repeated cancellation), then drains `chunks` with no further delay.
    Exact copy of test_cursor_style_spinning.py's own test double."""
    def __init__(self, chunks, delay=0.0):
        self._chunks = list(chunks)
        self._delay = delay
        self._deadline = None

    async def read(self, n=1):
        loop = asyncio.get_event_loop()
        if self._deadline is None:
            self._deadline = loop.time() + self._delay
        while loop.time() < self._deadline:
            await asyncio.sleep(min(self._deadline - loop.time(), 0.005))
        if self._chunks:
            return self._chunks.pop(0)
        return b''


def _make_session(reader, afk_warning_seconds=0, idle_timeout=0):
    writer = _FakeWriter()
    session = BBSSession(reader, writer, config={})
    session.user = {'id': 1}
    session.afk_warning_seconds = afk_warning_seconds
    session.idle_timeout = idle_timeout
    session.window_size = (80, 24)
    return session, writer


class MatrixRainTests(unittest.TestCase):
    """Pure frame-state logic -- no I/O, deterministic with a seeded
    random.Random."""

    def test_step_is_deterministic_with_a_seeded_rng(self):
        rain_a = MatrixRain(20, 10, rng=random.Random(42))
        rain_b = MatrixRain(20, 10, rng=random.Random(42))
        for _ in range(15):
            rain_a.step()
            rain_b.step()
        self.assertEqual(rain_a.frame_lines(), rain_b.frame_lines())

    def test_frame_lines_are_within_bounds(self):
        rain = MatrixRain(10, 8, rng=random.Random(1))
        for _ in range(50):
            rain.step()
            for line in rain.frame_lines():
                m = __import__('re').match(r'\x1b\[(\d+);(\d+)H', line)
                self.assertIsNotNone(m)
                row, col = int(m.group(1)), int(m.group(2))
                self.assertTrue(1 <= row <= 8)
                self.assertTrue(1 <= col <= 10)

    def test_eventually_produces_visible_drops(self):
        rain = MatrixRain(20, 10, rng=random.Random(7))
        saw_output = False
        for _ in range(40):
            rain.step()
            if rain.frame_lines():
                saw_output = True
                break
        self.assertTrue(saw_output, 'expected at least one frame with visible drops')

    def test_degenerate_sizes_do_not_crash(self):
        rain = MatrixRain(0, 0, rng=random.Random(3))
        for _ in range(10):
            rain.step()
            rain.frame_lines()  # must not raise


class AFKWarningStageCancelTests(unittest.TestCase):
    def test_keypress_during_warning_cancels_and_real_next_key_is_returned(self):
        """The idle threshold is crossed, the warning countdown starts,
        a keystroke arrives mid-countdown -- must be consumed (NOT
        returned as read_key()'s answer) and the caller's genuine next
        keystroke, sent afterward, must be what actually comes back."""
        import anetbbs.core.session as session_mod
        # First byte ('M') arrives after crossing the AFK threshold --
        # dismisses the warning. Second byte ('N') is the real answer.
        reader = _SlowReader([b'M', b'N'], delay=0.06)
        session, writer = _make_session(reader, afk_warning_seconds=0.03)
        with patch.object(session_mod, '_AFK_TICK_SECONDS', 0.01), \
             patch.object(session_mod, '_AFK_WARNING_COUNTDOWN_SECONDS', 20):
            result = asyncio.run(session.read_key())
        self.assertEqual(result, 'N')
        out = bytes(writer.written)
        self.assertIn(b'idle a while', out)
        self.assertIn(b'Welcome back', out)

    def test_afk_disabled_never_triggers(self):
        """afk_warning_seconds=0 (the default) must be a complete no-op
        -- baseline regression safety."""
        reader = _SlowReader([b'M'], delay=0.05)
        session, writer = _make_session(reader, afk_warning_seconds=0)
        result = asyncio.run(session.read_key())
        self.assertEqual(result, 'M')
        self.assertNotIn(b'idle a while', bytes(writer.written))


class AFKScreensaverStageTests(unittest.TestCase):
    def test_warning_countdown_expires_into_screensaver_then_dismissed(self):
        """No keystroke during the (patched-tiny) warning countdown --
        the screensaver stage must start (matrix-rain frames written),
        and a later keystroke dismisses it the same consumed-key way."""
        import anetbbs.core.session as session_mod
        reader = _SlowReader([b'M', b'N'], delay=0.08)
        session, writer = _make_session(reader, afk_warning_seconds=0.02)
        with patch.object(session_mod, '_AFK_TICK_SECONDS', 0.01), \
             patch.object(session_mod, '_AFK_WARNING_COUNTDOWN_SECONDS', 2), \
             patch.object(session_mod, '_AFK_FRAME_INTERVAL_SECONDS', 0.005):
            result = asyncio.run(session.read_key())
        self.assertEqual(result, 'N')
        out = bytes(writer.written)
        self.assertIn(b'idle a while', out)   # warning stage ran
        # Screensaver stage: at least one matrix-rain cursor-position
        # escape sequence must have been written.
        self.assertRegex(out, rb'\x1b\[\d+;\d+H')
        self.assertIn(b'Welcome back', out)

    def test_hard_idle_timeout_still_disconnects_during_screensaver(self):
        """If the sysop also has IDLE_TIMEOUT_SECONDS set and nobody
        EVER comes back (not even to dismiss the screensaver), the
        existing hard disconnect must still fire -- no new hangup logic
        needed, this reuses read_raw's own idle-timeout handling."""
        import anetbbs.core.session as session_mod
        reader = _SlowReader([], delay=5.0)  # never yields anything
        session, _writer = _make_session(
            reader, afk_warning_seconds=0.02, idle_timeout=0.08)
        with patch.object(session_mod, '_AFK_TICK_SECONDS', 0.01), \
             patch.object(session_mod, '_AFK_WARNING_COUNTDOWN_SECONDS', 1), \
             patch.object(session_mod, '_AFK_FRAME_INTERVAL_SECONDS', 0.005):
            with self.assertRaises(CarrierLost):
                asyncio.run(session.read_raw(1, allow_afk=True))


class ReadLineAFKBufferRedrawTests(unittest.TestCase):
    def test_partial_line_buffer_is_redrawn_after_afk_interruption(self):
        """Unlike read_key, read_line owns real partially-typed state
        -- after an AFK interruption mid-line, the already-typed text
        must be visibly redrawn (the buffer itself was never touched,
        only the screen), not just the bare prompt."""
        import anetbbs.core.session as session_mod
        # 'h','i' typed, then a long idle gap crossing the AFK
        # threshold, then 'M' dismisses it, then '\r' finishes the line.
        reader = _QueueReader([b'h', b'i'])
        session, writer = _make_session(reader, afk_warning_seconds=0.02)

        # Splice in a slow-then-resolving reader for the gap: swap the
        # session's reader after the first two chars are consumed.
        class _TwoPhaseReader:
            def __init__(self):
                self._phase1 = _QueueReader([b'h', b'i'])
                self._phase2 = _SlowReader([b'M', b'\r'], delay=0.05)
                self._n = 0

            async def read(self, n=1):
                self._n += 1
                if self._n <= 2:
                    return await self._phase1.read(n)
                return await self._phase2.read(n)

        session.reader = _TwoPhaseReader()
        with patch.object(session_mod, '_AFK_TICK_SECONDS', 0.01), \
             patch.object(session_mod, '_AFK_WARNING_COUNTDOWN_SECONDS', 20):
            result = asyncio.run(session.read_line())
        self.assertEqual(result, 'hi')
        out = bytes(writer.written)
        self.assertIn(b'Welcome back', out)
        # The buffer ('hi') must appear again AFTER the "Welcome back"
        # message -- i.e. it was genuinely redrawn post-interruption,
        # not just present from the original typing.
        idx = out.index(b'Welcome back')
        self.assertIn(b'hi', out[idx:])


class NonOptedInReadRawUnaffectedTests(unittest.TestCase):
    """The real risk found auditing every read_raw() call site: door
    games/bridges/editors call it directly with their own timeout/retry
    loops that would misinterpret an AFK interruption as "the game
    ended". Confirms the allow_afk opt-in actually protects them."""

    def test_plain_read_raw_never_triggers_afk_even_past_the_threshold(self):
        import anetbbs.core.session as session_mod
        reader = _SlowReader([b'M'], delay=0.05)
        session, writer = _make_session(reader, afk_warning_seconds=0.01)
        with patch.object(session_mod, '_AFK_TICK_SECONDS', 0.005):
            result = asyncio.run(session.read_raw(1))  # allow_afk defaults False
        self.assertEqual(result, b'M')
        self.assertNotIn(b'idle a while', bytes(writer.written))

    def test_plain_read_byte_maybe_spinning_never_triggers_afk(self):
        import anetbbs.core.session as session_mod
        reader = _SlowReader([b'M'], delay=0.05)
        session, writer = _make_session(reader, afk_warning_seconds=0.01)
        with patch.object(session_mod, '_AFK_TICK_SECONDS', 0.005):
            result = asyncio.run(session._read_byte_maybe_spinning(1))
        self.assertEqual(result, b'M')
        self.assertNotIn(b'idle a while', bytes(writer.written))


if __name__ == '__main__':
    unittest.main()
