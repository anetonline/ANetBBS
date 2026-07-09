"""Unit tests for anetbbs/msp/systat.py:parse_systat_response(), added
for the terminal MSP/PM recipient picker (anetbbs/features/bbs_ui.py).

Unlike the rest of the terminal sysop-tools pass, this one pure function
IS practically unit-testable without a session mock or a Flask app --
no DB, no I/O, just text in and a list of dicts out. Covers our own
_build_response() output shape (the format we control) plus a few
malformed/edge-case replies the "probe succeeded but fall back to
manual entry" path needs to handle gracefully.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.msp.systat import parse_systat_response  # noqa: E402


class SystatParserTests(unittest.TestCase):
    def test_our_own_two_user_reply(self):
        text = (
            "ANetBBS bbs.example.com - Example BBS\r\n"
            "\r\n"
            f"{'Node':>4}  {'User':<22} {'Action':<24} {'Idle':>5}\r\n"
            f"{'-'*4:>4}  {'-'*22:<22} {'-'*24:<24} {'-'*5:>5}\r\n"
            f"{1:>4}  {'alice':<22} {'Reading mail':<24} {'0:01':>5}\r\n"
            f"{2:>4}  {'bob':<22} {'Playing LORD':<24} {'0:08':>5}\r\n"
            "\r\n"
            "Total active: 2\r\n"
        )
        rows = parse_systat_response(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {'node': '1', 'user': 'alice',
                                    'action': 'Reading mail', 'idle': '0:01'})
        self.assertEqual(rows[1]['user'], 'bob')
        self.assertEqual(rows[1]['action'], 'Playing LORD')

    def test_no_users_active(self):
        text = "ANetBBS bbs.example.com - Example BBS\r\n\r\nNo users currently active.\r\n"
        self.assertEqual(parse_systat_response(text), [])

    def test_empty_string(self):
        self.assertEqual(parse_systat_response(''), [])

    def test_garbage_reply_with_no_header(self):
        self.assertEqual(parse_systat_response('not a systat reply at all\r\n'), [])

    def test_web_synthetic_node_ids(self):
        # _build_response() gives web-only sessions 'w1', 'w2', ... ids.
        text = (
            "ANetBBS bbs.example.com - Example BBS\r\n\r\n"
            f"{'Node':>4}  {'User':<22} {'Action':<24} {'Idle':>5}\r\n"
            f"{'-'*4:>4}  {'-'*22:<22} {'-'*24:<24} {'-'*5:>5}\r\n"
            f"{'w1':>4}  {'carol':<22} {'Boards':<24} {'2:30':>5}\r\n"
            "\r\nTotal active: 1\r\n"
        )
        rows = parse_systat_response(text)
        self.assertEqual(rows, [{'node': 'w1', 'user': 'carol',
                                  'action': 'Boards', 'idle': '2:30'}])

    def test_narrower_columns_than_our_own_format_still_parse(self):
        # A peer with different column widths -- still 2+ space runs.
        text = (
            "Synchronet peer.example.com - Peer BBS\r\n\r\n"
            "Node  User          Action        Idle\r\n"
            "----  ------------  ------------  ----\r\n"
            "1     dave          Chatting      0:00\r\n"
        )
        rows = parse_systat_response(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user'], 'dave')

    def test_row_missing_action_and_idle_still_parses_node_and_user(self):
        text = (
            "X - Y\r\n\r\nNode  User\r\n----  ----\r\n1  eve\r\n"
        )
        rows = parse_systat_response(text)
        self.assertEqual(rows, [{'node': '1', 'user': 'eve', 'action': '', 'idle': ''}])


if __name__ == '__main__':
    unittest.main()
