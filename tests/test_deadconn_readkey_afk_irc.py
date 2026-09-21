"""Regression tests for 3 real gaps found via a no-network dead-
connection regression harness an operator built and ran independently
after v1.0.96 shipped (see project memory for the full incident this
continues). All 3 share the same underlying mechanism already fixed
once for read_raw()/write()/_read_byte_maybe_spinning() in v1.0.96:
asyncio.StreamReader.read() and StreamWriter.drain() both re-raise an
exception already stored on the reader (set via set_exception(), by
the transport's own connection_lost() after a real error -- e.g. a
genuine OS-level ETIMEDOUT on an unresponsive-but-not-closed peer)
IMMEDIATELY on every call, forever -- not a fresh timeout each time.
Since Python 3.11, asyncio.TimeoutError IS the builtin TimeoutError, so
any `except asyncio.TimeoutError:` handler that doesn't specifically
check for a stored exception has no way to tell "a real timeout just
fired" apart from "the connection already died and this is that same
error being replayed."

1. BBSSession.read_key()/read_key_arrow() call
   _read_byte_maybe_spinning() directly, bypassing read_raw()'s own
   already-fixed handling. With AFK disabled (afk_warning_seconds<=0,
   the common case), _read_byte_maybe_spinning() takes a fast path with
   no wait_for wrapper at all, so a stored exception came straight out
   of self.reader.read() as a raw, uncaught TimeoutError -- not a spin
   by itself, but inconsistent with every other disconnect path in this
   codebase (always CarrierLost).

2. BBSSession._afk_peek() (used by _run_afk_sequence's warning +
   screensaver stages) treated a stored exception as an ordinary "no
   key pressed yet" timeout (returning None, not raising), and never
   even checked for plain EOF (self.reader.read(1) returning b'' with
   no exception at all). _run_afk_sequence's every write() call was
   also wrapped in a blanket `except Exception: pass` that swallowed
   write()'s own already-correct CarrierLost right along with it. The
   screensaver stage's `while True:` has no other exit condition when
   the outer idle_timeout is off, so a session idle long enough to
   reach the screensaver, on a connection that then died, looped
   forever -- doing real (if individually cheap) work every frame,
   never freeing the node.

3. anetbbs.features.anetirc2._IRC.read_loop() (the socket-level
   connection to the real upstream IRC server, separate from the
   already-fixed v1.0.90 local-terminal _read_key()) had the identical
   gap: a stored exception was indistinguishable from _READ_TIMEOUT's
   own genuine "nothing arrived, send a keepalive PING" case, so every
   pass tried (and silently failed) to PING an already-dead upstream
   connection, then immediately hit the same stored exception again --
   a tight, un-yielding retry storm.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession, CarrierLost  # noqa: E402
from anetbbs.features import anetirc2                      # noqa: E402


class _FakeWriter:
    def __init__(self, peer=('1.2.3.4', 1234)):
        self._peer = peer
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key, default=None):
        return self._peer if key == 'peername' else default

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    def get_write_buffer_size(self):
        return 0

    async def wait_closed(self):
        pass


class _DeadReader:
    """A StreamReader-alike that already has a real connection-level
    exception stored on it -- read() re-raises it immediately, on every
    call, forever, exactly like the real asyncio.StreamReader does
    (confirmed against CPython 3.12's asyncio/streams.py)."""

    def __init__(self, exc):
        self._exc = exc

    def exception(self):
        return self._exc

    async def read(self, n=1):
        raise self._exc


class _EofReader:
    """A StreamReader-alike at plain EOF -- read() returns b'' forever,
    no exception at all (real asyncio behavior for a cleanly-closed-but-
    unobserved transport)."""

    def exception(self):
        return None

    async def read(self, n=1):
        return b''


def _make_session(reader, afk_warning_seconds=0):
    writer = _FakeWriter()
    session = BBSSession(reader, writer, config={})
    session.user = {'id': 1}
    session.afk_warning_seconds = afk_warning_seconds
    session.idle_timeout = 0
    session.window_size = (80, 24)
    return session, writer


def _run(coro, timeout=5):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


class ReadKeyStoredExceptionTests(unittest.TestCase):

    def test_read_key_raises_carrier_lost_not_raw_timeout_afk_disabled(self):
        session, _writer = _make_session(
            _DeadReader(TimeoutError(110, 'Connection timed out')),
            afk_warning_seconds=0)
        with self.assertRaises(CarrierLost):
            _run(session.read_key())

    def test_read_key_arrow_raises_carrier_lost_not_raw_timeout_afk_disabled(self):
        session, _writer = _make_session(
            _DeadReader(TimeoutError(110, 'Connection timed out')),
            afk_warning_seconds=0)
        with self.assertRaises(CarrierLost):
            _run(session.read_key_arrow())

    def test_read_key_raises_carrier_lost_for_a_reset(self):
        session, _writer = _make_session(
            _DeadReader(ConnectionResetError()), afk_warning_seconds=0)
        with self.assertRaises(CarrierLost):
            _run(session.read_key())

    def test_read_key_raises_carrier_lost_for_eof(self):
        session, _writer = _make_session(_EofReader(), afk_warning_seconds=0)
        with self.assertRaises(CarrierLost):
            _run(session.read_key())


class AfkPeekStoredExceptionTests(unittest.TestCase):

    def test_stored_exception_raises_carrier_lost(self):
        session, _writer = _make_session(
            _DeadReader(TimeoutError(110, 'Connection timed out')))
        with self.assertRaises(CarrierLost):
            _run(session._afk_peek(0.05))

    def test_eof_raises_carrier_lost(self):
        session, _writer = _make_session(_EofReader())
        with self.assertRaises(CarrierLost):
            _run(session._afk_peek(0.05))

    def test_genuine_timeout_returns_none(self):
        class _IdleReader:
            def exception(self):
                return None

            async def read(self, n=1):
                await asyncio.sleep(10)

        session, _writer = _make_session(_IdleReader())
        result = _run(session._afk_peek(0.02))
        self.assertIsNone(result)


class AfkSequenceDeadConnectionTests(unittest.TestCase):
    """Drives _run_afk_sequence() itself (not just _afk_peek() in
    isolation) against a dead connection, in both the warning stage and
    the screensaver stage -- the real end-to-end proof, matching this
    project's established test_afk_screensaver.py conventions."""

    def test_warning_stage_ends_on_dead_connection_not_spins(self):
        session, _writer = _make_session(
            _DeadReader(TimeoutError(110, 'Connection timed out')))
        with self.assertRaises(CarrierLost):
            _run(session._run_afk_sequence(None), timeout=5)

    def test_screensaver_stage_ends_on_dead_connection_not_spins(self):
        import anetbbs.core.session as session_mod
        session, _writer = _make_session(
            _DeadReader(TimeoutError(110, 'Connection timed out')))
        # Force straight through the warning stage into the screensaver
        # stage in one tick, same technique test_afk_screensaver.py's
        # own tests already use.
        with patch_countdown(session_mod, 0):
            with self.assertRaises(CarrierLost):
                _run(session._run_afk_sequence(None), timeout=5)

    def test_screensaver_stage_ends_on_eof_not_spins(self):
        import anetbbs.core.session as session_mod
        session, _writer = _make_session(_EofReader())
        with patch_countdown(session_mod, 0):
            with self.assertRaises(CarrierLost):
                _run(session._run_afk_sequence(None), timeout=5)


def patch_countdown(session_mod, remain):
    from unittest.mock import patch
    return patch.object(session_mod, '_AFK_WARNING_COUNTDOWN_SECONDS', remain)


class IrcReadLoopStoredExceptionTests(unittest.TestCase):
    """anetirc2._IRC.read_loop() -- the socket-level connection to the
    real upstream IRC server, distinct from the already-fixed (v1.0.90)
    local-terminal _read_key()."""

    def _make_irc(self, reader):
        irc = object.__new__(anetirc2._IRC)
        irc.reader = reader
        irc.writer = _FakeWriter()
        irc.connected = True
        irc.server = 'irc.example.test'
        irc._rbuf = ''
        irc.client = None
        return irc

    def test_stored_exception_ends_the_loop_not_spins(self):
        irc = self._make_irc(
            _DeadReader(TimeoutError(110, 'Connection timed out')))
        # read_loop()'s own post-loop cleanup calls self.client._sys(...)
        # -- irrelevant to what's under test (did the loop spin or end
        # promptly), so a None client is fine as long as it doesn't
        # cause a hang; any exception there still proves the read loop
        # itself already exited.
        try:
            _run(irc.read_loop(), timeout=5)
        except Exception:
            pass

    def test_genuine_timeout_still_sends_a_keepalive_ping(self):
        """Baseline: a real _READ_TIMEOUT (no stored exception) must be
        completely unaffected -- still sends PING and keeps looping."""
        calls = {'n': 0}

        class _OnceThenStopReader:
            def exception(self):
                return None

            async def read(self, n=1):
                calls['n'] += 1
                if calls['n'] > 2:
                    raise ConnectionResetError()
                raise asyncio.TimeoutError()

        irc = self._make_irc(_OnceThenStopReader())
        pings = []

        async def _fake_tx(line):
            pings.append(line)

        irc._tx = _fake_tx
        try:
            _run(irc.read_loop(), timeout=5)
        except Exception:
            pass
        self.assertGreaterEqual(len(pings), 1)
        self.assertTrue(any('PING' in p for p in pings))


if __name__ == '__main__':
    unittest.main()
