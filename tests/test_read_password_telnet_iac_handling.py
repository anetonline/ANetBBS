"""Regression test: BBSSession.read_password() (anetbbs/core/session.py)
used to read directly via self.reader.read(1), bypassing read_raw()/
handle_telnet_command() -- the same telnet-IAC stripping read_line()/
read_key() already route through. IAC (0xFF) is > b' ', so it passed
the `ch < b' '` control-byte filter untouched. Most real telnet/SSH
clients send an unprompted IAC SB NAWS ... IAC SE the instant a user
resizes their terminal window -- an entirely ordinary action, e.g.
maximizing the window while sitting at a "Password: " prompt -- and
those raw negotiation bytes landed in the password buffer verbatim,
silently corrupting whatever was typed next. Found in a security/
performance audit.

Fixed by routing read_password() through read_raw() the same way
read_line() already does.
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
    """Feeds pre-queued byte chunks, one read() call at a time --
    ignores the requested `n`, so a multi-byte chunk queued as one
    item (e.g. a complete IAC SB NAWS ... IAC SE sequence) arrives to
    handle_telnet_command() in a single call, exactly as it would if a
    real client's terminal-resize negotiation landed in one TCP
    segment (the common case)."""
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


# A real IAC SB NAWS negotiation: IAC SB NAWS cols(2 bytes) rows(2 bytes)
# IAC SE -- exactly what most telnet/SSH clients send unprompted the
# instant a user resizes their terminal window.
_IAC = 0xFF
_SB = 0xFA
_SE = 0xF0
_NAWS = 0x1F
_NAWS_RESIZE_80x24 = bytes([_IAC, _SB, _NAWS, 0x00, 0x50, 0x00, 0x18, _IAC, _SE])


class ReadPasswordTelnetIacHandlingTests(unittest.TestCase):
    def test_naws_resize_mid_password_does_not_corrupt_the_buffer(self):
        # User types "sec", their client fires a NAWS resize (window
        # maximized mid-typing), then they finish typing "ret" + Enter.
        # Delivered as one combined chunk per _QueueReader.read() call --
        # a real client's negotiation CAN arrive as a single TCP segment
        # (see the byte-at-a-time variant below for the other realistic
        # case, where it doesn't).
        chunks = ([bytes([c]) for c in b'sec']
                 + [_NAWS_RESIZE_80x24]
                 + [bytes([c]) for c in b'ret']
                 + [b'\r'])
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(result, 'secret',
                         'a mid-entry NAWS negotiation must be stripped, '
                         'not landed in the password buffer as literal '
                         'garbage bytes')

    def test_naws_resize_before_any_typing_does_not_corrupt_the_buffer(self):
        chunks = ([_NAWS_RESIZE_80x24]
                 + [bytes([c]) for c in b'hunter2']
                 + [b'\r'])
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(result, 'hunter2')

    def test_naws_resize_split_byte_by_byte_still_does_not_corrupt_the_buffer(self):
        """The realistic production delivery pattern: confirmed
        empirically (see handle_telnet_command()'s own docstring) that
        asyncio.StreamReader.read(1) -- what read_raw(1) actually calls
        -- never returns more than 1 byte per call, even when a whole
        multi-byte sequence is already buffered on the socket. Each
        byte of the NAWS negotiation arrives as its OWN separate chunk
        here, exercising handle_telnet_command()'s cross-call
        buffering (self.telnet_command_buffer) rather than relying on
        the whole sequence conveniently arriving in one read()."""
        chunks = ([bytes([c]) for c in b'sec']
                 + [bytes([b]) for b in _NAWS_RESIZE_80x24]
                 + [bytes([c]) for c in b'ret']
                 + [b'\r'])
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(result, 'secret')

    def test_ordinary_password_with_no_negotiation_is_unaffected(self):
        chunks = [bytes([c]) for c in b'correcthorse'] + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(result, 'correcthorse')

    def test_backspace_still_works_after_the_fix(self):
        # 'a', 'b', backspace, 'c', Enter -> "ac"
        chunks = ([bytes([c]) for c in b'ab']
                 + [b'\x7f']
                 + [bytes([c]) for c in b'c']
                 + [b'\r'])
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(result, 'ac')


if __name__ == '__main__':
    unittest.main()
