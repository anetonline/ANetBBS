"""Regression test: both binkp_server.py's _receive_files() (answering
side) and binkp.py's _receive_messages() (dialing-out side) used to wait
up to 120s / 60s respectively for a new frame before giving up on a
peer that's gone quiet after its last file. Real live measurement
against a real hub (binkd/1.1a-113) showed the TCP connection itself
consistently dying ~15s after its last file -- far short of either of
those configured timeouts, meaning something OUTSIDE our own code (the
network path, not the hub's application logic) was silently killing
the idle connection well before we ever tried to speak again. Our own
confirmatory M_EOB (see the proactive-EOB fix in v1.0b2.148) was
therefore always being sent into an already-dead connection. Both
receive loops now use a much shorter wait (5s) so our confirmation goes
out while the link is still alive.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ServerShortIdleTimeoutTests(unittest.TestCase):
    """binkp_server.py's _receive_files()."""

    class _FakeReader:
        async def readexactly(self, n):
            raise asyncio.IncompleteReadError(partial=b'', expected=n)

    def test_uses_a_short_wait_not_120s(self):
        from anetbbs.echomail import binkp_server as mod

        captured_timeouts = []
        real_wait_for = asyncio.wait_for

        async def _spy_wait_for(coro, timeout=None):
            captured_timeouts.append(timeout)
            return await real_wait_for(coro, timeout=timeout)

        writer = None
        state = {'name': None, 'size': 0, 'buf': bytearray()}
        with patch.object(asyncio, 'wait_for', _spy_wait_for):
            asyncio.run(mod._receive_files(
                self._FakeReader(), writer, ('1.2.3.4', 1), state, []))

        self.assertTrue(captured_timeouts, 'expected at least one wait_for call')
        self.assertLess(captured_timeouts[0], 15,
            "receive loop's per-frame wait must be well under the ~15s "
            "window a real hub's connection was observed dying at -- "
            f"got {captured_timeouts[0]}")


class ClientShortIdleTimeoutTests(unittest.TestCase):
    """binkp.py's BinkPClient._receive_messages()."""

    def test_shrinks_socket_timeout_before_the_receive_loop(self):
        from anetbbs.echomail.binkp import BinkPClient

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._send_cmd = lambda cmd, text='': True

        class _FakeSocket:
            def __init__(self):
                self.timeouts_set = []
            def settimeout(self, value):
                self.timeouts_set.append(value)

        client._sock = _FakeSocket()

        def _fake_recv(*a, **kw):
            raise ConnectionError('peer closed')

        client._recv_frame_logged = _fake_recv
        client._receive_messages(data_dir='/tmp')

        self.assertTrue(client._sock.timeouts_set,
            'expected settimeout() to be called before the receive loop')
        self.assertLess(client._sock.timeouts_set[-1], 15,
            "socket timeout during the receive phase must be well under "
            "the ~15s window a real hub's connection was observed dying "
            f"at -- got {client._sock.timeouts_set[-1]}")


if __name__ == '__main__':
    unittest.main()
