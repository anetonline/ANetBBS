"""Regression test for a real gap found in this round's security/
performance audit: anetbbs/echomail/binkp_server.py's MAX_INBOUND_FILE_SIZE
bounds any ONE file offered during an inbound BinkP session, but nothing
bounded the CUMULATIVE bytes accepted across a single session's whole
receive phase. inbound_files (the list every accepted file's bytes get
appended to, held in memory until the session ends -- import is
deliberately deferred until after the socket closes, see
_handle_connection's own "7."/"8." comments) had no total-session cap of
its own.

This matters because the file-receive path is reachable with ZERO
authentication: an unrecognized peer is accepted as "anonymous
crashmail" (matching real FTN nodelist convention) and still proceeds
to file receive (see MAX_INBOUND_FILE_SIZE's own comment). A single
long-lived anonymous connection offering many just-under-the-per-file-cap
files back to back -- nothing stopped that as long as the peer kept
talking within the per-frame idle timeout -- could still exhaust this
process's memory one file at a time.

Fixed by tracking a running `state['session_total']` (the shared dict
already threaded through every _consume_inbound_file_frame() call site
for one session) and refusing (M_SKIP) any new file offer that would
push it past MAX_INBOUND_SESSION_BYTES -- same "reject before ever
allocating a buffer for it" posture as the existing per-file check.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _cmd_payload(cmd, text=''):
    return bytes([cmd]) + text.encode('latin-1', errors='replace')


class _FakeWriter:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass


class SessionByteCapTests(unittest.TestCase):
    def setUp(self):
        from anetbbs.echomail import binkp_server as mod
        self.mod = mod
        self._orig_cap = mod.MAX_INBOUND_SESSION_BYTES
        # Small, testable cap -- the real default (1GB) would make this
        # test impractically slow/memory-hungry.
        mod.MAX_INBOUND_SESSION_BYTES = 1000
        self.addCleanup(setattr, mod, 'MAX_INBOUND_SESSION_BYTES', self._orig_cap)

    def _offer_and_complete(self, writer, state, files, peer, name, payload):
        mod = self.mod

        async def _run():
            await mod._consume_inbound_file_frame(
                True,
                _cmd_payload(mod.CMD_FILE, f'{name} {len(payload)} 1700000000 0'),
                peer, writer, state, files)
            if not state.get('skip'):
                await mod._consume_inbound_file_frame(
                    False, payload, peer, writer, state, files)

        asyncio.run(_run())

    def test_files_under_session_cap_are_all_accepted(self):
        writer = _FakeWriter()
        state = {'name': None, 'size': 0, 'buf': bytearray()}
        files = []
        peer = ('198.51.100.7', 24554)
        self._offer_and_complete(writer, state, files, peer, 'a.pkt', b'x' * 300)
        self._offer_and_complete(writer, state, files, peer, 'b.pkt', b'y' * 300)
        self.assertEqual(len(files), 2)
        self.assertEqual(state.get('session_total'), 600)
        self.assertFalse(state.get('skip'))

    def test_file_that_would_exceed_session_cap_is_skipped(self):
        mod = self.mod
        writer = _FakeWriter()
        state = {'name': None, 'size': 0, 'buf': bytearray()}
        files = []
        peer = ('198.51.100.7', 24554)

        # First file: 900 bytes, well under both the per-file cap and the
        # (test-lowered) 1000-byte session cap.
        self._offer_and_complete(writer, state, files, peer, 'a.pkt', b'x' * 900)
        self.assertEqual(len(files), 1)

        # Second file: only 200 bytes -- trivially under the per-file cap
        # on its own -- but 900 + 200 = 1100 exceeds the 1000-byte session
        # cap, so this offer must be refused before any buffer is even
        # allocated for it.
        async def _offer_second():
            await mod._consume_inbound_file_frame(
                True, _cmd_payload(mod.CMD_FILE, 'c.pkt 200 1700000000 0'),
                peer, writer, state, files)

        asyncio.run(_offer_second())

        self.assertTrue(state.get('skip'),
                        'a file offer that would push the session total '
                        'over the cap must be rejected via SKIP')
        self.assertEqual(len(files), 1,
                         'the over-cap file must never be appended to files')

        # A real M_SKIP command frame must actually have been sent back --
        # not just the internal state flag flipped with nothing on the wire.
        last_frame = writer.sent[-1]
        self.assertEqual(last_frame[2], mod.CMD_SKIP)


if __name__ == '__main__':
    unittest.main()
