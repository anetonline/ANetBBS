"""Regression test for a real gap found in the same audit pass as
ANetBBS core's _drain_protected() fix (v1.0.98): finger_server._handle()
called `await writer.drain()` with no timeout. Finger (TCP 79) is
unauthenticated and directly internet-facing, and its reply payload is
unbounded (a user's own tagline/bio can be arbitrarily long) -- a
client that opens a connection, sends a query, and then never reads
the response parks that handler coroutine forever, same underlying
mechanism as ANetBBS's own v1.0.87 session write-hang bug, just on a
different, pre-auth listener. Verified by reverting first: the
pre-fix code genuinely hangs past this test's own bound.
"""
import asyncio
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.finger_server import _handle  # noqa: E402


class _FakeReader:
    async def readline(self):
        return b'\r\n'


class _StuckWriter:
    """A writer whose drain() never completes -- simulates a client
    that stopped reading without closing the socket."""

    def __init__(self):
        self.closed = False
        self.wait_closed_called = False

    def get_extra_info(self, name):
        return ('203.0.113.5', 54321) if name == 'peername' else None

    def write(self, data):
        pass

    async def drain(self):
        await asyncio.Event().wait()

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.wait_closed_called = True


@contextmanager
def _fake_app_context():
    yield


class _FakeApp:
    def app_context(self):
        return _fake_app_context()


class FingerServerDrainTimeoutTests(unittest.TestCase):
    def test_stuck_drain_times_out_instead_of_hanging(self):
        writer = _StuckWriter()
        with mock.patch('anetbbs.core.finger_server._flask_app',
                        return_value=_FakeApp()), \
             mock.patch('anetbbs.core.finger_server._online_users',
                        return_value=[]):
            asyncio.run(asyncio.wait_for(
                _handle(_FakeReader(), writer), timeout=15))
        self.assertTrue(writer.closed,
                        'a stuck drain() must still result in the '
                        'connection being torn down, not hung forever')
        self.assertTrue(writer.wait_closed_called)


if __name__ == '__main__':
    unittest.main()
