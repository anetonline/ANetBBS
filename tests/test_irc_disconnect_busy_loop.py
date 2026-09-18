"""Regression test for a real, live-reported Critical bug: a
disconnected native-IRC chat session (the remote IRC server closing
its end, or the local terminal connection dying) could spin the
event-loop thread at ~90% of one CPU core, growing to several
gigabytes of RSS, with the affected socket left stuck in CLOSE-WAIT
forever -- confirmed live via an automatic resource-threshold capture
(load/RSS trigger) that caught a real py-spy-style stack trace pointing
at exactly this call chain: session.py's read_raw() ->
anetirc2.py's _read_key() -> _ui_loop() -> _chat_session() ->
_startup_loop() -> run().

Root cause, confirmed directly against the source: BBSSession.read_raw()
already raises CarrierLost (a ConnectionError subclass) the instant the
transport hits real EOF -- never returns an ambiguous empty value (see
its own docstring in core/session.py). ANetIRC._read_key() wrapped its
call to read_raw() in `except asyncio.TimeoutError: pass` (correct --
that just means "no key typed yet, keep polling") followed by a
blanket `except Exception: pass` that ALSO swallowed CarrierLost,
turning a permanent disconnect into "no key this tick" forever. Once
the underlying connection is truly dead, read_raw() on the now-EOF'd
transport returns near-instantly on every call instead of blocking --
so _ui_loop()'s `while not self._back:` calling _read_key() right back
immediately re-entered the same cycle with no throttling at all,
exactly the same busy-loop shape as the MRC bug fixed in
tests/test_mrc_chat_disconnect_busy_loop.py, just in the separate
native-IRC client code path that was never audited at the time. This
also explains the observed stuck CLOSE-WAIT socket: _chat_session()'s
own `finally: await self.irc.disconnect()` never ran, since the loop
never exited to reach it.

Fixed by letting CarrierLost (and anything else besides the expected
poll TimeoutError) propagate out of _read_key() instead of being
silently discarded, so _ui_loop()/_chat_session()/run() unwind this
IRC session the same clean way every other disconnect already does.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import CarrierLost
from anetbbs.features.anetirc2 import ANetIRC, _Screen


class _RaisingSession:
    """Fake BBSSession whose read_raw() always raises, simulating a
    transport that's already at permanent EOF (matches the real
    incident: the remote IRC server's peer already closed, confirmed
    live via a lingering CLOSE-WAIT socket)."""

    def __init__(self, exc):
        self._exc = exc
        self.window_size = (80, 24)

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

    async def read_raw(self, n=1, allow_afk=False):
        self._calls += 1
        if self._calls == 1:
            await asyncio.sleep(10)  # never returns in time -- forces the caller's own wait_for to time out
        raise CarrierLost('client disconnected')

    async def write(self, text):
        pass


def _run(coro, timeout=5):
    """A real bounded timeout so a regression (the loop hanging/
    spinning forever) FAILS the test instead of hanging the whole
    test run -- same discipline as
    tests/test_mrc_chat_disconnect_busy_loop.py's own _run()."""
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


class ReadKeyPropagatesCarrierLostTests(unittest.TestCase):
    def test_carrier_lost_propagates_instead_of_returning_none(self):
        irc = ANetIRC(_RaisingSession(CarrierLost('client disconnected')),
                     '/tmp/.anetirc2_test_cfg_nonexistent.json')
        with self.assertRaises(CarrierLost):
            _run(irc._read_key(0.05))

    def test_connection_error_propagates(self):
        irc = ANetIRC(_RaisingSession(ConnectionError()),
                     '/tmp/.anetirc2_test_cfg_nonexistent.json')
        with self.assertRaises(ConnectionError):
            _run(irc._read_key(0.05))

    def test_carrier_lost_after_a_real_timeout_still_propagates(self):
        """First call: a legitimate poll timeout (no key typed yet) --
        must return None quietly, same as always. Second call: the
        connection is now actually dead -- must propagate CarrierLost,
        proving the fix survives a prior real timeout rather than only
        working on the very first call."""
        irc = ANetIRC(_TimingOutThenRaisingSession(),
                     '/tmp/.anetirc2_test_cfg_nonexistent.json')
        self.assertIsNone(_run(irc._read_key(0.05)))
        with self.assertRaises(CarrierLost):
            _run(irc._read_key(0.05))


class UiLoopDisconnectBusyLoopTests(unittest.TestCase):
    """The real end-to-end proof: drive _ui_loop() itself (not just
    _read_key() in isolation) against a session whose transport is
    already dead. Before the fix, this genuinely never returns -- the
    wait_for timeout below is what makes that failure mode show up as
    a test FAILURE instead of hanging the test runner forever."""

    def test_ui_loop_exits_promptly_on_dead_connection(self):
        irc = ANetIRC(_RaisingSession(CarrierLost('client disconnected')),
                     '/tmp/.anetirc2_test_cfg_nonexistent.json')
        irc._scr = _Screen(80, 24)  # normally set by run() via _detect_size()
        with self.assertRaises(CarrierLost):
            _run(irc._ui_loop(), timeout=5)


if __name__ == '__main__':
    unittest.main()
