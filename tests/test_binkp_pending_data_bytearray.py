"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): BinkPClient._consume_inbound_file_frame()
accumulated an inbound file's data frames via `state['pending_data'] +=
data` -- bytes += is O(n) per append (a full reallocation+copy of the
whole buffer so far), making frame-by-frame accumulation of a large
inbound file O(n^2) total work as it grows toward the 100MB cap. Real
CPU/memory churn on exactly the large-batch catch-up transfers involved
in the 2026-08-30 hub-queue incident. binkp_server.py's own equivalent
inbound path already uses a bytearray + .extend() (O(1) amortized) for
this same reason -- binkp.py's outbound-poller side was missed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.echomail.binkp import BinkPClient, CMD_FILE


class PendingDataBytearrayTests(unittest.TestCase):
    def _client(self):
        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._send_cmd = lambda cmd, text='': True
        client._import_completed = lambda fname, buf, inbound_dir: (
            self._captured.__setitem__('fname', fname) or
            self._captured.__setitem__('buf', buf) or [])
        return client

    def test_pending_data_is_a_bytearray_not_bytes(self):
        """Guards against a future edit reverting to a plain bytes
        `pending_data`, which would silently reintroduce the O(n^2)
        `+=` accumulation this fix removes."""
        state = {'pending_file': None, 'pending_size': 0,
                'pending_data': bytearray()}
        self.assertIsInstance(state['pending_data'], bytearray)

    def test_file_is_correctly_reassembled_across_multiple_frames(self):
        self._captured = {}
        client = self._client()
        state = {'pending_file': None, 'pending_size': 0,
                'pending_data': bytearray()}

        # M_FILE offer: filename size timestamp
        client._consume_inbound_file_frame(
            True, bytes([CMD_FILE]) + b'test.pkt 12 1700000000', state)
        self.assertIsInstance(state['pending_data'], bytearray)

        # Three data frames -- confirms .extend() (not +=) still
        # produces byte-for-byte correct reassembly across multiple
        # chunks, not just a single append.
        client._consume_inbound_file_frame(False, b'ABCD', state)
        client._consume_inbound_file_frame(False, b'EFGH', state)
        client._consume_inbound_file_frame(False, b'IJKL', state)

        self.assertEqual(self._captured.get('fname'), 'test.pkt')
        self.assertEqual(self._captured.get('buf'), b'ABCDEFGHIJKL')
        self.assertIsInstance(self._captured.get('buf'), bytes,
                              'the buffer handed to _import_completed() '
                              'must be real bytes, not a bytearray, for '
                              'downstream consumers')


if __name__ == '__main__':
    unittest.main()
