"""Regression test for a real security/performance-audit finding:
anetbbs.core.rlogin_server.RloginServer.handle_connection() read the
rlogin handshake (_read_rlogin_header()) with NO wall-clock timeout.
_read_rlogin_header() itself caps total accumulated BYTES (see
test_rlogin_header_size_cap.py), but a client that sends the leading
NUL byte and then simply never sends anything else parks the coroutine
on `await reader.read(256)` forever -- each such connection holds a
live socket fd + an entry in active_connections indefinitely, with no
cap on how many an attacker can open at once. Classic slowloris-style
resource-exhaustion DoS against an unauthenticated, internet-facing
listener (rlogin has no auth at the handshake layer at all).

Fix: wrap the handshake read in asyncio.wait_for(..., timeout=
_HANDSHAKE_TIMEOUT_SEC) and treat a resulting asyncio.TimeoutError the
same as an incomplete/aborted handshake -- close the connection instead
of hanging.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core import rlogin_server
from anetbbs.core.rlogin_server import RloginServer


class _StallsForeverReader:
    """Sends the initial NUL, then hangs on every subsequent read --
    exactly what an attacker's slowloris-style connection would do."""

    def __init__(self):
        self._first = True

    async def readexactly(self, n):
        if self._first:
            self._first = False
            return b'\x00'
        raise AssertionError('readexactly should not be called again')

    async def read(self, n):
        # Never resolves on its own -- only asyncio.wait_for()'s outer
        # timeout (patched down to a tiny value below) can unblock this.
        await asyncio.Future()


class _FakeWriter:
    def __init__(self):
        self.closed = False
        self.wait_closed_called = False
        self._closing = False

    def get_extra_info(self, name):
        return ('203.0.113.9', 54321) if name == 'peername' else None

    def write(self, data):
        raise AssertionError(
            'the ack NUL byte must never be written -- the handshake '
            'never completed')

    def is_closing(self):
        return self._closing

    def close(self):
        self.closed = True
        self._closing = True

    async def wait_closed(self):
        self.wait_closed_called = True

    async def drain(self):
        pass


class RloginHandshakeTimeoutTests(unittest.TestCase):
    def test_stalled_handshake_is_closed_instead_of_hanging_forever(self):
        server = RloginServer({})
        reader = _StallsForeverReader()
        writer = _FakeWriter()

        with mock.patch.object(rlogin_server, '_HANDSHAKE_TIMEOUT_SEC', 0.05):
            # The outer wait_for is just a test safety net: if the fix
            # regresses (no timeout on the handshake read), this whole
            # test would otherwise hang the suite forever instead of
            # failing cleanly.
            asyncio.run(asyncio.wait_for(
                server.handle_connection(reader, writer), timeout=5))

        self.assertTrue(
            writer.closed,
            'a stalled/slowloris rlogin handshake must still result in '
            'writer.close() -- this used to hang the connection (and its '
            'fd) open forever')
        self.assertTrue(writer.wait_closed_called)
        self.assertEqual(
            len(server.active_connections), 0,
            'active_connections must not leak an entry for a timed-out '
            'handshake')


if __name__ == '__main__':
    unittest.main()
