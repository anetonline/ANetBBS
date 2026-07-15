"""Regression tests locking anetbbs/echomail/qwk.py's MESSAGES.DAT
reader (_parse_messages_dat) against the real QWK format spec, fetched
and verified directly (wmcbrine.com/mmail/specs/qwklay.html, "QWK Mail
Packet File Layout") rather than inferred or trusted from memory of a
prior session's work on the OTHER three hand-rolled QWK writers in
this codebase (see test_qwk_wire_format_compliance.py, which covers
qwk_hub_ftp.py/qwk_hub.py/qwk_user.py but not this file).

Real spec table (1-indexed, exactly as published):

    Offset   Length  Field
    1        1       Message status flag
    2-8      7       Message number (ASCII)
    9-16     8       Date (mm-dd-yy, ASCII)
    17-21    5       Time (24-hour hh:mm, ASCII)
    22-46    25      To (uppercase, left justified)
    47-71    25      From (uppercase, left justified)
    72-96    25      Subject (mixed case)
    97-108   12      Password (space filled)
    109-116  8       Reference message number (ASCII)
    117-122  6       Number of 128-byte blocks (ASCII)
    123      1       Active flag (225=active, 226=kill)
    124-125  2       Conference number (unsigned word, little-endian)
    126-127  2       Logical message number in packet (unsigned word)
    128      1       '*' = network tagline, else ' '

Converted to 0-indexed Python slices and independently constructed
here (NOT reusing _parse_messages_dat's own offset constants), this
test builds a synthetic header byte-for-byte from the spec table and
confirms the parser extracts every field from the position the real
spec defines -- the strongest form of alignment check, since a copy-
paste offset bug in the parser can't accidentally match a test that
copied the same wrong offset.

Confirmed correct: every field _parse_messages_dat reads matches the
spec exactly. The one spec field never read at all -- offset 126-127,
"logical message number in packet" -- is consistent with this
codebase's already-documented, deliberate choice to rely on FTN
@MSGID/@REPLY kludges for reply-threading instead of QWK's native
mechanism (not a new bug; not fixed here, per that existing decision).
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _build_spec_header(status=b' ', msg_num='0000001', date='07-15-26',
                       time='12:00', to='ALL', frm='SYSOP', subject='Test',
                       password=b'', ref_num='0', num_blocks=1,
                       active_flag=0xE1, conf_num=5, logical_num=1,
                       tagline=b' '):
    """Build a 128-byte MESSAGES.DAT header record purely from the
    spec's own offset table -- independent of qwk.py's implementation."""
    h = bytearray(b' ' * 128)
    h[0:1] = status                                          # 1..1
    h[1:8] = msg_num.encode('ascii').ljust(7)[:7]            # 2..8
    h[8:16] = date.encode('ascii').ljust(8)[:8]               # 9..16
    h[16:21] = time.encode('ascii').ljust(5)[:5]              # 17..21
    h[21:46] = to.encode('ascii').ljust(25)[:25]              # 22..46
    h[46:71] = frm.encode('ascii').ljust(25)[:25]             # 47..71
    h[71:96] = subject.encode('ascii').ljust(25)[:25]         # 72..96
    h[96:108] = (password or b'').ljust(12)[:12]               # 97..108
    h[108:116] = ref_num.encode('ascii').rjust(8)[:8]          # 109..116
    h[116:122] = str(num_blocks).encode('ascii').rjust(6)[:6]  # 117..122
    h[122] = active_flag                                       # 123
    struct.pack_into('<H', h, 123, conf_num)                   # 124..125
    struct.pack_into('<H', h, 125, logical_num)                # 126..127
    h[127:128] = tagline                                       # 128
    return bytes(h)


class QwkMessagesDatSpecAlignmentTests(unittest.TestCase):
    def _parse_one(self, header, body_text='Hello from the spec test.',
                   conferences=None):
        from anetbbs.echomail.qwk import _parse_messages_dat, QWK_HEADER_SIZE

        body = body_text.encode('latin-1')
        remainder = len(body) % QWK_HEADER_SIZE
        if remainder:
            body += b' ' * (QWK_HEADER_SIZE - remainder)
        num_blocks = 1 + len(body) // QWK_HEADER_SIZE
        # Rewrite the num_blocks field to match the actual body we're
        # attaching (the header passed in may have used a placeholder).
        header = bytearray(header)
        header[116:122] = str(num_blocks).encode('ascii').rjust(6)[:6]
        # Whatever conf_num the caller baked into the header (bytes
        # 124-125), make sure it's in the known-conferences dict --
        # _parse_messages_dat legitimately drops messages from
        # conferences CONTROL.DAT never advertised, which is a real
        # feature (guards against misaligned reads), not something
        # these offset-alignment tests are checking.
        conf_num = struct.unpack('<H', header[123:125])[0]
        header = bytes(header)

        welcome = b' ' * QWK_HEADER_SIZE
        data = welcome + header + body
        if conferences is None:
            conferences = {conf_num: 'Test Conference'} if conf_num else {}
        return _parse_messages_dat(data, conferences=conferences)

    def test_to_name_read_from_spec_offset_22_46(self):
        header = _build_spec_header(to='ALICE')
        msgs = self._parse_one(header)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['to_name'], 'ALICE')

    def test_from_name_read_from_spec_offset_47_71(self):
        header = _build_spec_header(frm='BOBTHESYSOP')
        msgs = self._parse_one(header)
        self.assertEqual(msgs[0]['from_name'], 'BOBTHESYSOP')

    def test_subject_read_from_spec_offset_72_96(self):
        header = _build_spec_header(subject='Re: hello world')
        msgs = self._parse_one(header)
        self.assertEqual(msgs[0]['subject'], 'Re: hello world')

    def test_conference_number_read_from_spec_offset_124_125_little_endian(self):
        header = _build_spec_header(conf_num=42)
        msgs = self._parse_one(header)
        self.assertEqual(msgs[0]['conf_num'], 42)

    def test_active_flag_226_kill_causes_message_to_be_skipped(self):
        """Spec: 225=active, 226=kill -- 0xE1=225, 0xE2=226."""
        self.assertEqual(0xE1, 225)
        self.assertEqual(0xE2, 226)
        header = _build_spec_header(active_flag=0xE2)
        msgs = self._parse_one(header)
        self.assertEqual(msgs, [], 'a killed (226) message must not be imported')

    def test_active_flag_225_active_message_is_kept(self):
        header = _build_spec_header(active_flag=0xE1)
        msgs = self._parse_one(header)
        self.assertEqual(len(msgs), 1)

    def test_private_status_flag_marks_message_private(self):
        """Spec status flags: '*' = private/unread, '+' = private/read."""
        header = _build_spec_header(status=b'*')
        msgs = self._parse_one(header)
        self.assertTrue(msgs[0]['is_private'])

    def test_public_status_flag_marks_message_not_private(self):
        header = _build_spec_header(status=b' ')
        msgs = self._parse_one(header)
        self.assertFalse(msgs[0]['is_private'])

    def test_message_number_read_from_spec_offset_2_8(self):
        header = _build_spec_header(msg_num='0001234')
        msgs = self._parse_one(header)
        self.assertEqual(msgs[0]['msg_num'], '0001234')

    def test_reference_number_read_from_spec_offset_109_116(self):
        header = _build_spec_header(ref_num='99')
        msgs = self._parse_one(header)
        self.assertEqual(msgs[0]['ref_num'], 99)


if __name__ == '__main__':
    unittest.main()
