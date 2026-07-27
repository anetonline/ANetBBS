"""Regression test: BBSSession.read_line()/read_password() (anetbbs/core/
session.py) had no cap on input length -- used for username/email/
security-answer/password prompts among many other things, a client that
never sends \\r could hold the read loop open indefinitely, growing the
in-memory buffer without bound (a per-connection memory DoS). Found in a
full auth-security audit.

Fixed by capping accumulated length (2048 for read_line, 256 for
read_password) -- extra input beyond the cap is silently dropped (still
consumed from the stream so the loop keeps making progress) rather than
raising, so a long paste just gets truncated instead of the whole
read failing outright.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession


class _FakeWriter:
    def __init__(self, peer=('1.2.3.4', 1234)):
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


class _QueueReader:
    """Feeds pre-queued byte chunks, one read() call at a time."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''

    async def readexactly(self, n):
        if self._chunks:
            chunk = self._chunks.pop(0)
            if len(chunk) < n:
                raise asyncio.IncompleteReadError(chunk, n)
            return chunk
        raise asyncio.IncompleteReadError(b'', n)


def _make_session(reader_chunks, **kwargs):
    reader = _QueueReader(reader_chunks)
    writer = _FakeWriter()
    session = BBSSession(reader, writer, config={}, **kwargs)
    return session, writer


class ReadLineLengthCapTests(unittest.TestCase):
    def test_input_beyond_max_len_is_truncated_not_unbounded(self):
        # 3000 'A' bytes (one per read() call, matching real byte-at-a-
        # time terminal delivery) followed by Enter -- well past the
        # default 2048 cap.
        chunks = [b'A'] * 3000 + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_line())
        self.assertEqual(len(result), 2048)

    def test_normal_short_line_unaffected(self):
        chunks = list(b'hello') + [b'\r']
        chunks = [bytes([c]) for c in b'hello'] + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_line())
        self.assertEqual(result, 'hello')

    def test_custom_max_len_is_honored(self):
        chunks = [bytes([c]) for c in b'abcdefghij'] + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_line(max_len=5))
        self.assertEqual(result, 'abcde')


class ReadPasswordLengthCapTests(unittest.TestCase):
    def test_input_beyond_max_len_is_truncated_not_unbounded(self):
        chunks = [b'x'] * 500 + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(len(result), 256)

    def test_normal_short_password_unaffected(self):
        chunks = [bytes([c]) for c in b'correcthorse'] + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(result, 'correcthorse')


if __name__ == '__main__':
    unittest.main()
