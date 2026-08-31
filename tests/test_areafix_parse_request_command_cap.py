"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): areafix.py's parse_request() (shared by
both areafix.py and filefix.py's hub-side processing) had no cap on
how many commands a single netmail body could produce. Both bots do a
real DB round-trip per parsed command (subscribe/unsubscribe lookups),
and any BinkP peer with a valid session can send netmail addressed to
the areafix/filefix robot -- a body crafted with thousands of +/-TAG
lines would make a single inbound netmail do thousands of DB queries
in one processing pass.

Fixed with _MAX_PARSED_COMMANDS (500), far more than any real
AreaFix/FileFix session would ever need. Pure-function test, no Flask
app context needed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.echomail.areafix import parse_request, _MAX_PARSED_COMMANDS


class ParseRequestCommandCapTests(unittest.TestCase):
    def test_normal_request_is_unaffected(self):
        body = '+AF.ONE\n-AF.TWO\n%LIST\n%RESCAN AF.THREE\n'
        cmds = parse_request(body)
        self.assertEqual(len(cmds), 4)

    def test_command_count_is_capped(self):
        body = '\n'.join(f'+AF.TAG{i}' for i in range(_MAX_PARSED_COMMANDS + 200))
        cmds = parse_request(body)
        self.assertEqual(
            len(cmds), _MAX_PARSED_COMMANDS,
            f'a crafted body with far more than {_MAX_PARSED_COMMANDS} '
            'command-shaped lines must be truncated at the cap, not '
            'produce one DB-hitting command per line unbounded')

    def test_cap_counts_only_matched_commands_not_all_lines(self):
        """Interleaved junk (non-command) lines are cheap (a regex
        miss, no DB work) and must not count against the cap -- only
        actually-matched commands do."""
        lines = []
        for i in range(_MAX_PARSED_COMMANDS + 50):
            lines.append('this is not a command line')
            lines.append(f'+AF.TAG{i}')
        cmds = parse_request('\n'.join(lines))
        self.assertEqual(len(cmds), _MAX_PARSED_COMMANDS)

    def test_exactly_at_the_cap_is_not_truncated(self):
        body = '\n'.join(f'+AF.TAG{i}' for i in range(_MAX_PARSED_COMMANDS))
        cmds = parse_request(body)
        self.assertEqual(len(cmds), _MAX_PARSED_COMMANDS)


if __name__ == '__main__':
    unittest.main()
