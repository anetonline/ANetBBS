"""Tests for tools/door32_to_bbsdev_drp.py, the standalone DOOR32.SYS
-> BBSDEV.DRP converter built for use on a different BBS (e.g. Jerry's
Synchronet install, which writes DOOR32.SYS natively but has no reason
to know BBSDEV.DRP exists). Deliberately imports the tool module
directly rather than through the `anetbbs` package -- the whole point
is that it has zero dependency on that package, and a test importing
it via `anetbbs.tools...` would defeat the purpose of proving that.
"""
import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(_TOOLS_DIR))

import door32_to_bbsdev_drp as conv  # noqa: E402


_SAMPLE_TELNET = (
    "2\r\n"       # comm type: telnet/socket
    "9\r\n"       # comm/socket handle
    "38400\r\n"   # baud
    "Synchronet 3.20\r\n"  # BBS software name+version
    "42\r\n"      # user number
    "Jerry Reed\r\n"       # real name
    "StingRay\r\n"         # alias
    "200\r\n"     # security level
    "45\r\n"      # minutes remaining
    "1\r\n"       # terminal type (1=ANSI)
    "3\r\n"       # node number
)


class ParseDoor32Tests(unittest.TestCase):
    def test_parses_all_11_fields(self):
        d = conv.parse_door32_sys(_SAMPLE_TELNET)
        self.assertEqual(d['comm_type'], 2)
        self.assertEqual(d['comm_handle'], '9')
        self.assertEqual(d['software_name'], 'Synchronet 3.20')
        self.assertEqual(d['user_num'], '42')
        self.assertEqual(d['real_name'], 'Jerry Reed')
        self.assertEqual(d['alias'], 'StingRay')
        self.assertEqual(d['security'], 200)
        self.assertEqual(d['minutes_left'], 45)
        self.assertEqual(d['terminal_type'], 1)
        self.assertEqual(d['node'], 3)

    def test_accepts_bare_lf_not_just_crlf(self):
        lf_only = _SAMPLE_TELNET.replace('\r\n', '\n')
        d = conv.parse_door32_sys(lf_only)
        self.assertEqual(d['alias'], 'StingRay')

    def test_rejects_too_few_lines(self):
        with self.assertRaises(conv.Door32ConversionError):
            conv.parse_door32_sys("2\r\n9\r\n")

    def test_rejects_non_numeric_required_field(self):
        bad = _SAMPLE_TELNET.replace('200\r\n', 'not-a-number\r\n', 1)
        with self.assertRaises(conv.Door32ConversionError):
            conv.parse_door32_sys(bad)


