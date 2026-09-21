"""Shared test doubles for "dead connection" regression tests.

Extracted after the same fixture shapes were hand-rolled three times
independently (test_irc_disconnect_busy_loop.py,
test_aneview_disconnect_busy_loop.py, test_deadconn_readkey_afk_irc.py)
while chasing the same underlying bug class across v1.0.90/v1.0.96/
v1.0.97. See the deadconn-freeze-audit skill for the full methodology
this supports -- in short: a loop reading from a session/socket that
catches asyncio.TimeoutError or a broad Exception/ConnectionError
without distinguishing a genuinely expired wait from a connection that
had already died can spin indefinitely (pegging CPU, growing memory,
flooding logs) instead of ending cleanly. These doubles simulate a
dead connection at the two levels that bug shape has actually been
found at.

NOT auto-collected by pytest (filename doesn't match test_*.py) --
import directly, e.g.:

    from _deadconn_fixtures import DeadReader, EofReader, make_session

Stream-level (DeadReader / EofReader / FakeWriter / make_session) --
for testing BBSSession's own read_raw()/write()/read_key()/
_afk_peek()/_run_afk_sequence()/_drain_protected(), or any other code
(like anetirc2._IRC) that owns a real asyncio.StreamReader/
StreamWriter pair directly. DeadReader.read() re-raises a stored
exception on every call and StreamWriter-alike .drain() would too if
you gave FakeWriter one -- this matches real asyncio.StreamReader/
StreamWriter behavior exactly (confirmed against CPython 3.12's
asyncio/streams.py: StreamReader.read()'s very first line is
`if self._exception is not None: raise self._exception`;
StreamWriter.drain() checks `self._reader.exception()` before its own
`await sleep(0)`). EofReader.read() returns b'' forever -- plain EOF,
no exception at all.

Session-level (RaisingSession / TimingOutThenRaisingSession) -- for
testing code that takes a session-like object and calls
session.read_raw() on it (e.g. anetirc2.ANetIRC._read_key(),
anedit.ANEdit._read_key()) -- fakes read_raw() itself rather than the
underlying stream, since that code never touches session.reader
directly.
"""
import asyncio

from anetbbs.core.session import BBSSession, CarrierLost


class FakeWriter:
    """A StreamWriter-alike whose drain() is a no-op by default --
    pass drain_coro to simulate real backpressure behavior (a slow,
    stuck, or immediately-failing drain), matching
    test_session_write_drain_timeout.py's own fixture."""

    def __init__(self, peer=('1.2.3.4', 1234), drain_coro=None):
        self._peer = peer
        self.written = bytearray()
        self._closing = False
        self._drain_coro = drain_coro

    def get_extra_info(self, key, default=None):
        return self._peer if key == 'peername' else default

    def write(self, data):
        self.written += data

    async def drain(self):
        if self._drain_coro is not None:
            await self._drain_coro()

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    def get_write_buffer_size(self):
        return 0

    async def wait_closed(self):
        pass


class DeadReader:
    """A StreamReader-alike that already has a real connection-level
    exception stored on it -- read() re-raises it immediately, on
    every call, forever."""

    def __init__(self, exc):
        self._exc = exc

    def exception(self):
        return self._exc

    async def read(self, n=1):
        raise self._exc


class EofReader:
    """A StreamReader-alike at plain EOF -- read() returns b'' forever,
    no exception at all (real asyncio behavior for a cleanly-closed-
    but-unobserved transport)."""

    def exception(self):
        return None

    async def read(self, n=1):
        return b''


def make_session(reader, afk_warning_seconds=0, idle_timeout=0, writer=None):
    """A real BBSSession wired to the given fake reader, for testing
    BBSSession's own methods directly."""
    writer = writer if writer is not None else FakeWriter()
    session = BBSSession(reader, writer, config={})
    session.user = {'id': 1}
    session.afk_warning_seconds = afk_warning_seconds
    session.idle_timeout = idle_timeout
    session.window_size = (80, 24)
    return session, writer


class RaisingSession:
    """Fake session-like object whose read_raw() always raises,
    simulating a transport that's already at permanent EOF or has a
    stored error -- for testing code that takes a `session` argument
    and calls session.read_raw() on it, rather than owning a real
    reader/writer pair itself."""

    def __init__(self, exc):
        self._exc = exc
        self.window_size = (80, 24)
        self.encoding = 'cp437'

    async def read_raw(self, n=1, allow_afk=False):
        raise self._exc

    async def write(self, text):
        pass


class TimingOutThenRaisingSession:
    """First call times out (a normal idle poll tick), second call
    raises -- confirms a fix doesn't just get lucky by never
    legitimately timing out first."""

    def __init__(self, exc=None):
        self._calls = 0
        self._exc = exc if exc is not None else CarrierLost('client disconnected')
        self.window_size = (80, 24)
        self.encoding = 'cp437'

    async def read_raw(self, n=1, allow_afk=False):
        self._calls += 1
        if self._calls == 1:
            await asyncio.sleep(10)  # forces the caller's own wait_for to time out
        raise self._exc

    async def write(self, text):
        pass


def run(coro, timeout=5):
    """A real bounded timeout so a regression (a spin/hang) FAILS the
    test instead of hanging the whole test run.

    Note: a truly un-yielding loop (one whose every iteration re-raises
    an exception synchronously, with no real yield point in between)
    can defeat even this asyncio-level bound -- confirmed live more
    than once while verifying these exact fixes by reverting them. If
    a deliberately-broken probe hangs past this timeout instead of
    failing cleanly, that IS the confirmation (wrap the probe itself in
    a hard OS-level `timeout` shell command rather than trusting this
    bound alone)."""
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
