"""Dead-connection regression tests for anetbbs/core/enhanced_server.py's
_WSReaderAdapter -- per the deadconn-freeze-audit skill.

This adapter bridges aiohttp's message-oriented, exception-signaled
WebSocketResponse API into the byte-stream read(n) + stored-exception
(.exception()) contract anetbbs/core/session.py's BBSSession already
depends on via _reader_stored_exception()/_drain_protected(). Getting
this wrong risks the exact "dead connection replays a stored exception
forever, mistaken for a fresh asyncio.wait_for() timeout, spins the
whole session" bug class documented in that method's own docstring (a
real 16-minute, 8.4GB-RSS, ~1GB-log-spam incident elsewhere in this
codebase -- ANEView's _read_key()).

Plugs the REAL _WSReaderAdapter (not a reimplementation) straight into
tests/_deadconn_fixtures.py's make_session() -- which accepts any
object exposing the same async read(n) + sync exception() shape as a
real asyncio.StreamReader -- so this exercises BBSSession's actual
read_raw() against the actual adapter, with only a minimal hand-rolled
fake aiohttp WebSocketResponse standing in for aiohttp itself (which
isn't the thing under test here; the adapter is).

TEST VERSION ONLY -- part of the "ANetBBS Enhanced Client" feature,
disabled by default (Config.ENHANCED_ENABLED), not yet pushed/deployed.
"""
import asyncio
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / 'tests'))

from _deadconn_fixtures import make_session, run  # noqa: E402

from anetbbs.core.session import CarrierLost  # noqa: E402
from anetbbs.core.enhanced_server import _WSReaderAdapter, _WSWriterAdapter  # noqa: E402

try:
    from aiohttp import WSMsgType
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False


class _FakeWSMessage:
    def __init__(self, type_, data=None):
        self.type = type_
        self.data = data


class _DeadFakeWS:
    """Simulates an aiohttp WebSocketResponse that has already hit a
    real network-level error -- receive() raises it immediately, on
    every call, forever (matches real aiohttp behavior: once a
    connection has failed, every subsequent receive()/send_*() raises
    the same underlying error, not a fresh one each time)."""

    def __init__(self, exc):
        self._exc = exc

    async def receive(self):
        raise self._exc

    def exception(self):
        return self._exc


class _CloseFakeWS:
    """Simulates a clean close handshake -- receive() returns a CLOSE
    message rather than raising."""

    async def receive(self):
        return _FakeWSMessage(WSMsgType.CLOSE)

    def exception(self):
        return None


class _ErrorFakeWS:
    """Simulates aiohttp's own ERROR message type, with the real
    exception available via ws.exception()."""

    def __init__(self, exc):
        self._exc = exc

    async def receive(self):
        return _FakeWSMessage(WSMsgType.ERROR)

    def exception(self):
        return self._exc


class _OneGoodThenDeadFakeWS:
    """First receive() returns a real client keystroke; every call
    after that raises -- confirms live traffic is decoded correctly
    before the connection dies, not just the dead-from-the-start case."""

    def __init__(self, exc):
        self._exc = exc
        self._calls = 0

    async def receive(self):
        self._calls += 1
        if self._calls == 1:
            return _FakeWSMessage(WSMsgType.TEXT, '{"type": "key", "ch": "A"}')
        raise self._exc


