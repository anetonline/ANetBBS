"""Regression test for a real, live-reported Critical bug: a dead
telnet connection inside ANEView (the read-only message viewer,
Echomail -> read message) spun the event-loop thread at ~2,600
iterations/second, ~99.9% CPU on the main thread, for about 16 minutes
-- confirmed live via an operator's own py-spy capture. RSS grew to
8.4GB (from a normal ~110MB), roughly 1GB of log data was written, and
every other session on the BBS was starved for the whole duration,
since asyncio is single-threaded. The dead session had been connected
about 19 hours. The captured stack pointed at exactly this call chain:
session.py's write()/read_raw() -> anedit.py's _read_key() (line 597)
-> run() (line 1915) -> launch_aneview() -> read_echo_area() ->
_list_network_areas() -> list_echo_areas() -> _act_echo() -> run_menu().

Two independently-confirmed root causes, both fixed here:

1. anedit.py's _read_key() (used by BOTH ANEdit the composer and
   ANView the viewer, which subclasses it without overriding this
   method) wrapped read_raw() in `except asyncio.TimeoutError: pass`
   (correct -- "no key yet") followed by a blanket
   `except Exception: pass` that ALSO swallowed CarrierLost (a
   ConnectionError subclass read_raw() raises the instant the
   transport dies -- see its own docstring), turning a permanent
   disconnect into "no key this tick" forever. Exact same bug class
   already fixed once in anetirc2.py's own _read_key() (v1.0.90,
   see test_irc_disconnect_busy_loop.py) -- this test mirrors that
   one's structure.

2. Confirmed directly against the real CPython 3.12 asyncio source
   (asyncio/streams.py): StreamReader.read() and StreamWriter.drain()
   both check for an exception already stored on the reader (set via
   set_exception(), called by the transport's own connection_lost()
   with a real error -- e.g. a genuine OS-level ETIMEDOUT on a peer
   that went unresponsive without closing) and raise it IMMEDIATELY,
   before any yield point, on EVERY subsequent call -- not a fresh
   timeout each time. Since Python 3.11, asyncio.TimeoutError IS the
   builtin TimeoutError (the same type a real ETIMEDOUT raises), so
   session.py's read_raw()/write() had no way to tell "my own
   wait_for genuinely timed out" apart from "the connection already
   died and this is that same stored error being replayed" -- and
   treating the latter as the former was what made EVERY iteration
   of the read_key() retry loop (before fix #1) also call write() to
   print an idle-timeout banner, whose own drain() re-raised the same
   stored exception and got logged as a fresh "timed out after 30s"
   ERROR every single time -- the actual source of the ~1GB log flood.
   BBSSession._reader_stored_exception() is the new shared check;
   read_raw()/write()/_read_byte_maybe_spinning() all use it to raise
   CarrierLost immediately instead of proceeding as if a real timeout
   just fired.

Fix #2 alone would still leave anedit.py swallowing the (now correctly
raised, much cheaper) CarrierLost -- fix #1 is required either way.
Fix #2 is real defense in depth: it also closes the same busy-loop
shape for any OTHER read_raw()/write() caller elsewhere in the
codebase with a similar swallowing pattern, without needing to touch
every one of them individually.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession, CarrierLost  # noqa: E402
from anetbbs.features.anedit import ANEdit, ANView          # noqa: E402


class _RaisingSession:
    """Fake BBSSession whose read_raw() always raises, simulating a
    transport that's already permanently erroring (matches the real
    incident: a dead-but-not-closed telnet peer)."""

    def __init__(self, exc):
        self._exc = exc
        self.window_size = (80, 24)
        self.encoding = 'cp437'

    async def read_raw(self, n=1, allow_afk=False):
        raise self._exc

    async def write(self, text):
        pass


class _TimingOutThenRaisingSession:
    """First call times out (a normal idle poll tick), second call
    raises CarrierLost -- confirms the fix doesn't just get lucky by
    never legitimately timing out first."""

    def __init__(self):
        self._calls = 0
        self.window_size = (80, 24)
        self.encoding = 'cp437'

    async def read_raw(self, n=1, allow_afk=False):
        self._calls += 1
        if self._calls == 1:
            await asyncio.sleep(10)  # forces the caller's own wait_for to time out
        raise CarrierLost('client disconnected')

    async def write(self, text):
        pass


def _run(coro, timeout=5):
    """A real bounded timeout so a regression (the loop spinning
    forever) FAILS the test instead of hanging the whole test run --
    same discipline as test_irc_disconnect_busy_loop.py's own _run()."""
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


