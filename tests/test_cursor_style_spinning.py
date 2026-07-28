"""Regression tests for the terminal cursor-style feature (FR from
Winzlo, 2026-07-28): a blinking cursor makes iOS/macOS zoom's "follow
keyboard focus" repeatedly recenter the screen, fighting anyone trying
to look elsewhere while connected -- confirmed reproducible across four
separate SSH clients (Terminator, PuTTY, WebSSH, ShellFish).

Two new User.cursor_style values, both implemented in
anetbbs/core/session.py's _read_byte_maybe_spinning():
  - 'steady': a one-time DECSCUSR (ESC[4 q) sent at login, asking the
    client for a non-blinking underline cursor. No ongoing server-side
    work needed once sent.
  - 'spinning': a Synchronet-style rotating |/-\\ glyph shown while
    genuinely idle waiting for the next keystroke. Confirmed via
    Synchronet's own BAJA scripting docs that their K_SPIN feature is a
    mode flag on the blocking input-read call itself (not a separate
    background task) -- built the same way here, so it "just works"
    everywhere read_key/read_key_arrow/read_line already get called,
    with zero extra state to track across screens, and can never keep
    spinning after a real keystroke arrives.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession


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
    """Feeds pre-queued byte chunks, one read() call at a time, with no
    real delay -- for the 'default never spins' and 'data immediately
    available' cases."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class _SlowReader:
    """Like _QueueReader, but read() doesn't return anything until
    `delay` seconds have genuinely elapsed since the FIRST call -- long
    enough (with a patched-down spin tick interval) for a few real spin
    ticks to fire first.

    Tracks an absolute deadline (computed once, on the first call)
    rather than a per-call relative sleep -- the spin loop wraps each
    read() in asyncio.wait_for(), which CANCELS the coroutine on
    timeout. A naive `await asyncio.sleep(self._delay)` would never
    actually complete under repeated cancellation (every tick would
    restart the same delay from zero, forever); an absolute deadline
    keeps counting down correctly across cancellations since it's set
    exactly once, the moment real waiting begins.
    """
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


def _make_session(reader, cursor_style=None, **kwargs):
    writer = _FakeWriter()
    session = BBSSession(reader, writer, config={}, **kwargs)
    if cursor_style is not None:
        session.user = {'id': 1, 'cursor_style': cursor_style}
    return session, writer


class DefaultCursorStyleTests(unittest.TestCase):
    def test_default_cursor_style_never_spins(self):
        """No spin-glyph bytes should ever be written when cursor_style
        is 'default' (or unset), even though the reader could support
        it -- baseline/guard against the feature leaking to everyone."""
        reader = _QueueReader([b'M'])
        session, writer = _make_session(reader, cursor_style='default')
        result = asyncio.run(session.read_key())
        self.assertEqual(result, 'M')
        for glyph in ('|', '/', '-', '\\'):
            self.assertNotIn(glyph.encode(), bytes(writer.written))

    def test_no_user_set_never_spins(self):
        """self.user is None before login -- must not crash or spin."""
        reader = _QueueReader([b'M'])
        session, writer = _make_session(reader, cursor_style=None)
        result = asyncio.run(session.read_key())
        self.assertEqual(result, 'M')
        for glyph in ('|', '/', '-', '\\'):
            self.assertNotIn(glyph.encode(), bytes(writer.written))


