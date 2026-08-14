"""Regression tests for MRCProtocol.parse_packet()'s inbound
sanitization -- real gap found in a security/performance audit:
parse_packet() used to return every field completely raw, with
sanitize_field() only ever applied on the OUTBOUND/create_packet()
side. Safety rested entirely on downstream consumers (mrc_chat.py's
_pipe_to_ansi/_strip_pipe) remembering to re-sanitize before display --
a defense-in-depth gap, since a future/additional consumer that forgot
would reopen the exact cross-network ANSI/control-byte-injection class
of bug already fixed elsewhere this audit. Now stripped at the parse
boundary itself.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.mrc_protocol import MRCProtocol


class ParsePacketSanitizationTests(unittest.TestCase):
    def test_ansi_csi_sequence_in_message_is_stripped(self):
        packet = 'alice~bbs1~lobby~~~lobby~hello \x1b[2J\x1b[H world~'
        parsed = MRCProtocol.parse_packet(packet)
        self.assertNotIn('\x1b', parsed['message'])
        self.assertIn('hello', parsed['message'])
        self.assertIn('world', parsed['message'])

    def test_osc_sequence_is_stripped(self):
        packet = 'alice~bbs1~lobby~~~lobby~before\x1b]0;fake title\x07after~'
        parsed = MRCProtocol.parse_packet(packet)
        self.assertNotIn('\x1b', parsed['message'])
        self.assertNotIn('\x07', parsed['message'])
        self.assertIn('before', parsed['message'])
        self.assertIn('after', parsed['message'])

    def test_bare_or_malformed_escape_cannot_survive(self):
        packet = 'alice~bbs1~lobby~~~lobby~oops\x1bnotarealcsi~'
        parsed = MRCProtocol.parse_packet(packet)
        self.assertNotIn('\x1b', parsed['message'])

    def test_other_c0_control_bytes_are_stripped(self):
        packet = 'alice~bbs1~lobby~~~lobby~bell\x07backspace\x08end~'
        parsed = MRCProtocol.parse_packet(packet)
        self.assertNotIn('\x07', parsed['message'])
        self.assertNotIn('\x08', parsed['message'])

    def test_identity_fields_are_sanitized_too_not_just_message(self):
        """Not just the message body -- a malicious/buggy remote hub
        could put control bytes in ANY field, and mrc_chat.py displays
        from_user/from_room in join/part notices and /who listings."""
        packet = 'ali\x1b[2Jce~bbs1~lob\x07by~~~lobby~hi~'
        parsed = MRCProtocol.parse_packet(packet)
        self.assertNotIn('\x1b', parsed['from_user'])
        self.assertNotIn('\x07', parsed['from_room'])
        self.assertIn('alice', parsed['from_user'])
        self.assertIn('lobby', parsed['from_room'])

    def test_ordinary_plain_text_message_is_unaffected(self):
        packet = 'alice~bbs1~lobby~~~lobby~just a normal chat message~'
        parsed = MRCProtocol.parse_packet(packet)
        self.assertEqual(parsed['message'], 'just a normal chat message')

    def test_pipe_color_codes_survive_untouched(self):
        """|00-|15 pipe color codes are plain printable ASCII, not
        control bytes -- must not be affected by the new sanitization,
        since mrc_chat.py's own _pipe_to_ansi() still needs to see them
        to render colors."""
        packet = 'alice~bbs1~lobby~~~lobby~|12red |09blue~'
        parsed = MRCProtocol.parse_packet(packet)
        self.assertEqual(parsed['message'], '|12red |09blue')

    def test_empty_message_field_still_parses_to_empty_string(self):
        packet = 'alice~bbs1~lobby~~~lobby~~'
        parsed = MRCProtocol.parse_packet(packet)
        self.assertEqual(parsed['message'], '')


if __name__ == '__main__':
    unittest.main()