class ReadKeyPropagatesCarrierLostTests(unittest.TestCase):
    """ANEdit._read_key() is shared by ANView (no override) -- tested
    once via ANEdit directly, same as the real inheritance."""

    def test_carrier_lost_propagates_instead_of_returning_none(self):
        editor = ANEdit(_RaisingSession(CarrierLost('client disconnected')),
                        ["line one"])
        with self.assertRaises(CarrierLost):
            _run(editor._read_key())

    def test_connection_error_propagates(self):
        editor = ANEdit(_RaisingSession(ConnectionError()), ["line one"])
        with self.assertRaises(ConnectionError):
            _run(editor._read_key())

    def test_carrier_lost_after_a_real_timeout_still_propagates(self):
        """First call: a legitimate 0.08s poll timeout (no key typed
        yet) -- must return None quietly, same as always. Second call:
        the connection is now actually dead -- must propagate
        CarrierLost, proving the fix survives a prior real timeout
        rather than only working on the very first call."""
        editor = ANEdit(_TimingOutThenRaisingSession(), ["line one"])
        self.assertIsNone(_run(editor._read_key()))
        with self.assertRaises(CarrierLost):
            _run(editor._read_key())


class ANViewDisconnectBusyLoopTests(unittest.TestCase):
    """The real end-to-end proof: drive ANView.run() itself (the exact
    class/method in the reported incident's stack trace), not just
    _read_key() in isolation, against a session whose transport is
    already dead. Before the fix, this genuinely never returns -- the
    wait_for timeout is what makes that failure mode show up as a test
    FAILURE instead of hanging the test runner forever."""

    def test_run_exits_promptly_on_dead_connection(self):
        viewer = ANView(_RaisingSession(CarrierLost('client disconnected')),
                        ["Some message body.", "Second line."],
                        subject="Test message")
        with self.assertRaises(CarrierLost):
            _run(viewer.run(), timeout=5)


class ReaderStoredExceptionTests(unittest.TestCase):
    """session.py's own layer: a stored reader exception must be
    recognized and converted to CarrierLost immediately, not treated
    as a fresh idle timeout -- confirmed against the real CPython 3.12
    asyncio source (StreamReader.read()/StreamWriter.drain() both
    check reader.exception() and re-raise it before any yield point)."""

    def _make_session(self):
        # object.__new__ bypasses BBSSession.__init__ (which wants a
        # real reader/writer/config) -- only the attributes each
        # method under test actually touches are set, same pattern
        # this project's own mrc/bridge test fixtures already use.
        s = object.__new__(BBSSession)
        s.idle_timeout = 0
        s.encoding = 'cp437'
        s.username = 'tester'
        s.terminal_type = 'ansi'
        return s

    def test_reader_stored_exception_returns_the_stored_exception(self):
        s = self._make_session()

        class _FakeReader:
            def exception(self):
                return TimeoutError(110, 'Connection timed out')

        s.reader = _FakeReader()
        stored = s._reader_stored_exception()
        self.assertIsInstance(stored, TimeoutError)

    def test_reader_stored_exception_is_none_for_a_healthy_reader(self):
        s = self._make_session()

        class _FakeReader:
            def exception(self):
                return None

        s.reader = _FakeReader()
        self.assertIsNone(s._reader_stored_exception())

    def test_read_raw_raises_carrier_lost_without_attempting_a_write(self):
        """The specific mechanism behind the ~1GB log flood: read_raw()
        must NOT try to write an idle-timeout banner (which would
        itself re-raise the same stored exception out of drain() and
        get logged) once a stored reader exception is detected."""
        s = self._make_session()

        stored_exc = TimeoutError(110, 'Connection timed out')

        class _FakeReader:
            def exception(self):
                return stored_exc

        class _FakeWriter:
            def close(self):
                pass

        async def _raising_spin(*a, **kw):
            raise asyncio.TimeoutError()

        write_calls = []

        async def _tracked_write(text):
            write_calls.append(text)

        s.reader = _FakeReader()
        s.writer = _FakeWriter()
        s._read_byte_maybe_spinning = _raising_spin
        s.write = _tracked_write

        with self.assertRaises(CarrierLost):
            _run(s.read_raw(1))
        self.assertEqual(write_calls, [],
                         "read_raw() must skip the idle-timeout banner "
                         "write entirely once a stored reader exception "
                         "is detected -- attempting it is what produced "
                         "the log flood in the real incident")

    def test_read_raw_still_shows_idle_banner_for_a_genuine_timeout(self):
        """A real idle timeout (no stored exception -- the reader is
        healthy, the user just didn't type anything) must be
        completely unaffected by this fix."""
        s = self._make_session()

        class _FakeReader:
            def exception(self):
                return None

        class _FakeWriter:
            def close(self):
                pass

        async def _raising_spin(*a, **kw):
            raise asyncio.TimeoutError()

        write_calls = []

        async def _tracked_write(text):
            write_calls.append(text)

        s.reader = _FakeReader()
        s.writer = _FakeWriter()
        s._read_byte_maybe_spinning = _raising_spin
        s.write = _tracked_write

        with self.assertRaises(CarrierLost):
            _run(s.read_raw(1))
        self.assertEqual(len(write_calls), 1)
        self.assertIn('Idle timeout', write_calls[0])


