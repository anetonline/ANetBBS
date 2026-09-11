"""Regression test: MRCChat._read_chat_line() (and its AsciiMRCChat/
PetsciiMRCChat overrides) reads raw bytes straight off self.session.reader
one at a time, accumulating into a buffer with NO length cap while
waiting for '\\r'/'\\n' -- unlike core/session.py's own read_line(),
which was already fixed for this exact bug class in a prior
auth-security audit (see that method's own docstring: "no cap existed
on line length ... a client that never sends \\r could hold this loop
open indefinitely, growing the buffer without bound"). _read_chat_line()
bypasses read_line() entirely (see mrc_chat_petscii.py's module
docstring for why -- AFK avoidance), so it never inherited that fix.

Found in a security/performance audit. Fixed by capping the buffer at
MAX_CHAT_INPUT_LEN (2048, matching read_line()'s own cap) in all three
implementations -- extra input beyond the cap is silently dropped
(still consumed from the stream, still echoed) rather than growing the
buffer forever, exactly like read_line()'s own documented behavior.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat, MAX_CHAT_INPUT_LEN
from anetbbs.features.mrc_chat_ascii import AsciiMRCChat
from anetbbs.features.mrc_chat_petscii import PetsciiMRCChat


class _QueuedReader:
    """Feeds pre-queued single-byte reads, like a real asyncio
    StreamReader would for a terminal session that never sends a line
    terminator."""
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, n=1):
        if self._pos >= len(self._data):
            return b''
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeSession:
    def __init__(self, reader_bytes=b''):
        self.user = {'username': 'tester'}
        self.written = []
        self.reader = _QueuedReader(reader_bytes)
        self.petscii_width = 40

    async def write(self, text):
        self.written.append(text)


def _run(coro):
    return asyncio.run(coro)


# 500 bytes over the cap, no terminator -- exercises the accumulation
# loop past MAX_CHAT_INPUT_LEN; the base-class test below inspects
# self._input_buf directly (the loop returns '' on EOF regardless of
# buffer size, so the return value alone wouldn't distinguish
# fixed/unfixed code here).
_OVERSIZE = b'A' * (MAX_CHAT_INPUT_LEN + 500)
# Same, but terminated with '\r' -- needed for the Ascii/Petscii
# subclass tests below, which only expose their accumulated buffer
# through the returned line (a local variable, not stored on self).
_OVERSIZE_TERMINATED = _OVERSIZE + b'\r'


class BaseMrcChatInputBufferCapTests(unittest.TestCase):
    def test_input_buf_capped_at_max_chat_input_len(self):
        chat = MRCChat(_FakeSession(_OVERSIZE))
        chat._split_screen = False  # makes _draw_input_line/_draw_status_line cheap no-ops
        chat._handle = 'StingRay'
        _run(chat._read_chat_line())
        self.assertLessEqual(len(chat._input_buf), MAX_CHAT_INPUT_LEN)

    def test_characters_within_cap_still_all_accepted(self):
        data = b'B' * 100 + b'\r'
        chat = MRCChat(_FakeSession(data))
        chat._split_screen = False
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertEqual(line, 'B' * 100)


class AsciiMrcChatInputBufferCapTests(unittest.TestCase):
    def test_input_buf_capped_at_max_chat_input_len(self):
        chat = AsciiMRCChat(_FakeSession(_OVERSIZE_TERMINATED))
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertLessEqual(len(line), MAX_CHAT_INPUT_LEN)
        self.assertLess(len(line), len(_OVERSIZE_TERMINATED) - 1)


class PetsciiMrcChatInputBufferCapTests(unittest.TestCase):
    def test_input_buf_capped_at_max_chat_input_len(self):
        chat = PetsciiMRCChat(_FakeSession(_OVERSIZE_TERMINATED))
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertLessEqual(len(line), MAX_CHAT_INPUT_LEN)
        self.assertLess(len(line), len(_OVERSIZE_TERMINATED) - 1)


if __name__ == '__main__':
    unittest.main()
