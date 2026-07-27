"""Regression test: anetbbs/echomail/nodelist.py's parse_line() dropped
every point-address entry in an imported nodelist. int(raw_node) was
tried FIRST, unconditionally -- for a point row (e.g. ",567.1,...")
int("567.1") raises ValueError and the function returned None
immediately, well before the dedicated point-parsing block further down
ever ran. That block was dead code: unreachable for exactly the input
shape it existed to handle.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.echomail.nodelist import parse_line, _ParserState


class NodelistPointAddressParsingTests(unittest.TestCase):
    def test_point_address_row_parses_correctly(self):
        state = _ParserState()
        state.current_zone = 1
        state.current_net = 234
        row = parse_line(',567.1,PointSystem,City,Sysop,555-1212,9600', state)
        self.assertIsNotNone(row, 'a point-address row must not be dropped')
        self.assertEqual(row['node'], 567)
        self.assertEqual(row['point'], 1)
        self.assertEqual(row['zone'], 1)
        self.assertEqual(row['net'], 234)

    def test_plain_node_row_still_has_point_zero(self):
        state = _ParserState()
        state.current_zone = 1
        state.current_net = 234
        row = parse_line(',5,PlainSystem,City,Sysop,555-1212,9600', state)
        self.assertIsNotNone(row)
        self.assertEqual(row['node'], 5)
        self.assertEqual(row['point'], 0)

    def test_hub_flagged_point_address_row_parses_correctly(self):
        state = _ParserState()
        state.current_zone = 1
        state.current_net = 234
        row = parse_line('Hub,12.5,HubPoint,City,Sysop,555-1212,9600', state)
        self.assertIsNotNone(row)
        self.assertEqual(row['node'], 12)
        self.assertEqual(row['point'], 5)

    def test_zone_and_net_rows_unaffected(self):
        state = _ParserState()
        zone_row = parse_line('Zone,1,ZoneCoord,City,Sysop,555-1212,9600', state)
        self.assertIsNotNone(zone_row)
        self.assertEqual(zone_row['node'], 0)
        self.assertEqual(state.current_zone, 1)

        net_row = parse_line('Net,234,NetCoord,City,Sysop,555-1212,9600', state)
        self.assertIsNotNone(net_row)
        self.assertEqual(net_row['node'], 0)
        self.assertEqual(state.current_net, 234)


if __name__ == '__main__':
    unittest.main()
