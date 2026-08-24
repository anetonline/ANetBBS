"""Regression tests for the WaZOO FREQ (FTS-0006) pure parsing/building
functions in anetbbs/echomail/freq.py -- filename convention, .REQ
line grammar, and content round-trip. Verified against the real,
published FTSC document (ftsc.org/docs/fts-0006.002, "WaZOO File
Requests" section) rather than guessed from binkp's own casual
"M_GET = request specific file" description, which is actually a
completely different (resume-only) mechanism -- see freq.py's module
docstring for the full story.

DB-touching behavior (process_inbound_req matching against FileArea/
FileUpload) is covered separately in test_freq_inbound_matching.py,
which needs a real app/DB fixture.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.echomail.freq import (
    req_filename_for_address, is_req_filename, parse_req_lines,
    build_req_content,
)


class ReqFilenameTests(unittest.TestCase):
    def test_matches_the_real_fts0006_worked_example(self):
        # FTS-0006's own example: "requesting from 12/2... file name
        # would be 000C0002.REQ" (12 decimal = 0xC, 2 decimal = 0x2).
        self.assertEqual(req_filename_for_address('1:12/2').lower(),
                         '000c0002.req')

    def test_pads_to_four_hex_digits_each(self):
        self.assertEqual(req_filename_for_address('1:1/1').lower(),
                         '00010001.req')

    def test_ignores_zone_and_point(self):
        # Only net/node feed the filename per spec -- zone and point
        # aren't part of this convention at all.
        self.assertEqual(req_filename_for_address('21:1/1.5').lower(),
                         req_filename_for_address('1:1/1').lower())

    def test_unparseable_address_returns_none(self):
        self.assertIsNone(req_filename_for_address('not an address'))
        self.assertIsNone(req_filename_for_address(''))
        self.assertIsNone(req_filename_for_address(None))


class IsReqFilenameTests(unittest.TestCase):
    def test_recognizes_real_examples(self):
        self.assertTrue(is_req_filename('000C0002.REQ'))
        self.assertTrue(is_req_filename('000c0002.req'))
        self.assertTrue(is_req_filename('DEADBEEF.Req'))

    def test_rejects_non_req_files(self):
        self.assertFalse(is_req_filename('12345678.pkt'))
        self.assertFalse(is_req_filename('000C0002.REQQ'))
        self.assertFalse(is_req_filename('C0002.REQ'))       # too short
        self.assertFalse(is_req_filename('000C00021.REQ'))   # too long
        self.assertFalse(is_req_filename('ZZZZZZZZ.REQ'))    # not hex
        self.assertFalse(is_req_filename(''))
        self.assertFalse(is_req_filename(None))


class ParseReqLinesTests(unittest.TestCase):
    def test_plain_filename_line(self):
        lines = parse_req_lines(b'this.arc\r\n')
        self.assertEqual(lines, [{'filename': 'this.arc', 'password': None,
                                  'sign': None, 'time': None}])

    def test_the_real_fts0006_worked_example_with_password(self):
        # "If the sysop of 12/2 requires a password of THAT to get the
        # file THIS.ARC" -- straight from the spec's own example.
        content = b'this.arc !that\r\nnodelist.*\r\n'
        lines = parse_req_lines(content)
        self.assertEqual(lines[0]['filename'], 'this.arc')
        self.assertEqual(lines[0]['password'], 'that')
        self.assertEqual(lines[1]['filename'], 'nodelist.*')
        self.assertIsNone(lines[1]['password'])

    def test_update_qualifier_with_sign_and_time(self):
        lines = parse_req_lines(b'NEWOPUS.* +599634000\r\n')
        self.assertEqual(lines[0]['filename'], 'NEWOPUS.*')
        self.assertEqual(lines[0]['sign'], '+')
        self.assertEqual(lines[0]['time'], 599634000)

    def test_password_and_update_qualifier_together(self):
        lines = parse_req_lines(b'this.arc !that -12345\r\n')
        self.assertEqual(lines[0]['password'], 'that')
        self.assertEqual(lines[0]['sign'], '-')
        self.assertEqual(lines[0]['time'], 12345)

    def test_blank_lines_and_malformed_lines_are_skipped_not_fatal(self):
        content = b'good.txt\r\n\r\n   \r\nalso-good.zip\r\n'
        lines = parse_req_lines(content)
        self.assertEqual([l['filename'] for l in lines],
                         ['good.txt', 'also-good.zip'])

    def test_empty_content_returns_empty_list(self):
        self.assertEqual(parse_req_lines(b''), [])
        self.assertEqual(parse_req_lines(b'\r\n\r\n'), [])


class BuildReqContentTests(unittest.TestCase):
    def test_plain_filenames_round_trip_through_parse(self):
        content = build_req_content(['this.arc', 'nodelist.*'])
        lines = parse_req_lines(content)
        self.assertEqual([l['filename'] for l in lines], ['this.arc', 'nodelist.*'])
        self.assertTrue(all(l['password'] is None for l in lines))

    def test_password_tuples_round_trip_through_parse(self):
        content = build_req_content([('secret.zip', 'hunter2')])
        lines = parse_req_lines(content)
        self.assertEqual(lines[0]['filename'], 'secret.zip')
        self.assertEqual(lines[0]['password'], 'hunter2')

    def test_mixed_plain_and_password_items(self):
        content = build_req_content(['open.txt', ('gated.zip', 'pw')])
        lines = parse_req_lines(content)
        self.assertEqual(len(lines), 2)
        self.assertIsNone(lines[0]['password'])
        self.assertEqual(lines[1]['password'], 'pw')


if __name__ == '__main__':
    unittest.main()
