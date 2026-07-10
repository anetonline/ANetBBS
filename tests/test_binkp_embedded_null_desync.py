"""Regression test for a packet-record parser desync in
anetbbs/echomail/binkp.py:_parse_ftn_packet().

FTS-0001 message records have no explicit length field -- the message
text is simply read up to the next 0x00 byte. That's fine for ordinary
prose, but a real inbound packet (a Mystic hub's AreaFix %RESCAN reply
batch) contained an "[ANSI]" post whose raw ANSI-art body had an
embedded 0x00 byte partway through. The old code treated that embedded
null as the end of the message, truncating its body and leaving the
read cursor pointed at the middle of what should still have been that
message's body text -- not a real message boundary. Everything parsed
after that point came out as garbage: fabricated "messages" with
From/To/Subject fields built from fragments of the real body text,
confirmed against a live database dump showing raw \\r bytes embedded
in From/Subject columns and English-looking sentence fragments split
across fields that should never contain them.

This test hand-builds a two-message FTS-0001 packet where the first
message's body has an embedded 0x00, and asserts both messages parse
cleanly: the first message's full body (including the content after
the embedded null) is recovered, and the second message's header
fields are intact rather than garbled.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest


def _build_message_record(orig_addr, dest_addr, to_name, from_name, subject,
                           body_bytes):
    """Hand-build one FTS-0001 packed message record (not via
    _build_ftn_packet, which never embeds raw nulls in a body -- we
    need to construct the exact malformed-body scenario by hand)."""
    from anetbbs.echomail.binkp import MSG_TYPE_2

    on, oo = (int(x) for x in orig_addr.split(':')[1].split('/'))
    dn, dd = (int(x) for x in dest_addr.split(':')[1].split('/'))

    routing = struct.pack('<HHHHHH', oo, dd, on, dn, 0, 0)
    date_b = b'10 Jul 26  10:27:27\x00'
    to_b = to_name.encode('latin-1') + b'\x00'
    from_b = from_name.encode('latin-1') + b'\x00'
    subj_b = subject.encode('latin-1') + b'\x00'
    return MSG_TYPE_2 + routing + date_b + to_b + from_b + subj_b + \
        body_bytes + b'\x00'


def _build_packet(records):
    from anetbbs.echomail.binkp import FTN_PKT_HEADER_SIZE
    header = b'\x00' * FTN_PKT_HEADER_SIZE
    return header + b''.join(records) + b'\x00\x00'


class BinkpEmbeddedNullDesyncTests(unittest.TestCase):
    def test_embedded_null_in_body_does_not_truncate_or_desync(self):
        from anetbbs.echomail.binkp import _parse_ftn_packet

        # Body contains a raw 0x00 partway through (simulating embedded
        # binary/ANSI content), followed by more real content that must
        # not be lost.
        body_with_embedded_null = (
            b'AREA:TESTECHO\r\n\x1b[1;33mcolor stuff\x00more after the '
            b'embedded null that must survive\r\n--- tearline\r\n'
        )
        msg_a = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='Agent-037',
            subject='[ANSI] Test post with embedded null',
            body_bytes=body_with_embedded_null)

        msg_b = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='CleanSender',
            subject='A perfectly normal follow-up post',
            body_bytes=b'AREA:TESTECHO\r\nJust plain text.\r\n')

        packet = _build_packet([msg_a, msg_b])
        messages = _parse_ftn_packet(packet)

        self.assertEqual(len(messages), 2,
                         'both messages must parse -- the desync must not '
                         'swallow or fabricate a message')

        first, second = messages

        self.assertEqual(first['from_name'], 'Agent-037')
        self.assertEqual(first['subject'],
                         '[ANSI] Test post with embedded null')
        self.assertIn('more after the embedded null that must survive',
                      first['body'])

        self.assertEqual(second['from_name'], 'CleanSender')
        self.assertEqual(second['to_name'], 'All')
        self.assertEqual(second['subject'], 'A perfectly normal follow-up post')
        self.assertIn('Just plain text.', second['body'])
        # The specific corruption signature seen in production: raw \r
        # bytes leaking into header fields that must never contain them.
        self.assertNotIn('\r', second['from_name'])
        self.assertNotIn('\r', second['subject'])

    def test_embedded_null_immediately_followed_by_packet_terminator(self):
        """An embedded null right before the packet's own 0x00 0x00
        end-of-data marker must not be mistaken for that marker."""
        from anetbbs.echomail.binkp import _parse_ftn_packet

        body = b'text before\x00text after the embedded null\r\n'
        msg = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='Solo',
            subject='Only message', body_bytes=body)
        packet = _build_packet([msg])
        messages = _parse_ftn_packet(packet)

        self.assertEqual(len(messages), 1)
        self.assertIn('text after the embedded null', messages[0]['body'])

    def test_deep_chain_with_valid_date_field_is_still_rejected(self):
        """A second production incident (also captured via
        BINKP_DEBUG_DUMP_DIR, after the marker+date fix above shipped
        as v1.0b2.73) showed the marker+date check alone still isn't
        enough: a real article's body happened to contain a byte
        sequence that was not just MSG_TYPE_2 by coincidence, but a
        FULLY well-formed date+time+null field too (almost certainly
        quoted/reposted old FidoNet message content within the
        article's own prose, not random noise). That candidate's own
        immediate to/from/subject fields were clean (no control
        characters) -- a single-level check would have accepted it --
        but scanning past it landed on a *second* coincidental
        marker+date match one level deeper whose fields did contain a
        raw '\\r'. This test reproduces that exact two-level structure:
        a clean-looking marker+date match, immediately followed by a
        second marker+date match with a dirty field, all still nested
        inside what is really just one message's body -- and confirms
        the chain-validating fix correctly rejects both and finds the
        one genuine boundary further out."""
        from anetbbs.echomail.binkp import _parse_ftn_packet, MSG_TYPE_2

        def _fake_header(to_name, from_name, subject):
            routing = struct.pack('<HHHHHH', 500, 600, 500, 600, 0, 0)
            date_b = b'10 Jul 26  10:27:27\x00'
            to_b = to_name.encode('latin-1') + b'\x00'
            from_b = from_name.encode('latin-1') + b'\x00'
            subj_b = subject.encode('latin-1') + b'\x00'
            return MSG_TYPE_2 + routing + date_b + to_b + from_b + subj_b

        # Level 1: clean fields, would fool a single-level check.
        fake_level1 = _fake_header('Clean1', 'Clean2', 'CleanSubject')
        # Level 2: immediately follows level 1's "empty body" null --
        # its from_name contains the corruption signature seen live.
        fake_level2 = _fake_header('X', 'Dirty\rField', 'Y')

        msg_a_body = (
            b'Real article prose before the coincidence, quoting an '
            b'old FTN dump as an example: \x00'
            + fake_level1 + b'\x00'
            + fake_level2 + b'\x00'
            + b'more real article prose that must not be lost.\r\n'
        )
        msg_a = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='ArticleAuthor',
            subject='An article that happens to quote old FTN structure',
            body_bytes=msg_a_body)

        msg_b = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='NextSender',
            subject='The genuinely next message',
            body_bytes=b'AREA:TESTECHO\r\nThis is the real next message.\r\n')

        packet = _build_packet([msg_a, msg_b])
        messages = _parse_ftn_packet(packet)

        self.assertEqual(len(messages), 2,
                         'both fake nested candidates must be rejected, '
                         'landing on the one genuine boundary')

        first, second = messages
        self.assertEqual(first['from_name'], 'ArticleAuthor')
        self.assertIn('more real article prose that must not be lost',
                      first['body'])
        self.assertNotIn('\r', first['from_name'])

        self.assertEqual(second['from_name'], 'NextSender')
        self.assertEqual(second['subject'], 'The genuinely next message')
        self.assertIn('This is the real next message', second['body'])

    def test_coincidental_msg_type_marker_inside_body_is_not_a_false_boundary(self):
        """A first fix attempt only checked the 2 bytes after a
        candidate null for MSG_TYPE_2 (b'\\x02\\x00'). Confirmed live
        against a real inbound packet (captured via
        BINKP_DEBUG_DUMP_DIR): a message's real body happened to
        contain the exact byte sequence 0x00 0x02 0x00 as part of
        ordinary embedded content, with no relation to any real message
        boundary. The 2-byte-only check falsely accepted that as a new
        message starting there, desyncing everything after it -- same
        failure mode as the original bug, just a rarer trigger. The fix
        additionally requires a date-shaped string right where the
        packed message header's date field would be, which this
        coincidental byte pair does not provide (it's followed by
        arbitrary content, not "DD Mon YY")."""
        from anetbbs.echomail.binkp import _parse_ftn_packet, MSG_TYPE_2

        # Body contains an embedded null immediately followed by
        # MSG_TYPE_2's own bytes purely by coincidence (0x00 0x02 0x00),
        # then more real content, then the message's true terminator.
        body_with_coincidental_marker = (
            b'Real article text before the coincidence.\x00'
            + MSG_TYPE_2 +
            b'more real article text that must not be lost.\r\n'
        )
        msg_a = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='ArticleAuthor',
            subject='Men In Black: The Secret Terror Among Us',
            body_bytes=body_with_coincidental_marker)

        msg_b = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='NextSender',
            subject='The genuinely next message',
            body_bytes=b'AREA:TESTECHO\r\nThis is the real next message.\r\n')

        packet = _build_packet([msg_a, msg_b])
        messages = _parse_ftn_packet(packet)

        self.assertEqual(len(messages), 2,
                         'the coincidental marker must not fabricate a '
                         'third bogus message or swallow the real second one')

        first, second = messages
        self.assertEqual(first['from_name'], 'ArticleAuthor')
        self.assertEqual(first['subject'],
                         'Men In Black: The Secret Terror Among Us')
        self.assertIn('more real article text that must not be lost',
                      first['body'])

        self.assertEqual(second['from_name'], 'NextSender')
        self.assertEqual(second['subject'], 'The genuinely next message')
        self.assertIn('This is the real next message', second['body'])

    def test_rejected_candidate_routing_header_not_mistaken_for_packet_end(self):
        """Once a candidate boundary is correctly rejected by the chain
        check, its own raw bytes get treated as unparsed body content
        for the rest of the scan. A routing header's attr and cost
        fields are each 2 bytes and overwhelmingly zero in real traffic
        (confirmed against production: attr=0 on the large majority of
        real messages, cost effectively always 0) -- two adjacent
        zero fields produce a 0x00 0x00 pair that used to be trusted
        outright as "the packet's own end-of-data marker," terminating
        the parse early and silently dropping every message after it,
        even though the real terminator is nowhere nearby. The fix
        requires that marker to actually be at the tail of the buffer,
        since that's the only place FTS-0001 ever puts it."""
        from anetbbs.echomail.binkp import _parse_ftn_packet, MSG_TYPE_2

        def _fake_header_with_zero_attr_cost(to_name, from_name, subject):
            # attr=0, cost=0 -- the two trailing zero fields that
            # produce the coincidental 0x00 0x00 once this candidate
            # (correctly) fails the chain check and its bytes are
            # never treated as a real message.
            routing = struct.pack('<HHHHHH', 500, 600, 500, 600, 0, 0)
            date_b = b'10 Jul 26  10:27:27\x00'
            to_b = to_name.encode('latin-1') + b'\x00'
            from_b = from_name.encode('latin-1') + b'\x00'
            subj_b = subject.encode('latin-1') + b'\x00'
            return MSG_TYPE_2 + routing + date_b + to_b + from_b + subj_b

        # Dirty field forces the chain check to reject this candidate --
        # exactly what should then expose its attr=0/cost=0 tail bytes
        # as ordinary (still-unconsumed) body content.
        fake = _fake_header_with_zero_attr_cost('X', 'Dirty\rField', 'Y')

        msg_a_body = (
            b'Real prose before the coincidence: \x00' + fake +
            b'more real prose after the rejected candidate, '
            b'which must not be silently dropped.\r\n'
        )
        msg_a = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='ArticleAuthor',
            subject='Article with a rejected nested candidate',
            body_bytes=msg_a_body)
        msg_b = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='NextSender',
            subject='The genuinely next message',
            body_bytes=b'AREA:TESTECHO\r\nThis is the real next message.\r\n')

        packet = _build_packet([msg_a, msg_b])
        messages = _parse_ftn_packet(packet)

        self.assertEqual(len(messages), 2,
                         'the rejected candidate\'s own zero-valued attr/cost '
                         'fields must not be mistaken for the packet terminator, '
                         'silently dropping the real second message')
        self.assertIn('which must not be silently dropped', messages[0]['body'])
        self.assertEqual(messages[1]['from_name'], 'NextSender')

    def test_no_embedded_null_still_parses_normally(self):
        """Baseline: ordinary messages with no embedded nulls are
        unaffected by the forward-scanning change."""
        from anetbbs.echomail.binkp import _parse_ftn_packet

        msg = _build_message_record(
            '1:1/1', '2:2/2', to_name='All', from_name='Normal',
            subject='Ordinary subject',
            body_bytes=b'AREA:TESTECHO\r\nNothing unusual here.\r\n')
        packet = _build_packet([msg])
        messages = _parse_ftn_packet(packet)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['from_name'], 'Normal')
        self.assertEqual(messages[0]['area_tag'], 'TESTECHO')
        self.assertIn('Nothing unusual here.', messages[0]['body'])


if __name__ == '__main__':
    unittest.main()
