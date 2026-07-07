"""Regression test for a live-caught, real message-loss bug in the QWK
hub's outbound packet writer.

`_build_messages_dat()` (anetbbs/echomail/qwk_hub_ftp.py) wrote every
field from byte 113 onward at the wrong offset compared to what
`_parse_messages_dat()` (anetbbs/echomail/qwk.py) expects on the
reading side -- including using binary encoding for `num_chunks` where
the real QWK format requires 6-char ASCII text. The conference number
in particular got written 3 bytes off from where the reader looks for
it, so every message's conf_num came back as garbage on read, which
never matched a real conference in CONTROL.DAT -- silently dropped
inside the parser itself (never even reaching the caller's "received"
list), before any error or drop-count ever got logged.

Net effect, live-caught testing the real ANotherNetwork QWK flow
end-to-end between bbs.a-net.fyi and a Pi peer install: a QWK poll
would report clean "success" with 0 messages received, no error at
all, no matter how many real messages existed in the subscribed areas
on the hub -- every single message, always, silently vanished on the
wire.
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeArea:
    def __init__(self, name):
        self.name = name


class FakeMsg:
    def __init__(self, from_name, to_name, subject, body, created_at=None):
        self.from_name = from_name
        self.to_name = to_name
        self.subject = subject
        self.body = body
        self.created_at = created_at or datetime(2026, 7, 7, 12, 34)


class QwkPacketRoundtripTests(unittest.TestCase):
    def test_message_survives_hub_write_then_client_parse(self):
        from anetbbs.echomail.qwk_hub_ftp import (
            _build_control_dat, _build_messages_dat)
        from anetbbs.echomail.qwk import _parse_control_dat, _parse_messages_dat

        conferences = {1: FakeArea('ANN.TECH'), 2: FakeArea('ANN.SYSOP.HELP')}
        messages_by_conf = {
            1: [FakeMsg('Tech News Bot', 'All', 'Some tech headline',
                        'A test message body.\nSecond line.')],
            2: [FakeMsg('StingRay', 'All', 'help wanted', 'Body two.')],
        }

        control = _build_control_dat('ANET', conferences)
        messages_dat = _build_messages_dat(messages_by_conf, 'PI3ANET')

        info = _parse_control_dat(control)
        parsed = _parse_messages_dat(messages_dat, info['conferences'])

        self.assertEqual(len(parsed), 2,
                         'messages were silently dropped during the '
                         'hub-write -> client-parse round trip')

        by_subject = {m['subject']: m for m in parsed}
        self.assertIn('Some tech headline', by_subject)
        self.assertIn('help wanted', by_subject)

        msg1 = by_subject['Some tech headline']
        self.assertEqual(msg1['from_name'], 'Tech News Bot')
        self.assertEqual(msg1['to_name'], 'All')
        self.assertEqual(msg1['conf_num'], 1)
        self.assertIn('A test message body.', msg1['body'])

        msg2 = by_subject['help wanted']
        self.assertEqual(msg2['from_name'], 'StingRay')
        self.assertEqual(msg2['conf_num'], 2)

    def test_conference_number_is_read_from_the_correct_byte_offset(self):
        """Directly pins the conf_num field to bytes 123-124, matching the
        reader -- the exact offset the old writer got wrong."""
        import struct
        from anetbbs.echomail.qwk_hub_ftp import _build_messages_dat

        messages_by_conf = {5: [FakeMsg('A', 'B', 'C', 'D')]}
        data = _build_messages_dat(messages_by_conf, 'ANET')

        # First 128 bytes = welcome block; message header starts right after.
        header = data[128:256]
        conf_num = struct.unpack('<H', header[123:125])[0]
        self.assertEqual(conf_num, 5)

    def test_num_chunks_is_ascii_text_not_binary(self):
        """The real QWK spec requires num_chunks as 6-char ASCII text at
        bytes 116-121 -- pins this explicitly since it's easy to
        accidentally regress back to binary encoding."""
        from anetbbs.echomail.qwk_hub_ftp import _build_messages_dat

        messages_by_conf = {1: [FakeMsg('A', 'B', 'C', 'short body')]}
        data = _build_messages_dat(messages_by_conf, 'ANET')
        header = data[128:256]

        num_chunks_field = header[116:122]
        # Must decode cleanly as ASCII digits (whitespace-padded), NOT
        # contain arbitrary binary bytes.
        text = num_chunks_field.decode('ascii').strip()
        self.assertTrue(text.isdigit(), f'num_chunks field is not ASCII text: {num_chunks_field!r}')
        self.assertEqual(int(text), 2)  # 1 header block + 1 short body block


if __name__ == '__main__':
    unittest.main()