@unittest.skipUnless(_AIOHTTP_AVAILABLE, 'requires aiohttp')
class WSReaderAdapterDeadConnTests(unittest.TestCase):
    """_WSReaderAdapter now starts a background pump task at
    construction time (see its own docstring -- the fix for the real
    "Concurrent call to receive() is not allowed" crash found live in
    MRC chat), so it must always be constructed from inside a running
    event loop, same as real usage (anetbbs/core/enhanced_server.py's
    _handle_ws() constructs it inside an async handler). Each test
    wraps its whole scenario in one coroutine passed to run() rather
    than constructing the adapter at the top of a sync test method."""

    def test_stored_exception_raises_carrierlost_not_a_hang(self):
        async def _scenario():
            fake_ws = _DeadFakeWS(ConnectionResetError('peer reset'))
            adapter = _WSReaderAdapter(fake_ws)
            session, _writer = make_session(adapter)
            with self.assertRaises(CarrierLost):
                await session.read_raw()
        run(_scenario())

    def test_stored_exception_is_the_same_object_on_every_subsequent_call(self):
        # The real incident this guards against: a dead connection's
        # error must replay IDENTICALLY forever, not get re-derived or
        # swallowed into a generic timeout on the second call.
        async def _scenario():
            exc = ConnectionResetError('peer reset')
            fake_ws = _DeadFakeWS(exc)
            adapter = _WSReaderAdapter(fake_ws)
            session, _writer = make_session(adapter)
            with self.assertRaises(CarrierLost):
                await session.read_raw()
            self.assertIs(adapter.exception(), exc)
            with self.assertRaises(CarrierLost):
                await session.read_raw()
            self.assertIs(adapter.exception(), exc)
        run(_scenario())

    def test_close_message_type_raises_carrierlost(self):
        async def _scenario():
            adapter = _WSReaderAdapter(_CloseFakeWS())
            session, _writer = make_session(adapter)
            with self.assertRaises(CarrierLost):
                await session.read_raw()
        run(_scenario())

    def test_error_message_type_raises_carrierlost_with_real_exception(self):
        async def _scenario():
            underlying = ConnectionAbortedError('transport error')
            adapter = _WSReaderAdapter(_ErrorFakeWS(underlying))
            session, _writer = make_session(adapter)
            with self.assertRaises(CarrierLost):
                await session.read_raw()
            self.assertIs(adapter.exception(), underlying)
        run(_scenario())

    def test_live_keystroke_decoded_before_connection_dies(self):
        async def _scenario():
            adapter = _WSReaderAdapter(_OneGoodThenDeadFakeWS(ConnectionResetError('later')))
            session, _writer = make_session(adapter)
            data = await adapter.read(1)
            self.assertEqual(data, b'A')
            with self.assertRaises(CarrierLost):
                await session.read_raw()
        run(_scenario())

    def test_stored_timeouterror_is_not_replayed_as_an_ambiguous_timeout(self):
        """Shape 1 of the deadconn-freeze-audit skill: since Python 3.11
        asyncio.TimeoutError IS the builtin TimeoutError, a stored
        TimeoutError would be indistinguishable BY TYPE from a genuine
        asyncio.wait_for() timeout elsewhere in session.py. The adapter
        must never store a bare TimeoutError as-is."""
        async def _scenario():
            fake_ws = _DeadFakeWS(TimeoutError('peer stopped acking'))
            adapter = _WSReaderAdapter(fake_ws)
            with self.assertRaises(ConnectionResetError):
                await adapter.read(1)
            stored = adapter.exception()
            self.assertIsNotNone(stored)
            self.assertNotIsInstance(stored, TimeoutError)
        run(_scenario())

    def test_concurrent_receive_cannot_happen_even_under_repeated_cancellation(self):
        """The actual bug found live: mrc_chat.py's _enter_split_screen()
        wraps read_raw() in a tight asyncio.wait_for(timeout=...) loop
        while polling for a CPR response that never comes (an Enhanced
        Client browser isn't a real terminal and never answers the
        cursor-position query). Each timeout cancels the in-flight
        read() -- before the pump-task fix, that could leave aiohttp's
        WebSocketResponse.receive() in a state where the NEXT call
        raised "Concurrent call to receive() is not allowed" even
        though nothing was really concurrent. Simulates that exact
        polling pattern against a ws whose receive() hangs forever
        (never answers) -- the fix means every cancelled read() is
        just abandoning a queue.get(), never touching receive() itself,
        so repeated cancellation must never raise anything from the ws
        side at all."""
        class _NeverAnswersWS:
            def __init__(self):
                self.receive_call_count = 0

            async def receive(self):
                self.receive_call_count += 1
                await asyncio.sleep(3600)

            def exception(self):
                return None

        async def _scenario():
            fake_ws = _NeverAnswersWS()
            adapter = _WSReaderAdapter(fake_ws)
            for _ in range(8):
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(adapter.read(32), timeout=0.05)
            # receive() was only ever called once -- the pump task's own
            # single long-lived call -- never re-entered or restarted by
            # any of the 8 cancelled read() attempts above.
            self.assertEqual(fake_ws.receive_call_count, 1)
        run(_scenario(), timeout=10)


class WSWriterAdapterTests(unittest.TestCase):
    """write()/drain() must preserve asyncio.StreamWriter's split: write()
    is synchronous (just schedules the send), drain() is what actually
    waits for it -- session.py's _drain_protected() depends on exactly
    this split to apply its own asyncio.wait_for() timeout (Shape 2)."""

    def test_write_is_synchronous_and_drain_waits_for_the_real_send(self):
        sent = []

        class _OkWS:
            async def send_str(self, text):
                sent.append(text)

            async def close(self):
                pass

        async def _scenario():
            adapter = _WSWriterAdapter(_OkWS())
            adapter.write('{"type": "text", "s": "hi"}')  # must not require await
            self.assertEqual(sent, [])  # not sent yet -- only scheduled
            await adapter.drain()
            self.assertEqual(sent, ['{"type": "text", "s": "hi"}'])

        run(_scenario())

    def test_close_cancels_a_still_pending_send(self):
        class _HangingWS:
            async def send_str(self, text):
                await asyncio.sleep(10)

            async def close(self):
                pass

        async def _scenario():
            adapter = _WSWriterAdapter(_HangingWS())
            adapter.write('{"type": "text", "s": "hi"}')
            pending = adapter._pending
            adapter.close()
            await asyncio.sleep(0)
            self.assertTrue(pending.cancelled() or pending.done())

        run(_scenario())


if __name__ == '__main__':
    unittest.main()
