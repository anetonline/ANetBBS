"""Regression test for a real bug found in a full echomail-subsystem
audit: nodelist.py's header parser (_HEADER_RE / parse_header) never
matched this SAME module's own generate_nodelist() output -- confirmed
directly against a real on-disk file
(data/files/annet_nodelist/NODELIST.196):

    ;A NODELIST.196 for ANotherNetwork -- July 15, 2026 -- Day 196 : 0000

The old anchored regex required a literal "nodelist" keyword right
after the first word and no extra clause before the date -- neither of
which this format has (it inserts "for ANotherNetwork --" before the
date, and has no "nodelist" keyword at all). Every nodelist this
software generates -- meaning every peer's own re-import of it -- fell
through to the fallback path silently, showing day_of_year=1 and
release_date=today() instead of the real values baked into the file.

Fixed by extracting day-of-year, CRC, and release date independently
via tolerant searches instead of one monolithic anchored pattern.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class NodelistHeaderParseTests(unittest.TestCase):
    def test_parses_this_softwares_own_generated_header_format(self):
        from anetbbs.echomail.nodelist import parse_header
        content = (
            ';A NODELIST.196 for ANotherNetwork -- July 15, 2026 -- Day 196 : 0000\n'
            ';\n'
            'Zone,1,ANotherNetwork,World,Sysop,000-000-0000,9600,\n'
        )
        result = parse_header(content)
        self.assertEqual(result['day_of_year'], 196)
        self.assertEqual(result['crc_checksum'], '0000')
        import datetime
        self.assertEqual(result['release_date'], datetime.date(2026, 7, 15))

    def test_matches_generate_nodelist_output_exactly(self):
        """Direct round-trip: whatever this module's own generator
        writes as its first line must be parseable by parse_header,
        not just a hand-constructed example string."""
        import inspect
        from anetbbs.echomail import nodelist as mod
        src = inspect.getsource(mod.generate_nodelist)
        self.assertIn('NODELIST.{day_of_year:03d} for', src,
                      "this test's expectations are tied to the generator's "
                      "actual header format -- update both together if it "
                      "changes")
        result = mod.parse_header(
            ';A NODELIST.045 for SomeNet -- March 3, 2027 -- Day 45 : 1234\n')
        self.assertEqual(result['day_of_year'], 45)
        self.assertEqual(result['crc_checksum'], '1234')

    def test_classic_fts5000_style_header_with_weekday_and_day_number(self):
        """Real-world external nodelists often use this fuller phrasing
        (weekday prefix, 'Day number N' instead of bare 'Day N') --
        confirm the tolerant extraction still works, not just this
        software's own simplified format."""
        from anetbbs.echomail.nodelist import parse_header
        content = (';A fidonet nodelist for Wednesday, October 15, 1997 '
                  '-- Day number 288 : 12345\n')
        result = parse_header(content)
        self.assertEqual(result['day_of_year'], 288)
        self.assertEqual(result['crc_checksum'], '12345')
        import datetime
        self.assertEqual(result['release_date'], datetime.date(1997, 10, 15))

    def test_non_comment_first_line_falls_back_cleanly(self):
        from anetbbs.echomail.nodelist import parse_header
        import datetime
        result = parse_header('Zone,1,FidoNet,World,,,,\n')
        self.assertEqual(result['day_of_year'], 1)
        self.assertEqual(result['release_date'], datetime.date.today())
        self.assertEqual(result['crc_checksum'], '')

    def test_empty_content_does_not_crash(self):
        from anetbbs.echomail.nodelist import parse_header
        result = parse_header('')
        self.assertEqual(result['day_of_year'], 1)


if __name__ == '__main__':
    unittest.main()
