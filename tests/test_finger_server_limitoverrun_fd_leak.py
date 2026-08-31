"""Regression test for a real High-severity finding from a security/
performance audit (2026-08-31): anetbbs.core.finger_server._handle()
only caught asyncio.TimeoutError around reader.readline() -- readline()
has no explicit limit= here, so it uses asyncio.start_server()'s
default 64KiB buffer, and any client sending more than 64KB with no
newline raises asyncio.LimitOverrunError, which was NOT caught. That
exception propagated straight out of _handle(), skipping the
writer.close()/wait_closed() cleanup entirely (it only ran inside a
LATER try/finally, past the failure point) -- a trivial, remote,
UNAUTHENTICATED (finger/TCP-79 has no auth of any kind, its own
systemd unit, directly internet-facing), repeatable file-descriptor
leak.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.finger_server import _handle


class _FakeReader:
    def __init__(self, exc):
        self._exc = exc

    async def readline(self):
        raise self._exc


class _FakeWriter:
    def __init__(self):
        self.closed = False
        self.wait_closed_called = False

    def get_extra_info(self, name):
        return ('203.0.113.5', 54321) if name == 'peername' else None

    def write(self, data):
        raise AssertionError('write() must not be reached if readline() failed')

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.wait_closed_called = True

    async def drain(self):
        pass


class FingerServerLimitOverrunFdLeakTests(unittest.TestCase):
    def test_limit_overrun_still_closes_the_connection(self):
        reader = _FakeReader(asyncio.LimitOverrunError('line too long', 65536))
        writer = _FakeWriter()
        asyncio.run(_handle(reader, writer))
        self.assertTrue(writer.closed,
                        'a LimitOverrunError from readline() must still result '
                        'in writer.close() -- this used to leak the fd entirely')
        self.assertTrue(writer.wait_closed_called)

    def test_timeout_still_closes_the_connection(self):
        """Confirms the pre-existing (already-working) case still works
        after the restructure -- not just the newly-fixed one."""
        reader = _FakeReader(asyncio.TimeoutError())
        writer = _FakeWriter()
        asyncio.run(_handle(reader, writer))
        self.assertTrue(writer.closed)
        self.assertTrue(writer.wait_closed_called)

    def test_unexpected_exception_mid_handler_still_closes_the_connection(self):
        """Defense in depth: the fix wraps the WHOLE handler body in one
        try/finally, not just a patch around the one known exception
        type -- any other unexpected failure mid-handler must still
        close the connection instead of leaking it."""
        class _RaisingReader:
            async def readline(self):
                return b'someuser\r\n'

        writer = _FakeWriter()
        with mock.patch('anetbbs.core.finger_server._flask_app',
                        side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                asyncio.run(_handle(_RaisingReader(), writer))
        self.assertTrue(writer.closed)
        self.assertTrue(writer.wait_closed_called)


if __name__ == '__main__':
    unittest.main()