class SpinningCursorTests(unittest.TestCase):
    def test_spinning_with_data_immediately_available_returns_correctly(self):
        """Fast path: if a byte is already sitting there, spinning mode
        must return it immediately, no different from default mode."""
        reader = _QueueReader([b'M'])
        session, _writer = _make_session(reader, cursor_style='spinning')
        result = asyncio.run(session.read_key())
        self.assertEqual(result, 'M')

    def test_spinning_draws_glyph_while_genuinely_idle(self):
        """With a patched-down tick interval and a reader that delays
        several ticks' worth before yielding real input, at least one
        spin glyph must actually get drawn."""
        import anetbbs.core.session as session_mod
        reader = _SlowReader([b'M'], delay=0.09)
        session, writer = _make_session(reader, cursor_style='spinning')
        with patch.object(session_mod, '_SPIN_TICK_SECONDS', 0.02):
            result = asyncio.run(session.read_key())
        self.assertEqual(result, 'M')
        drawn = any(glyph.encode() in bytes(writer.written)
                    for glyph in ('|', '/', '-', '\\'))
        self.assertTrue(drawn, 'expected at least one spin glyph to be drawn')

    def test_bulk_read_never_spins_even_with_spinning_enabled(self):
        """Safety guard: n>1 reads (a hypothetical future bulk read --
        file transfers use their own raw reader calls today, never this
        helper) must never have spin-glyph bytes injected, since that
        could corrupt a binary stream."""
        import anetbbs.core.session as session_mod
        reader = _SlowReader([b'hello'], delay=0.09)
        session, writer = _make_session(reader, cursor_style='spinning')
        with patch.object(session_mod, '_SPIN_TICK_SECONDS', 0.02):
            result = asyncio.run(session._read_byte_maybe_spinning(5))
        self.assertEqual(result, b'hello')
        self.assertEqual(bytes(writer.written), b'',
                         'n>1 reads must never write spin-glyph bytes')


class ReadRawIdleTimeoutCompositionTests(unittest.TestCase):
    def test_idle_timeout_still_disconnects_with_spinning_enabled(self):
        """read_raw's existing idle-timeout disconnect must still fire
        (roughly on schedule) even when spinning is also enabled --
        short spin ticks must accrue toward the same overall deadline,
        not reset it every tick."""
        import anetbbs.core.session as session_mod
        from anetbbs.core.session import CarrierLost
        reader = _SlowReader([], delay=1.0)  # never actually yields data
        session, _writer = _make_session(reader, cursor_style='spinning')
        session.idle_timeout = 0.05
        with patch.object(session_mod, '_SPIN_TICK_SECONDS', 0.01):
            with self.assertRaises(CarrierLost):
                asyncio.run(session.read_raw(1))

    def test_idle_timeout_unaffected_when_not_spinning(self):
        """Baseline: idle_timeout disconnect already worked before this
        feature -- confirm it's unchanged for cursor_style='default'."""
        from anetbbs.core.session import CarrierLost
        reader = _SlowReader([], delay=1.0)
        session, _writer = _make_session(reader, cursor_style='default')
        session.idle_timeout = 0.05
        with self.assertRaises(CarrierLost):
            asyncio.run(session.read_raw(1))


class SteadyCursorLoginTests(unittest.TestCase):
    """_maybe_send_steady_cursor() is the exact method BBSSession.start()
    calls once, right after a successful login -- calling it directly
    here (rather than exercising the whole start() flow, which acquires
    multinode slots, opens NodeActivity rows, etc. and isn't practically
    unit-testable) tests the real production code, not a re-implemented
    copy of its logic."""

    def _make_bare_session(self, cursor_style):
        reader = _QueueReader([])
        writer = _FakeWriter()
        session = BBSSession(reader, writer, config={})
        session.user = {'id': 1, 'cursor_style': cursor_style} if cursor_style else None
        return session, writer

    def test_steady_preference_sends_decscusr(self):
        session, writer = self._make_bare_session('steady')
        asyncio.run(session._maybe_send_steady_cursor())
        self.assertIn(b'\x1b[4 q', bytes(writer.written))

    def test_default_preference_sends_nothing(self):
        session, writer = self._make_bare_session('default')
        asyncio.run(session._maybe_send_steady_cursor())
        self.assertEqual(bytes(writer.written), b'')

    def test_spinning_preference_sends_nothing_here(self):
        """Spinning has no login-time ANSI code of its own -- it's
        handled entirely by _read_byte_maybe_spinning per read call."""
        session, writer = self._make_bare_session('spinning')
        asyncio.run(session._maybe_send_steady_cursor())
        self.assertEqual(bytes(writer.written), b'')

    def test_no_user_set_sends_nothing(self):
        session, writer = self._make_bare_session(None)
        asyncio.run(session._maybe_send_steady_cursor())
        self.assertEqual(bytes(writer.written), b'')


if __name__ == '__main__':
    unittest.main()
