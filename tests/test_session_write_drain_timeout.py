"""Regression tests for the write()-drain-timeout fix in
anetbbs.core.session.BBSSession.

Real live incident: a bare ``await self.writer.drain()`` has no timeout
on either asyncio's StreamWriter or asyncssh's SSHWriter -- both simply
await a Future that only resolves once the peer relieves write
backpressure (a TCP zero-window reopening, or -- on SSH -- the client
sending SSH_MSG_CHANNEL_WINDOW_ADJUST on the channel). Confirmed by
reading asyncssh's actual source (asyncssh/stream.py's
SSHStreamSession.drain(): ``await waiter`` with no timeout, resolved
only by _maybe_resume_writing() when the send window reopens).

An operator reported a real session freeze on 1.0.86 that followed a
client's direct SSH connection regardless of which write-heavy screen
(MRC chat, then IRC) was active, with idle CPU (main thread parked in
select()), empty TCP send/receive queues (the data never reached the OS
socket -- it was stuck in the SSH channel's own userspace buffer), and
Ctrl+Q doing nothing (this was never XON/XOFF flow control, a different
mechanism entirely). All of that is exactly what an unbounded drain()
looks like from outside the process.

write() now wraps drain() in asyncio.wait_for() with a bounded timeout
(WRITE_DRAIN_TIMEOUT_SECONDS) and, on timeout, logs real diagnostics
(peer, user, terminal type, and -- when the writer exposes it, as the
SSH writer now does -- how many bytes are still queued in the channel's
own send buffer), force-closes the transport, and raises CarrierLost so
that ONE session unwinds cleanly through the exact same path every other
disconnect already takes, instead of hanging forever with no recovery.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core import session as session_mod
from anetbbs.core.session import BBSSession, CarrierLost
from anetbbs.core.ssh_server import _SshStreamWriter


class _FakeWriter:
    """Mirrors tests/test_petscii_session.py's _FakeWriter, plus a
    controllable drain() so tests can simulate a writer that never
    relieves backpressure."""

    def __init__(self, drain_coro=None):
        self.written = bytearray()
        self._drain_coro = drain_coro
        self.closed = False

    def write(self, data):
        self.written += data

    async def drain(self):
        if self._drain_coro is not None:
            await self._drain_coro()

    def close(self):
        self.closed = True

    def get_extra_info(self, key, default=None):
        return ('203.0.113.5', 12345) if key == 'peername' else default


def _make_session(writer):
    reader = object()  # write() never touches reader
    session = BBSSession(reader, writer, config={})
    return session


class WriteDrainTimeoutTests(unittest.TestCase):
    def test_normal_drain_is_unaffected(self):
        """A writer whose drain() returns promptly must not be delayed
        or disturbed by the new timeout wrapper at all."""
        writer = _FakeWriter()
        session = _make_session(writer)
        asyncio.run(session.write('hello'))
        self.assertEqual(bytes(writer.written), b'hello')
        self.assertFalse(writer.closed)

    def test_slow_but_completing_drain_is_not_falsely_killed(self):
        """A drain() that takes a little while (a real slow connection)
        but genuinely completes before the timeout must not be treated
        as stuck."""
        async def _slow_drain():
            await asyncio.sleep(0.05)

        writer = _FakeWriter(drain_coro=_slow_drain)
        session = _make_session(writer)
        with mock.patch.object(session_mod, 'WRITE_DRAIN_TIMEOUT_SECONDS', 1):
            asyncio.run(session.write('hello'))
        self.assertEqual(bytes(writer.written), b'hello')
        self.assertFalse(writer.closed)

    def test_stuck_drain_times_out_closes_and_raises_carrier_lost(self):
        """The actual bug scenario: drain() never resolves (client
        stopped acknowledging output). Must not hang forever -- must
        time out, force-close the transport, and raise CarrierLost so
        the session unwinds through the standard disconnect path."""
        async def _stuck_drain():
            await asyncio.Event().wait()  # never set -- never resolves

        writer = _FakeWriter(drain_coro=_stuck_drain)
        session = _make_session(writer)
        with mock.patch.object(session_mod, 'WRITE_DRAIN_TIMEOUT_SECONDS', 0.05):
            with self.assertRaises(CarrierLost):
                asyncio.run(session.write('hello'))
        self.assertTrue(writer.closed)

    def test_stuck_drain_logs_diagnostics_including_buffer_size(self):
        """The log line on timeout is the whole point operationally --
        it's what lets a real hang be diagnosed from the journal instead
        of needing py-spy/strace access mid-incident. Confirm it fires
        and includes the queued-write-buffer size when the writer
        exposes one (as the real SSH writer does)."""
        async def _stuck_drain():
            await asyncio.Event().wait()

        writer = _FakeWriter(drain_coro=_stuck_drain)
        writer.get_write_buffer_size = lambda: 4096
        session = _make_session(writer)
        with mock.patch.object(session_mod, 'WRITE_DRAIN_TIMEOUT_SECONDS', 0.05):
            with self.assertLogs(session_mod.logger, level='ERROR') as cm:
                with self.assertRaises(CarrierLost):
                    asyncio.run(session.write('hello'))
        joined = '\n'.join(cm.output)
        self.assertIn('drain() timed out', joined)
        self.assertIn('4096', joined)

    def test_writer_without_buffer_size_hook_does_not_crash(self):
        """Most writers (plain telnet/rlogin) won't have
        get_write_buffer_size() -- the diagnostic must degrade
        gracefully, not blow up trying to call a method that isn't
        there."""
        async def _stuck_drain():
            await asyncio.Event().wait()

        writer = _FakeWriter(drain_coro=_stuck_drain)
        session = _make_session(writer)
        with mock.patch.object(session_mod, 'WRITE_DRAIN_TIMEOUT_SECONDS', 0.05):
            with self.assertRaises(CarrierLost):
                asyncio.run(session.write('hello'))


class _FakeSSHChannel:
    def __init__(self, size, raise_exc=False):
        self._size = size
        self._raise = raise_exc

    def get_write_buffer_size(self):
        if self._raise:
            raise RuntimeError('boom')
        return self._size


class _FakeSSHWriter:
    def __init__(self, channel):
        self.channel = channel

    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class SshWriterBufferSizeTests(unittest.TestCase):
    """Confirms _SshStreamWriter.get_write_buffer_size() actually reaches
    the real asyncssh API (SSHChannel.get_write_buffer_size(), verified
    against asyncssh's own source) rather than a guessed attribute name."""

    def test_proxies_real_channel_api(self):
        adapter = _SshStreamWriter(_FakeSSHWriter(_FakeSSHChannel(1234)),
                                    ('198.51.100.1', 22))
        self.assertEqual(adapter.get_write_buffer_size(), 1234)

    def test_returns_none_if_channel_access_fails(self):
        adapter = _SshStreamWriter(_FakeSSHWriter(_FakeSSHChannel(0, raise_exc=True)),
                                    ('198.51.100.1', 22))
        self.assertIsNone(adapter.get_write_buffer_size())


if __name__ == '__main__':
    unittest.main()
