"""Regression test for the inbound-listener twin of a real, live-caught
BinkP interop bug (see tests/test_binkp_interleaved_eob_credited.py for
the outbound-poller side, where it was root-caused against a real
SouthEast Star / binkd 1.1a-113 transcript).

anetbbs/echomail/binkp_server.py's _handle_connection() sends our own
outbound mail (if any) via _send_pkt_file() BEFORE draining the peer's
inbound stream via _receive_files() (v1.0b2.137 fix, so a peer waiting
to hear from us first isn't kept in silence). But a spec-compliant peer
sends its own M_EOB the instant ITS OWN outbound queue empties (FSP-1011
Table 5), independent of whether it's still waiting on us to M_GOT our
file to it -- so its M_EOB can arrive while _send_pkt_file()'s
wait-for-GOT loop is still running. That loop only checked for
CMD_GOT/SKIP/ERR before falling through to _consume_inbound_file_frame,
which only recognized CMD_FILE -- an interleaved CMD_EOB fell into the
"some other frame -- keep waiting" debug branch with zero record kept
anywhere. _receive_files(), called next, would then sit through a full
120-second timeout waiting for an EOB frame that had already arrived and
gone unrecognized.

Fixed by having _consume_inbound_file_frame() record any CMD_EOB into
the shared `state` dict (state['eob_count']) -- already threaded through
both _send_pkt_file() and _receive_files() via _handle_connection() --
and having _receive_files() check that count up front, returning
immediately instead of blocking, when the peer's EOB was already seen.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _cmd_payload(cmd, text=''):
    return bytes([cmd]) + text.encode('latin-1', errors='replace')


class ConsumeFrameRecordsEobTests(unittest.TestCase):
    def test_eob_frame_is_recorded_not_dropped(self):
        from anetbbs.echomail.binkp_server import (
            _consume_inbound_file_frame, CMD_EOB,
        )

        state = {'name': None, 'size': 0, 'buf': bytearray()}
        consumed = asyncio.run(_consume_inbound_file_frame(
            True, _cmd_payload(CMD_EOB), ('1.2.3.4', 1), None, state, []))

        self.assertEqual(state.get('eob_count'), 1)
        # Deliberately returns False -- the caller's own CMD_EOB branch
        # still performs the actual state transition (break out of its
        # loop, etc); this only ensures the count survives regardless of
        # which loop happened to be running when the frame arrived.
        self.assertFalse(consumed)

    def test_second_eob_increments_the_count(self):
        from anetbbs.echomail.binkp_server import (
            _consume_inbound_file_frame, CMD_EOB,
        )

        state = {'name': None, 'size': 0, 'buf': bytearray()}
        asyncio.run(_consume_inbound_file_frame(
            True, _cmd_payload(CMD_EOB), ('1.2.3.4', 1), None, state, []))
        asyncio.run(_consume_inbound_file_frame(
            True, _cmd_payload(CMD_EOB), ('1.2.3.4', 1), None, state, []))

        self.assertEqual(state['eob_count'], 2)


class ReceiveFilesSkipsWhenEobAlreadySeenTests(unittest.TestCase):
    class _NeverCalledReader:
        """Any call to readexactly()/read() fails the test -- proves
        _receive_files() didn't block waiting on the peer at all."""
        async def readexactly(self, n):
            raise AssertionError(
                'reader.readexactly() must not be called when the peer '
                'EOB was already recorded before _receive_files() started')

        async def read(self, n):
            raise AssertionError('reader.read() must not be called either')

    class _FakeWriter:
        def get_extra_info(self, key):
            return ('1.2.3.4', 1) if key == 'peername' else None
        def write(self, data):
            pass
        async def drain(self):
            pass

    def test_returns_immediately_when_eob_already_recorded(self):
        from anetbbs.echomail.binkp_server import _receive_files

        state = {'name': None, 'size': 0, 'buf': bytearray(), 'eob_count': 1}
        files = []
        result = asyncio.run(_receive_files(
            self._NeverCalledReader(), self._FakeWriter(),
            ('1.2.3.4', 1), state, files))

        self.assertIs(result, files)
        self.assertEqual(files, [])

    def test_does_not_skip_when_no_eob_recorded_yet(self):
        """Baseline / guard against a too-broad fix: with no prior EOB
        recorded, _receive_files() must still actually read from the
        reader (existing behavior, unchanged)."""
        from anetbbs.echomail.binkp_server import _receive_files, CMD_EOB

        class _OneEobReader:
            def __init__(self):
                self.calls = 0
            async def readexactly(self, n):
                self.calls += 1
                # First call: 2-byte frame header for a CMD_EOB frame of
                # length 1. Second call: the 1-byte CMD_EOB payload.
                if self.calls == 1:
                    return (0x8000 | 1).to_bytes(2, 'big')
                return bytes([CMD_EOB])
            async def read(self, n):
                return b''

        reader = _OneEobReader()
        state = {'name': None, 'size': 0, 'buf': bytearray()}
        files = []
        result = asyncio.run(_receive_files(
            reader, self._FakeWriter(), ('1.2.3.4', 1), state, files))

        self.assertIs(result, files)
        self.assertGreaterEqual(reader.calls, 1,
                                'must actually read frames when no EOB '
                                'was recorded beforehand')


if __name__ == '__main__':
    unittest.main()