class WriteDrainStoredExceptionTests(unittest.TestCase):
    """write()'s own drain()-timeout handler must also distinguish a
    stored reader exception from a genuine 30s wait_for timeout --
    confirmed real: write() is what read_raw()'s idle-timeout banner
    attempt calls internally, and it's this exact drain() re-raise
    that produced the "timed out after 30s" ERROR line thousands of
    times per second in the real incident."""

    def _make_session(self, stored_exc):
        s = object.__new__(BBSSession)
        s._forced_term_mode = None
        s.window_size = (80, 24)
        s.encoding = 'cp437'
        s.username = 'tester'
        s.terminal_type = 'ansi'

        class _FakeReader:
            def exception(self):
                return stored_exc

        class _FakeWriter:
            def __init__(self):
                self.closed = False

            def write(self, data):
                pass

            async def drain(self):
                # Real drain() checks self._reader.exception() and
                # re-raises it (a TimeoutError, for a real ETIMEDOUT)
                # before ever genuinely waiting -- simulated directly
                # here since that's the observable outcome at write()'s
                # own call site regardless of the deeper asyncio
                # internals (already verified separately against the
                # real CPython 3.12 source).
                raise asyncio.TimeoutError()

            def get_extra_info(self, key):
                return ('1.2.3.4', 1234)

            def get_write_buffer_size(self):
                return 0

            def close(self):
                self.closed = True

        s.reader = _FakeReader()
        s.writer = _FakeWriter()
        return s

    def test_stored_exception_raises_carrier_lost_and_closes_writer(self):
        s = self._make_session(TimeoutError(110, 'Connection timed out'))
        with self.assertRaises(CarrierLost) as ctx:
            _run(s.write('hello'))
        self.assertTrue(s.writer.closed)
        # Distinguishes this from the generic-drain-timeout path below:
        # both raise CarrierLost and close the writer, so the message
        # (carrying the real stored error, not the generic
        # "write drain timed out" text) is the only observable proof
        # this went through the stored-exception branch specifically.
        self.assertIn('Connection timed out', str(ctx.exception))

    def test_genuine_drain_timeout_still_raises_carrier_lost(self):
        # No stored exception -- a real backpressure timeout (client
        # stopped acknowledging output) must be completely unaffected.
        s = self._make_session(None)
        with self.assertRaises(CarrierLost) as ctx:
            _run(s.write('hello'))
        self.assertEqual(str(ctx.exception), 'write drain timed out')
        self.assertTrue(s.writer.closed)


class SpinningCursorStoredExceptionTests(unittest.TestCase):
    """The related latent gap (report item A, not observed in this
    incident -- ANEView always reads in 64/8-byte chunks, which never
    enters the spinning branch at all): a session with the 'spinning'
    cursor preference and no idle_timeout configured
    (overall_timeout=None) must not spin forever on a dead connection
    either. Before this fix, the `elapsed >= overall_timeout` exit
    never fires when overall_timeout is None, so only the stored-
    exception check can end the loop."""

    def test_stored_exception_ends_the_spin_loop_with_no_overall_timeout(self):
        s = object.__new__(BBSSession)
        s.user = {'cursor_style': 'spinning'}

        class _FakeReader:
            def exception(self):
                return TimeoutError(110, 'Connection timed out')

            async def read(self, n):
                raise asyncio.TimeoutError()

        s.reader = _FakeReader()

        with self.assertRaises(CarrierLost):
            _run(s._read_byte_maybe_spinning(1, overall_timeout=None,
                                             allow_afk=False), timeout=5)


if __name__ == '__main__':
    unittest.main()
