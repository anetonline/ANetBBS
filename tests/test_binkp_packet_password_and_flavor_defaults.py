"""Regression/feature tests: a real sysop asked for (1) a spot to
configure a per-network FTS-0001 packet-header password, distinct from
the BinkP session password, and (2) per-network defaults for the
Crash/Hold/Direct netmail delivery flavors, independently selectable.

Both are wired into `_build_ftn_packet()` (anetbbs/echomail/binkp.py),
the single packet-header writer used by every outbound path (already
confirmed shared by test_binkp_capword_byteswap.py). Extra care taken
here because this touches the same packet-writing code from this
session's whole BinkP resend-loop saga: every existing header field
(capValidate/capWord byte-swap, header size) is re-asserted alongside
the new fields so a regression in either direction would fail loudly.
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeMsg:
    """Mirrors the same minimal shape used by
    test_binkp_netmail_point_address.py's _FakeMsg -- .area=None marks
    it as netmail (no AREA:/SEEN-BY/PATH lines)."""
    def __init__(self, *, to_address='1:1/1', from_address='2:2/2',
                is_crash=False, is_hold=False, area=None):
        self.area = area
        self.from_name = 'Tester'
        self.to_name = 'Recipient'
        self.subject = 'Test'
        self.body = 'Hello'
        self.tear_line = None
        self.origin_line = None
        self.kludges = None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = None
        self.reply_id = None
        self.to_address = to_address
        self.from_address = from_address
        self.is_crash = is_crash
        self.is_hold = is_hold


class _FakeArea:
    tag = 'TEST.AREA'


class PacketPasswordTests(unittest.TestCase):
    def _header(self, **kwargs):
        from anetbbs.echomail.binkp import _build_ftn_packet, FTN_PKT_HEADER_SIZE
        pkt = _build_ftn_packet([], '1:1/1', '2:2/2', **kwargs)
        return pkt[:FTN_PKT_HEADER_SIZE]

    def test_password_written_nul_padded_at_correct_offset(self):
        header = self._header(packet_password='SECRET')
        self.assertEqual(header[26:34], b'SECRET\x00\x00')

    def test_password_longer_than_8_bytes_is_truncated_not_rejected(self):
        header = self._header(packet_password='TOOLONGPASSWORD')
        self.assertEqual(header[26:34], b'TOOLONGP')

    def test_blank_password_is_all_nuls_same_as_before_this_feature(self):
        from anetbbs.echomail.binkp import _build_ftn_packet, FTN_PKT_HEADER_SIZE
        pkt = _build_ftn_packet([], '1:1/1', '2:2/2')  # no packet_password kwarg at all
        header = pkt[:FTN_PKT_HEADER_SIZE]
        self.assertEqual(header[26:34], b'\x00' * 8)

    def test_header_size_and_capword_byteswap_unaffected(self):
        """Guard against the new fields disturbing anything else in this
        58-byte header -- the exact regression class this file's whole
        BinkP saga kept re-triggering."""
        from anetbbs.echomail.binkp import FTN_PKT_HEADER_SIZE
        header = self._header(packet_password='X',
                              default_crash=True, default_hold=True,
                              default_direct=True)
        self.assertEqual(len(header), 58)
        self.assertEqual(FTN_PKT_HEADER_SIZE, 58)
        cap_validate_be = struct.unpack('>H', header[40:42])[0]
        cap_word_le = struct.unpack('<H', header[44:46])[0]
        self.assertEqual(cap_validate_be, cap_word_le)
        self.assertEqual(cap_word_le, 0x0001)


class FlavorDefaultsTests(unittest.TestCase):
    ATTR_CRASH = 0x0002
    ATTR_HOLD = 0x0200

    def _parse_first(self, msg, **kwargs):
        from anetbbs.echomail.binkp import _build_ftn_packet, _parse_ftn_packet
        pkt = _build_ftn_packet([msg], '1:1/1', '2:2/2', **kwargs)
        parsed = _parse_ftn_packet(pkt)
        self.assertEqual(len(parsed), 1)
        return parsed[0]

    def test_network_default_crash_sets_attribute_bit_on_plain_netmail(self):
        msg = _FakeMsg()  # no explicit is_crash/is_hold
        parsed = self._parse_first(msg, default_crash=True)
        self.assertTrue(parsed['attribute'] & self.ATTR_CRASH)

    def test_network_default_hold_sets_attribute_bit_on_plain_netmail(self):
        msg = _FakeMsg()
        parsed = self._parse_first(msg, default_hold=True)
        self.assertTrue(parsed['attribute'] & self.ATTR_HOLD)

    def test_network_default_direct_also_sets_crash_bit(self):
        """Direct has no FTS-0001 bit of its own -- FTN convention treats
        it as the same immediate-delivery intent as Crash (see the
        model's own comment in models.py)."""
        msg = _FakeMsg()
        parsed = self._parse_first(msg, default_direct=True)
        self.assertTrue(parsed['attribute'] & self.ATTR_CRASH)

    def test_no_defaults_and_no_per_message_flags_leaves_bits_clear(self):
        msg = _FakeMsg()
        parsed = self._parse_first(msg)
        self.assertFalse(parsed['attribute'] & self.ATTR_CRASH)
        self.assertFalse(parsed['attribute'] & self.ATTR_HOLD)

    def test_per_message_flag_still_works_without_any_network_default(self):
        """Regression guard: the pre-existing per-message is_crash/is_hold
        path (already live before this feature) must keep working
        untouched when no network-level default is set at all."""
        msg = _FakeMsg(is_crash=True)
        parsed = self._parse_first(msg)
        self.assertTrue(parsed['attribute'] & self.ATTR_CRASH)

    def test_flavor_defaults_do_not_leak_onto_echomail_messages(self):
        """Crash/Hold/Direct are netmail delivery-flavor concepts -- FTN
        convention has no per-message meaning for them on echomail area
        posts. An echomail message (.area set) must not pick up the
        network's netmail flavor defaults even if all three are on."""
        msg = _FakeMsg(area=_FakeArea())
        parsed = self._parse_first(msg, default_crash=True, default_hold=True,
                                   default_direct=True)
        self.assertFalse(parsed['attribute'] & self.ATTR_CRASH,
                         'echomail must not inherit netmail Crash/Direct default')
        self.assertFalse(parsed['attribute'] & self.ATTR_HOLD,
                         'echomail must not inherit netmail Hold default')


if __name__ == '__main__':
    unittest.main()