class BuildBbsdevDrpTests(unittest.TestCase):
    def test_telnet_comm_type_maps_to_socket(self):
        d = conv.parse_door32_sys(_SAMPLE_TELNET)
        content = conv.build_bbsdev_drp(d)
        lines = content.split('\r\n')
        self.assertEqual(lines[1], 'socket')  # line 2
        self.assertEqual(lines[2], '9')       # line 3: the inherited handle

    def test_local_comm_type_maps_to_local_with_empty_param(self):
        local_sample = _SAMPLE_TELNET.replace('2\r\n9\r\n', '0\r\n0\r\n', 1)
        d = conv.parse_door32_sys(local_sample)
        content = conv.build_bbsdev_drp(d)
        lines = content.split('\r\n')
        self.assertEqual(lines[1], 'local')
        self.assertEqual(lines[2], '')

    def test_alias_prefers_handle_falls_back_to_real_name(self):
        d = conv.parse_door32_sys(_SAMPLE_TELNET)
        content = conv.build_bbsdev_drp(d)
        self.assertEqual(content.split('\r\n')[3], 'StingRay')

        no_alias = _SAMPLE_TELNET.replace('StingRay\r\n', '\r\n', 1)
        d2 = conv.parse_door32_sys(no_alias)
        content2 = conv.build_bbsdev_drp(d2)
        self.assertEqual(content2.split('\r\n')[3], 'Jerry Reed')

    def test_unique_user_key_is_the_door32_user_number(self):
        d = conv.parse_door32_sys(_SAMPLE_TELNET)
        content = conv.build_bbsdev_drp(d)
        self.assertEqual(content.split('\r\n')[4], '42')

    def test_ansi_and_rip_flags_from_terminal_type(self):
        # 0=ASCII, 1=ANSI, 2=AVATAR, 3=RIP
        cases = {0: ('N', 'N'), 1: ('Y', 'N'), 2: ('Y', 'N'), 3: ('Y', 'Y')}
        for term_type, (ansi, rip) in cases.items():
            sample = _SAMPLE_TELNET.replace('1\r\n3\r\n', f'{term_type}\r\n3\r\n', 1)
            d = conv.parse_door32_sys(sample)
            content = conv.build_bbsdev_drp(d)
            lines = content.split('\r\n')
            self.assertEqual(lines[7], ansi, f'terminal_type={term_type}')
            self.assertEqual(lines[8], rip, f'terminal_type={term_type}')

    def test_deadline_is_now_plus_minutes_remaining(self):
        from datetime import datetime, timezone
        d = conv.parse_door32_sys(_SAMPLE_TELNET)  # 45 minutes remaining
        fixed_now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
        content = conv.build_bbsdev_drp(d, now=fixed_now)
        self.assertEqual(content.split('\r\n')[10], '2026-08-30T12:45:00Z')

    def test_software_name_and_board_name_default_the_same_when_unset(self):
        d = conv.parse_door32_sys(_SAMPLE_TELNET)
        content = conv.build_bbsdev_drp(d)
        lines = content.split('\r\n')
        self.assertEqual(lines[13], 'Synchronet 3.20')  # line 14
        self.assertEqual(lines[14], 'Synchronet 3.20')  # line 15, defaulted

    def test_board_name_and_sysop_name_overrides(self):
        d = conv.parse_door32_sys(_SAMPLE_TELNET)
        content = conv.build_bbsdev_drp(d, board_name='My BBS', sysop_name='TheOp')
        lines = content.split('\r\n')
        self.assertEqual(lines[14], 'My BBS')
        self.assertEqual(lines[15], 'TheOp')

    def test_output_has_exactly_19_lines(self):
        d = conv.parse_door32_sys(_SAMPLE_TELNET)
        content = conv.build_bbsdev_drp(d)
        self.assertEqual(len(content.split('\r\n')), 20)  # 19 + trailing empty


class ConvertFileEndToEndTests(unittest.TestCase):
    def test_convert_door32_to_bbsdev_drp_round_trip(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmp:
            door32_path = os.path.join(tmp, 'DOOR32.SYS')
            out_path = os.path.join(tmp, 'BBSDEV.DRP')
            with open(door32_path, 'w') as f:
                f.write(_SAMPLE_TELNET)

            content = conv.convert_door32_to_bbsdev_drp(door32_path, out_path)

            self.assertTrue(os.path.isfile(out_path))
            with open(out_path, newline='') as f:
                on_disk = f.read()
            self.assertEqual(on_disk, content)
            self.assertIn('StingRay', on_disk)

    def test_main_cli_writes_file_and_returns_zero(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmp:
            door32_path = os.path.join(tmp, 'DOOR32.SYS')
            out_path = os.path.join(tmp, 'BBSDEV.DRP')
            with open(door32_path, 'w') as f:
                f.write(_SAMPLE_TELNET)

            rc = conv.main([door32_path, out_path, '--sysop-name', 'RealOp'])
            self.assertEqual(rc, 0)
            with open(out_path, newline='') as f:
                self.assertIn('RealOp', f.read())

    def test_main_cli_returns_nonzero_on_malformed_input(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmp:
            door32_path = os.path.join(tmp, 'DOOR32.SYS')
            out_path = os.path.join(tmp, 'BBSDEV.DRP')
            with open(door32_path, 'w') as f:
                f.write('not a real door32.sys file\n')

            rc = conv.main([door32_path, out_path])
            self.assertNotEqual(rc, 0)
            self.assertFalse(os.path.exists(out_path))


if __name__ == '__main__':
    unittest.main()
