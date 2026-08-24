"""Regression tests for outbound BinkP bundle compression -- Jerry's
follow-up ask to actually complete the "outbound bundles are always
sent uncompressed" limitation documented in docs/06-echomail.md.

anetbbs/echomail/binkp.py's _build_outbound_bundle() is the shared
helper both BinkPClient (outbound dial) and binkp_server.py (inbound
listener sending its own queued mail back) use. Naming convention
verified against the real, published FTSC document (WaZOO Filename
Conventions, ftsc.org/docs/fts-0006.002) and Synchronet's own
reference docs (wiki.synchro.net/ref:fidonet_files) -- both state a
compressed bundle must use the day-of-week 2-letter + sequence-digit
extension (".Mo0" etc), never ".zip" or any other common archive
suffix.
"""
import sys
import unittest
import zipfile
import io
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.echomail.binkp import _build_outbound_bundle, _ARCMAIL_DAY_CODES


class BuildOutboundBundleTests(unittest.TestCase):
    def test_uncompressed_returns_plain_pkt_unchanged(self):
        data = b'some fts-0001 packet bytes'
        filename, payload = _build_outbound_bundle(data, compress=False)
        self.assertTrue(filename.endswith('.pkt'))
        self.assertEqual(payload, data)

    def test_compressed_filename_is_never_dot_zip(self):
        filename, _payload = _build_outbound_bundle(b'x', compress=True)
        self.assertFalse(filename.lower().endswith('.zip'))

    def test_compressed_filename_uses_real_arcmail_day_code(self):
        # Pin "today" to a known Wednesday (2026-08-19) so the day
        # code is deterministic regardless of when this test runs.
        fixed_wed = datetime(2026, 8, 19, 12, 0, 0)
        with patch('anetbbs.echomail.binkp.datetime') as mock_dt:
            mock_dt.utcnow.return_value = fixed_wed
            filename, _payload = _build_outbound_bundle(b'x', compress=True)
        self.assertTrue(filename.lower().endswith('.we0'),
                        f'expected a .We0-style extension, got {filename}')

    def test_compressed_payload_is_a_real_readable_zip_containing_the_pkt(self):
        original = b'FTS-0001 packet payload, arbitrary bytes \x00\x01\x02'
        filename, payload = _build_outbound_bundle(original, compress=True)
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = zf.namelist()
            self.assertEqual(len(names), 1)
            self.assertTrue(names[0].endswith('.pkt'))
            self.assertEqual(zf.read(names[0]), original)

    def test_day_codes_cover_every_weekday_with_no_dupes(self):
        self.assertEqual(len(_ARCMAIL_DAY_CODES), 7)
        self.assertEqual(len(set(_ARCMAIL_DAY_CODES)), 7)
        # datetime.weekday(): Monday=0 .. Sunday=6
        self.assertEqual(_ARCMAIL_DAY_CODES[0], 'Mo')
        self.assertEqual(_ARCMAIL_DAY_CODES[6], 'Su')


if __name__ == '__main__':
    unittest.main()
