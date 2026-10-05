"""Regression test: both binkp_server.py's _receive_files() (answering
side) and binkp.py's _receive_messages() (dialing-out side) used to wait
up to 120s / 60s respectively for a new frame before giving up on a
peer that's gone quiet after its last file. Real live measurement
against a real hub (binkd/1.1a-113) showed the TCP connection itself
consistently dying ~15s after its last file -- far short of either of
those configured timeouts, meaning something OUTSIDE our own code (the
network path, not the hub's application logic) was silently killing
the idle connection well before we ever tried to speak again. Our own
confirmatory M_EOB (see the proactive-EOB fix in v1.0b2.148) was
therefore always being sent into an already-dead connection. Both
receive loops now use a much shorter wait (5s) so our confirmation goes
out while the link is still alive.

Updated for a real follow-up bug report (Winzlo/Clearing Houz,
2026-10-04): that 5s value assumed a transfer had already happened THIS
session. When we have nothing of our own queued outbound,
binkp.py's _receive_messages() is reached as the FIRST wait of the
whole session, not a post-transfer one -- a hub that's simply slow to
START its backlog (confirmed live: 30-40+ seconds) got cut off before
ever offering anything, logged as a clean success, backlog never
received. The client-side test below now covers both the ORIGINAL
post-transfer scenario (a file was already exchanged -- still shrinks
to 5s, unchanged) and the NEW first-wait scenario (nothing exchanged
yet -- uses the full self.timeout instead, exactly matching
_send_messages()'s own already-proven-sufficient ack-wait window).
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ServerShortIdleTimeoutTests(unittest.TestCase):
    """binkp_server.py's _receive_files()."""

    class _FakeReader:
        async def readexactly(self, n):
            raise asyncio.IncompleteReadError(partial=b'', expected=n)

    def test_uses_a_short_wait_not_120s(self):
        from anetbbs.echomail import binkp_server as mod

        captured_timeouts = []
        real_wait_for = asyncio.wait_for

        async def _spy_wait_for(coro, timeout=None):
            captured_timeouts.append(timeout)
            return await real_wait_for(coro, timeout=timeout)

        writer = None
        state = {'name': None, 'size': 0, 'buf': bytearray()}
        with patch.object(asyncio, 'wait_for', _spy_wait_for):
            asyncio.run(mod._receive_files(
                self._FakeReader(), writer, ('1.2.3.4', 1), state, []))

        self.assertTrue(captured_timeouts, 'expected at least one wait_for call')
        self.assertLess(captured_timeouts[0], 15,
            "receive loop's per-frame wait must be well under the ~15s "
            "window a real hub's connection was observed dying at -- "
            f"got {captured_timeouts[0]}")


class ClientShortIdleTimeoutTests(unittest.TestCase):
    """binkp.py's BinkPClient._receive_messages()."""

    @staticmethod
    def _make_client():
        from anetbbs.echomail.binkp import BinkPClient
        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._send_cmd = lambda cmd, text='': True
        return client

    class _FakeSocket:
        def __init__(self):
            self.timeouts_set = []
        def settimeout(self, value):
            self.timeouts_set.append(value)

    def test_post_transfer_still_shrinks_to_5s_when_a_file_was_already_sent_or_received(self):
        """The ORIGINAL v1.0b2.150 guarantee, unchanged: once a file has
        actually been exchanged this session (outbound traffic queued
        and sent, or an interleaved file received during an earlier
        wait), this receive phase's very first wait still shrinks to 5s
        -- exactly as it always has for every session that has outbound
        traffic, which is every session this fix is NOT about."""
        client = self._make_client()
        client._sock = self._FakeSocket()
        client._any_file_this_session = True  # a file already crossed the wire

        def _fake_recv(*a, **kw):
            raise ConnectionError('peer closed')
        client._recv_frame_logged = _fake_recv

        client._receive_messages(data_dir='/tmp')

        self.assertTrue(client._sock.timeouts_set)
        self.assertEqual(client._sock.timeouts_set[0], 5.0,
            "a session that already exchanged a file must still get the "
            "original short post-transfer timeout on the very first wait "
            f"-- got {client._sock.timeouts_set[0]}")

    def test_first_wait_uses_the_full_timeout_when_nothing_exchanged_yet(self):
        """Real bug report (Winzlo/Clearing Houz, 2026-10-04): a session
        with nothing queued outbound reaches this receive phase having
        exchanged zero files -- the short 5s timeout has no justification
        yet (nothing has happened for the hub to go quiet AFTER), and a
        hub slow to start its backlog (confirmed live: 30-40+ seconds)
        was getting cut off here before ever offering anything. The
        first wait must use the full self.timeout (60s default) instead,
        matching _send_messages()'s own already-proven-sufficient
        ack-wait window."""
        client = self._make_client()
        client._sock = self._FakeSocket()
        self.assertFalse(client._any_file_this_session,
            'fresh client must start with nothing exchanged yet')

        def _fake_recv(*a, **kw):
            raise ConnectionError('peer closed')
        client._recv_frame_logged = _fake_recv

        client._receive_messages(data_dir='/tmp')

        self.assertTrue(client._sock.timeouts_set)
        self.assertEqual(client._sock.timeouts_set[0], client.timeout,
            "the first wait of a session with nothing exchanged yet must "
            "use the full self.timeout, not the short post-transfer value "
            f"-- got {client._sock.timeouts_set[0]} (self.timeout="
            f"{client.timeout})")
        self.assertGreaterEqual(client.timeout, 15,
            'sanity check: self.timeout really is the generous default, '
            'not itself already short')

    def test_retightens_to_5s_the_moment_the_hubs_first_file_arrives(self):
        """Once the hub's first CMD_FILE header arrives mid-loop (even
        though we started this wait at the full self.timeout, per the
        above), every subsequent wait must go back to the original 5s
        protection -- the fix only widens the very FIRST wait of an
        empty-outbound session, it doesn't loosen post-transfer teardown
        detection for the rest of that same session."""
        from anetbbs.echomail.binkp import CMD_FILE

        client = self._make_client()
        client._sock = self._FakeSocket()
        self.assertFalse(client._any_file_this_session)

        file_header = bytes([CMD_FILE]) + b'testfile.pkt 4 1700000000 0'
        responses = iter([
            (True, file_header),   # hub finally starts its backlog
        ])

        def _fake_recv(*a, **kw):
            try:
                return next(responses)
            except StopIteration:
                raise ConnectionError('peer closed')
        client._recv_frame_logged = _fake_recv

        client._receive_messages(data_dir='/tmp')

        timeouts = client._sock.timeouts_set
        self.assertGreaterEqual(len(timeouts), 2,
            f'expected at least 2 settimeout() calls, got {timeouts}')
        self.assertEqual(timeouts[0], client.timeout,
            'first wait (nothing exchanged yet) must use the full timeout')
        self.assertEqual(timeouts[-1], 5.0,
            "once the hub's first file header arrives, every subsequent "
            f"wait must re-tighten to 5.0s -- got {timeouts}")


if __name__ == '__main__':
    unittest.main()
