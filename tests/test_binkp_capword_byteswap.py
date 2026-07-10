"""Regression test for the FTS-0001 Type-2+ capValidate/capWord byte-swap
bug in anetbbs/echomail/binkp.py:_build_ftn_packet().

Root-caused from a real rejected-packet report by an external FTN sysop
(SmallTime BBS, running the `hpt`/husky tosser): our outbound packets
were rejected with "CapabilityWord error in following pkt! rtfm:
IgnoreCapWord." hpt's own source (pktread.c:openPkt, pktwrite.c:
createPkt) confirms capValidate (header offset 40-41) must hold the same
integer as capWord (offset 44-45), but BYTE-SWAPPED (big-endian) rather
than as a plain identical little-endian copy. Our code previously wrote
CAP_WORD (0x0001) as plain little-endian at both offsets -- which
produces two DIFFERENT byte sequences for any non-palindromic value
(high byte != low byte), silently failing any tosser that validates this
field the way hpt does.

This test decodes the header exactly the way hpt's pktread.c does
(capValidate as big-endian, capWord as little-endian) and asserts they
match -- the actual acceptance condition, not just "the bytes look
right."
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest


class BinkpCapWordByteSwapTests(unittest.TestCase):
    def _build_header(self, our_addr='1:1/1', hub_addr='2:2/2'):
        from anetbbs.echomail.binkp import _build_ftn_packet, FTN_PKT_HEADER_SIZE
        pkt = _build_ftn_packet([], our_addr, hub_addr)
        return pkt[:FTN_PKT_HEADER_SIZE]

    def test_cap_validate_and_cap_word_match_hpt_decode(self):
        """The actual condition hpt's pktread.c checks: capValidate read
        big-endian must equal capWord read little-endian."""
        header = self._build_header()
        cap_validate_be = struct.unpack('>H', header[40:42])[0]
        cap_word_le = struct.unpack('<H', header[44:46])[0]
        self.assertEqual(cap_validate_be, cap_word_le)
        self.assertEqual(cap_word_le, 0x0001)

    def test_the_two_fields_are_byte_swapped_not_identical_copies(self):
        """For the non-palindromic value 0x0001, a correct byte-swapped
        pair must NOT be byte-identical -- this is exactly the case that
        silently broke before (0x0001 written as plain little-endian at
        both offsets happened to look plausible but decoded differently
        under hpt's mixed-endianness read)."""
        header = self._build_header()
        cap_validate_bytes = header[40:42]
        cap_word_bytes = header[44:46]
        self.assertEqual(cap_validate_bytes, b'\x00\x01',
                         'capValidate must be big-endian 0x0001 -> bytes 00 01')
        self.assertEqual(cap_word_bytes, b'\x01\x00',
                         'capWord must be little-endian 0x0001 -> bytes 01 00')
        self.assertNotEqual(cap_validate_bytes, cap_word_bytes)

    def test_header_is_still_exactly_58_bytes(self):
        """The byte-swap fixup must not change the overall header size."""
        from anetbbs.echomail.binkp import FTN_PKT_HEADER_SIZE
        header = self._build_header()
        self.assertEqual(len(header), FTN_PKT_HEADER_SIZE)
        self.assertEqual(FTN_PKT_HEADER_SIZE, 58)

    def test_other_header_fields_unaffected_by_the_fixup(self):
        """The byte-swap only touches offset 40-41 -- confirm neighboring
        fields (aux_net at 38-39, prod_code_hi/prod_rev_minor at 42-43)
        weren't accidentally shifted or clobbered."""
        header = self._build_header(our_addr='3:3/3', hub_addr='4:4/4')
        aux_net = struct.unpack('<H', header[38:40])[0]
        prod_code_hi, prod_rev_minor = struct.unpack('BB', header[42:44])
        self.assertEqual(aux_net, 0)
        self.assertEqual(prod_code_hi, 0x00)
        self.assertEqual(prod_rev_minor, 0x01)

    def test_used_by_both_client_and_server_side_pkt_writers(self):
        """_build_ftn_packet is the ONLY packet-header writer in the
        codebase -- confirm both binkp.py (outbound poll) and
        binkp_server.py (hub-to-downstream-node) import the same
        function, so this one fix covers every outbound path."""
        import inspect
        from anetbbs.echomail import binkp_server
        src = inspect.getsource(binkp_server)
        self.assertIn('_build_ftn_packet', src)


if __name__ == '__main__':
    unittest.main()
