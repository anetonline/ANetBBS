"""Hardening regression tests for anetbbs/mail/smtp_server.py's
_handle(), mirroring the real finger_server.py gaps found in a past
security/performance audit (deadconn-freeze-audit convention): a
client that never reads its response must not hang the handler
forever (drain timeout), and a client that sends an oversized/
unterminated line past the default read buffer must not leak the
connection's file descriptor (LimitOverrunError must still close it).

Port 25 is unauthenticated and directly internet-facing, same risk
class as finger's port 79 -- this new listener gets the same coverage
from day one rather than waiting for a live incident to find it.
"""
import asyncio
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.mail.smtp_server import _handle  # noqa: E402


class _FakeMailConfig:
    enabled = True
    domain = 'bbs.a-net.fyi'
    max_message_size = 25 * 1024 * 1024


@contextmanager
def _fake_app_context():
    yield


class _FakeApp:
    def app_context(self):
        return _fake_app_context()


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


class _FakeWriter:
    """A normal, cooperative writer -- for the read-side failure tests,
    where it's readline() that misbehaves, not drain()."""

    def __init__(self):
        self.closed = False
        self.wait_closed_called = False

    def get_extra_info(self, name):
        return ('203.0.113.5', 54321) if name == 'peername' else None

    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.wait_closed_called = True


def _patches():
    return (
        mock.patch('anetbbs.mail.smtp_server._flask_app', return_value=_FakeApp()),
        mock.patch('anetbbs.mail.smtp_server._dnsbl_check', return_value=False),
        mock.patch('anetbbs.models.MailConfig.get', return_value=_FakeMailConfig()),
    )


class SmtpServerDrainTimeoutTests(unittest.TestCase):
    def test_stuck_drain_on_greeting_times_out_instead_of_hanging(self):
        writer = _StuckWriter()

        class _FakeReader:
            async def readline(self):
                return b'QUIT\r\n'

        p1, p2, p3 = _patches()
        with p1, p2, p3:
            asyncio.run(asyncio.wait_for(
                _handle(_FakeReader(), writer), timeout=15))
        self.assertTrue(writer.closed,
                        'a stuck drain() on the greeting must still result in '
                        'the connection being torn down, not hung forever')
        self.assertTrue(writer.wait_closed_called)


class SmtpServerLimitOverrunFdLeakTests(unittest.TestCase):
    def test_limit_overrun_on_command_read_still_closes_the_connection(self):
        class _RaisingReader:
            def __init__(self):
                self._first = True

            async def readline(self):
                if self._first:
                    self._first = False
                    raise asyncio.LimitOverrunError('line too long', 65536)
                return b'QUIT\r\n'

        writer = _FakeWriter()
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            asyncio.run(_handle(_RaisingReader(), writer))
        self.assertTrue(writer.closed,
                        'a LimitOverrunError from readline() must still '
                        'result in writer.close() -- not leak the fd')
        self.assertTrue(writer.wait_closed_called)

    def test_unexpected_exception_mid_handler_still_closes_the_connection(self):
        """Defense in depth: the whole handler body is one try/finally,
        not a patch around one anticipated exception type -- any other
        unexpected failure mid-handler must still close the connection."""
        class _FakeReader:
            async def readline(self):
                return b'QUIT\r\n'

        writer = _FakeWriter()
        with mock.patch('anetbbs.mail.smtp_server._flask_app',
                        side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                asyncio.run(_handle(_FakeReader(), writer))
        self.assertTrue(writer.closed)
        self.assertTrue(writer.wait_closed_called)


if __name__ == '__main__':
    unittest.main()
