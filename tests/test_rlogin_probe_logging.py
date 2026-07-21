"""Regression test for a live sysop report: ANetBBS's own Service Control
Center health-check prober (anetbbs/web/control.py's _probe_tcp_uncached,
which connects-and-immediately-closes each listener port every ~5s from
127.0.0.1 to confirm it's alive) used to log at INFO level identically to
a real client:

    rlogin connection from ('127.0.0.1', 33294)
    rlogin connection closed for ('127.0.0.1', 33294)

with nothing in between (the probe sends zero bytes, so
_read_rlogin_header's readexactly(1) immediately raises
IncompleteReadError). A sysop reported this live as what looked like a
sustained, unbannable attack -- no real external IP ever appears because
there genuinely isn't one; it's the BBS's own loopback health check.

Fix: the initial accept and the "closed" line in the finally block are
now DEBUG unless the rlogin handshake actually completed, and a NEW INFO
line ("rlogin session started for ...") only fires once it does -- so a
real connection attempt is still fully visible at INFO, but the SCC's
own probe traffic no longer looks like repeated failed/attacking
connections.
"""
import asyncio
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.rlogin_server import RloginServer


class _FakeWriter:
    def __init__(self, peer=('127.0.0.1', 12345)):
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


class _ProbeReader:
    """A bare connect-then-close: reads immediately hit EOF, exactly
    like the SCC's health-check probe (zero bytes sent)."""
    async def readexactly(self, n):
        raise asyncio.IncompleteReadError(b'', n)


class _RealClientReader:
    """A genuine rlogin client sending the real NUL-terminated header."""
    def __init__(self):
        self._first = True
        self._payload = b'alice\x00bob\x00vt100/9600\x00'

    async def readexactly(self, n):
        if self._first:
            self._first = False
            return b'\x00'
        raise asyncio.IncompleteReadError(b'', n)

    async def read(self, n):
        if self._payload:
            chunk, self._payload = self._payload, b''
            return chunk
        return b''


class RloginProbeLoggingTests(unittest.TestCase):
    def test_bare_probe_connection_does_not_log_at_info(self):
        server = RloginServer(config={})
        with self.assertLogs('anetbbs.core.rlogin_server', level='DEBUG') as cm:
            asyncio.run(server.handle_connection(_ProbeReader(), _FakeWriter()))
        info_records = [r for r in cm.records if r.levelno >= logging.INFO]
        self.assertEqual(info_records, [],
                         'a bare connect-and-close (no handshake data) must not '
                         'produce any INFO-level log -- that is what made the SCC '
                         'health-check probe look like a real/attacking connection')
        # It must still be visible at DEBUG for anyone who wants to see it.
        debug_msgs = [r.getMessage() for r in cm.records]
        self.assertTrue(any('rlogin connection from' in m for m in debug_msgs))

    def test_real_handshake_still_logs_at_info(self):
        server = RloginServer(config={})
        with patch('anetbbs.core.rlogin_server.BBSSession') as MockSession:
            MockSession.return_value.start = AsyncMock()
            with self.assertLogs('anetbbs.core.rlogin_server', level='DEBUG') as cm:
                asyncio.run(server.handle_connection(_RealClientReader(), _FakeWriter()))
        info_msgs = [r.getMessage() for r in cm.records if r.levelno >= logging.INFO]
        self.assertTrue(any('rlogin session started' in m for m in info_msgs),
                        f'expected an INFO line for a completed handshake, got: {info_msgs}')
        self.assertTrue(any('rlogin connection closed' in m for m in info_msgs))

    def test_real_handshake_passes_prefill_username_through(self):
        server = RloginServer(config={})
        with patch('anetbbs.core.rlogin_server.BBSSession') as MockSession:
            MockSession.return_value.start = AsyncMock()
            asyncio.run(server.handle_connection(_RealClientReader(), _FakeWriter()))
        _, kwargs = MockSession.call_args
        self.assertEqual(kwargs.get('prefill_username'), 'bob')


if __name__ == '__main__':
    unittest.main()
