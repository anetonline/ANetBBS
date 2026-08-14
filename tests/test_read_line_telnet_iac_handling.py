"""Regression test: BBSSession.read_line() (anetbbs/core/session.py)
already routed through read_raw()/handle_telnet_command(), but its own
`if not char: raise CarrierLost(...)` check made the same wrong
assumption read_password()'s did: that read_raw() only ever returns
empty when the connection is truly gone. In fact read_raw() can
legitimately return b'' for a single call whose byte was consumed
entirely as telnet negotiation (e.g. the final byte of an IAC SB NAWS
... IAC SE resize sequence) -- not a disconnect. Once
handle_telnet_command() gained real cross-call buffering (so a
sequence delivered one byte per read() actually gets recognized as a
complete unit), this became far more likely to trigger: the LAST byte
of every complete negotiation sequence now produces exactly this
"real byte consumed, nothing to hand back yet" case. Before this fix,
a user resizing their terminal mid-username (or at any other read_line
prompt) got disconnected with CarrierLost even though the connection
was perfectly healthy. Found in a security/performance audit.
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
    ignores the requested `n`, matching the existing fixture pattern
    used across this test suite's other session I/O tests."""
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


class ReadLineTelnetIacHandlingTests(unittest.TestCase):
    def test_naws_resize_split_byte_by_byte_does_not_disconnect(self):
        """The realistic production delivery pattern -- each byte of
        the negotiation arrives as its own separate chunk, exercising
        handle_telnet_command()'s cross-call buffering. Before the
        fix, the final byte of the sequence resolves to an empty
        read_raw() return, which read_line() wrongly raised
        CarrierLost for."""
        chunks = ([bytes([c]) for c in b'admin']
                 + [bytes([b]) for b in _NAWS_RESIZE_80x24]
                 + [bytes([c]) for c in b'user']
                 + [b'\r'])
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_line())
        self.assertEqual(result, 'adminuser',
                         'a mid-entry NAWS negotiation split byte-by-byte '
                         'must not be mistaken for a client disconnect')

    def test_naws_resize_combined_chunk_does_not_disconnect(self):
        chunks = ([bytes([c]) for c in b'foo']
                 + [_NAWS_RESIZE_80x24]
                 + [bytes([c]) for c in b'bar']
                 + [b'\r'])
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_line())
        self.assertEqual(result, 'foobar')

    def test_ordinary_line_with_no_negotiation_is_unaffected(self):
        chunks = [bytes([c]) for c in b'hello world'] + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_line())
        self.assertEqual(result, 'hello world')


if __name__ == '__main__':
    unittest.main()
