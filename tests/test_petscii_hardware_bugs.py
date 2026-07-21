"""Regression tests for two real bugs found on first real-hardware
testing of the PETSCII terminal mode (v1.0b2.177):

1. Login always failed with "invalid username or password" even against
   a real, correct account. Root cause: a real C64 keyboard's PETSCII
   byte code for a letter key depends on which charset ROM is currently
   selected (a KERNAL keyboard-decode-table difference, not just a
   display/rendering choice) -- unshifted letters send the UPPERCASE
   byte range in the default power-on "graphics" charset, and only send
   the lowercase range once the upper/lowercase charset (0x0E) is
   selected. The charset switch used to be sent only AFTER a successful
   login (in petscii_ui.py's menu stub) -- so during login itself, every
   password typed arrived silently case-flattened to uppercase and
   authentication always failed. Fixed by sending the switch as the
   very first bytes of the connection (petscii_server.py's
   handle_connection(), before session.start() ever runs).

2. Backspace/delete didn't work at all for PETSCII sessions -- a real
   C64 keyboard's DEL/INST key sends PETSCII 0x14, not ASCII 0x7f/0x08,
   and the erase-in-place echo sequence needs PETSCII cursor-left
   codes, not ASCII backspace (0x08 means something else entirely on
   real hardware -- "disable Shift-Commodore", not erase-in-place).
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession
from anetbbs.core.petscii_server import PetsciiServer
from anetbbs.features import petscii_codec as pc


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
    models a real client sending one keystroke per TCP segment."""
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


class CharsetSwitchTimingTests(unittest.TestCase):
    def test_lowercase_charset_sent_before_session_start(self):
        # The exact bug: the charset switch used to only happen AFTER
        # login succeeded, so login itself ran in the wrong charset.
        # This confirms handle_connection() writes it BEFORE
        # session.start() is ever awaited.
        server = PetsciiServer(config={}, width=40)
        writer = _FakeWriter()
        order = []

        async def _fake_start(self):
            order.append('start')
        # Patch BBSSession.start on the class so our real write() call
        # (which happens before start()) still goes through unmocked.
        with patch.object(BBSSession, 'start', new=AsyncMock(side_effect=lambda: order.append('start'))):
            asyncio.run(server.handle_connection(_QueueReader([]), writer))

        self.assertIn(bytes([0x0E]), bytes(writer.written),
                     'LOWERCASE_CHARSET (0x0E) must be written to the wire')
        # It must be the very first byte written -- before anything
        # session.start() itself would ever produce.
        self.assertEqual(bytes(writer.written)[0], 0x0E)


class PetsciiBackspaceTests(unittest.TestCase):
    def test_read_line_recognizes_petscii_delete_key(self):
        # Types 'a', 'b', DEL (0x14 -- the real C64 key code), 'c', Enter.
        # Expected result: "AC" (the 'b' was deleted) -- letters come out
        # case-swapped per petscii_codec's real-hardware-confirmed
        # keyboard-decode behavior (see test_petscii_codec.py).
        session, writer = _make_session(
            [b'a', b'b', b'\x14', b'c', b'\r'],
            forced_term_mode='petscii')
        result = asyncio.run(session.read_line(''))
        self.assertEqual(result, 'AC')

    def test_read_line_backspace_echo_uses_petscii_cursor_left(self):
        session, writer = _make_session(
            [b'x', b'\x14', b'\r'], forced_term_mode='petscii')
        asyncio.run(session.read_line(''))
        cursor_left_byte = bytes([ord(pc.CURSOR_LEFT)])
        self.assertIn(cursor_left_byte, bytes(writer.written))
        self.assertNotIn(b'\x08', bytes(writer.written),
                         'must not echo ASCII backspace (0x08) -- that byte means '
                         '"disable Shift-Commodore" on real PETSCII hardware, not erase')

    def test_ascii_ansi_session_backspace_unaffected(self):
        # Regression guard: the petscii branch must not change existing
        # ANSI/ASCII sessions' backspace handling (0x7f/0x08).
        session, writer = _make_session([b'a', b'\x7f', b'b', b'\r'])
        result = asyncio.run(session.read_line(''))
        self.assertEqual(result, 'b')
        self.assertIn(b'\x08', bytes(writer.written))

    def test_read_password_recognizes_petscii_delete_key(self):
        # Letters come out case-swapped, same reasoning as read_line() above.
        session, writer = _make_session(
            [b's', b'e', b'c', b'\x14', b'\x14', b'x', b'\r'],
            forced_term_mode='petscii')
        result = asyncio.run(session.read_password(''))
        self.assertEqual(result, 'SX')

    def test_read_password_echo_uses_petscii_cursor_left_not_ascii_backspace(self):
        session, writer = _make_session(
            [b'z', b'\x14', b'\r'], forced_term_mode='petscii')
        asyncio.run(session.read_password(''))
        cursor_left_byte = bytes([ord(pc.CURSOR_LEFT)])
        self.assertIn(cursor_left_byte, bytes(writer.written))
        self.assertNotIn(b'\x08', bytes(writer.written))

    def test_read_password_ascii_session_unaffected(self):
        session, writer = _make_session([b'z', b'\x7f', b'y', b'\r'])
        result = asyncio.run(session.read_password(''))
        self.assertEqual(result, 'y')


if __name__ == '__main__':
    unittest.main()
