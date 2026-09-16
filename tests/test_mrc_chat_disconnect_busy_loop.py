"""Regression test for a real, live-reported Critical bug: a
disconnected MRC chat session (SSH client killed without /quit, or any
other transport drop) could spin the event-loop thread at 100% CPU
forever, freezing the entire BBS process for every user, with the dead
node never released.

Reported by a real ANetBBS operator, 2026-09-16, with a real py-spy
stack trace pointing at exactly this call chain: ssh_server.py's
reader.read() -> mrc_chat.py's _read_chat_line() -> _chat_loop() ->
_connect_and_chat() -> show_menu() -> menu_engine.py's
_act_chat_mrc() -> run_menu().

Root cause, confirmed directly against the source: MRCChat._read_chat_line()
(and its AsciiMRCChat/PetsciiMRCChat subclass overrides) returned the
empty STRING '' both on end-of-stream (`if not ch:`) and on a
ConnectionError/OSError -- but _chat_loop(), the only caller, already
had (and needed) a real tri-state contract: None means "the connection
is gone, stop reading" (`if line is None: break`), while '' means
"blank line submitted, keep looping" (`if not line: continue`).
asyncio.StreamReader.read() on an already-closed/EOF stream returns
b'' immediately rather than blocking, so a dead connection re-entered
this exact read()->''->continue->read() cycle with no throttling,
pinning one CPU core indefinitely and never releasing the node (the
loop never reaches the code path that would).

Fixed by returning None instead of '' from both return sites in each
of the three _read_chat_line() implementations, using the exact
tri-state contract _chat_loop() already expected.

Reuses the _QueuedReader/_FakeSession harness already proven in
tests/test_mrc_terminal_input_buffer_cap.py.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat
from anetbbs.features.mrc_chat_ascii import AsciiMRCChat
from anetbbs.features.mrc_chat_petscii import PetsciiMRCChat


class _QueuedReader:
    """Same as test_mrc_terminal_input_buffer_cap.py's own -- feeds
    pre-queued single-byte reads, returning b'' (EOF) once exhausted,
    exactly like a real asyncio StreamReader on a closed connection."""
    def __init__(self, data: bytes = b''):
        self._data = data
        self._pos = 0

    async def read(self, n=1):
        if self._pos >= len(self._data):
            return b''
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _RaisingReader:
    """Simulates a genuinely dropped transport where read() itself
    raises, rather than just returning EOF -- exercises
    MRCChat._read_chat_line()'s own `except (ConnectionError, OSError)`
    branch specifically."""
    def __init__(self, exc):
        self._exc = exc

    async def read(self, n=1):
        raise self._exc


class _FakeSession:
    def __init__(self, reader):
        self.user = {'username': 'tester'}
        self.written = []
        self.reader = reader
        self.petscii_width = 40

    async def write(self, text):
        self.written.append(text)


def _run(coro, timeout=5):
    """Wrap every call in a real timeout so a regression (the loop
    hanging/spinning forever) FAILS the test instead of hanging the
    whole test run -- the same discipline
    tests/test_door_idle_timeout_enforcement.py already uses for an
    analogous "must not hang" proof."""
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


class BaseMrcChatEofReturnsNoneTests(unittest.TestCase):
    def test_read_chat_line_returns_none_on_immediate_eof(self):
        chat = MRCChat(_FakeSession(_QueuedReader(b'')))
        chat._split_screen = False
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertIsNone(
            line, "must return None on EOF, not '' -- '' is "
                  "indistinguishable from a real blank Enter to "
                  "_chat_loop(), which is exactly the busy-loop bug")

    def test_read_chat_line_returns_none_on_connection_error(self):
        chat = MRCChat(_FakeSession(_RaisingReader(ConnectionError())))
        chat._split_screen = False
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertIsNone(line)

    def test_read_chat_line_returns_none_on_os_error(self):
        chat = MRCChat(_FakeSession(_RaisingReader(OSError())))
        chat._split_screen = False
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertIsNone(line)

    def test_normal_submitted_line_is_unaffected(self):
        """The fix must not turn a real, normal blank Enter press into
        a disconnect -- only genuine EOF/errors return None."""
        chat = MRCChat(_FakeSession(_QueuedReader(b'\r')))
        chat._split_screen = False
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertEqual(line, '')


class AsciiMrcChatEofReturnsNoneTests(unittest.TestCase):
    def test_read_chat_line_returns_none_on_immediate_eof(self):
        chat = AsciiMRCChat(_FakeSession(_QueuedReader(b'')))
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertIsNone(line)


class PetsciiMrcChatEofReturnsNoneTests(unittest.TestCase):
    def test_read_chat_line_returns_none_on_immediate_eof(self):
        chat = PetsciiMRCChat(_FakeSession(_QueuedReader(b'')))
        chat._handle = 'StingRay'
        line = _run(chat._read_chat_line())
        self.assertIsNone(line)


class ChatLoopDisconnectBusyLoopTests(unittest.TestCase):
    """The real end-to-end proof: drive _chat_loop() itself (not just
    _read_chat_line() in isolation) against a reader that's already at
    EOF, exactly simulating the reported scenario (client killed
    mid-MRC-session). Before the fix, this genuinely never returns --
    the wait_for timeout below is what makes that failure mode show up
    as a test FAILURE instead of hanging the test runner forever."""

    def test_chat_loop_exits_promptly_on_immediate_disconnect(self):
        chat = MRCChat(_FakeSession(_QueuedReader(b'')))
        chat._split_screen = False
        chat._handle = 'StingRay'
        chat._connected = True

        # _run()'s own asyncio.wait_for(timeout=5) is the actual
        # assertion here -- if _chat_loop() busy-loops instead of
        # exiting, this raises asyncio.TimeoutError and the test fails
        # (rather than hanging indefinitely, which the old '' bug
        # would otherwise do).
        _run(chat._chat_loop(), timeout=5)

        self.assertTrue(True, '_chat_loop() returned promptly')

    def test_chat_loop_exits_promptly_on_connection_error(self):
        chat = MRCChat(_FakeSession(_RaisingReader(ConnectionError())))
        chat._split_screen = False
        chat._handle = 'StingRay'
        chat._connected = True

        _run(chat._chat_loop(), timeout=5)


if __name__ == '__main__':
    unittest.main()
