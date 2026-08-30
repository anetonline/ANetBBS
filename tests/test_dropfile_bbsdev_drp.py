"""Tests for the BBSDEV.DRP drop-file generator, added so ANetBBS can
launch doors expecting RealDeuce's newer bbsdev.drp format
(https://github.com/RealDeuce/bbsdev.drp) -- RDQ3 and ANetCHESS both
gained a real reader for it (third_party/OpenDoors's own ODInEx1.c,
the FOUND_BBSDEV_DRP branch), verified directly against a real
compiled OpenDoors binary, not just read. Field positions here are
verified against the spec's own 19-line table, the same
real-consumer-verification discipline used for the existing
DOOR.SYS/DORINFO1.DEF/DOOR32.SYS/CHAIN.TXT/SFDOORS.DAT generators.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BbsdevDrpTests(unittest.TestCase):
    def _user(self, **overrides):
        user = {'id': 42, 'username': 'Stingray', 'display_name': 'Stingray',
                'email': 'u@example.com', 'is_admin': False}
        user.update(overrides)
        return user

    def test_has_exactly_19_lines(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1)
        # 19 real lines + one trailing empty element from the final \r\n
        lines = content.split('\r\n')
        self.assertEqual(len(lines), 20)
        self.assertEqual(lines[19], '')

    def test_uses_crlf_line_endings(self):
        # Strip every CRLF pair; if any bare \r or \n survives, a line
        # ending wasn't a matched CRLF pair (note: two ADJACENT blank
        # lines legitimately produce a "\r\n\r\n" run, which contains
        # the substring "\n\r" -- so checking for that substring
        # directly is not a valid way to test this).
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1)
        self.assertEqual(content.count('\r\n'), 19)
        stripped = content.replace('\r\n', '')
        self.assertNotIn('\r', stripped)
        self.assertNotIn('\n', stripped)

    def test_format_version_and_comm_type(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[0], '1.0')     # Line 1: format version
        self.assertEqual(lines[1], 'stdio')   # Line 2: comm type
        self.assertEqual(lines[2], '')        # Line 3: comm params (empty for stdio)

    def test_user_alias_and_unique_key(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[3], 'Stingray')  # Line 4: user alias
        self.assertEqual(lines[4], '42')        # Line 5: unique user key (the db id)

    def test_screen_dims_and_ansi_rip_flags(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[5], '80')  # Line 6: screen width
        self.assertEqual(lines[6], '24')  # Line 7: screen height
        self.assertEqual(lines[7], 'Y')   # Line 8: ANSI
        self.assertEqual(lines[8], 'N')   # Line 9: RIP

    def test_ctermm_and_logoff_deadline_are_empty(self):
        # ANetBBS doesn't detect CTerm or compute an absolute forced-
        # logoff deadline -- both fields are spec-legal to leave empty.
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[9], '')   # Line 10: CTerm version
        self.assertEqual(lines[10], '')  # Line 11: time of logoff

    def test_encoding_and_language(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[11], 'IBM437')  # Line 12: encoding
        self.assertEqual(lines[12], 'en-US')   # Line 13: language

    def test_software_name_board_name_and_sysop_alias(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1,
                                       bbs_name='My Board', sysop_name='TheOp')
        lines = content.split('\r\n')
        self.assertIn('ANetBBS', lines[13])     # Line 14: software name+version
        self.assertEqual(lines[14], 'My Board')  # Line 15: board name (distinct from line 14)
        self.assertEqual(lines[15], 'TheOp')     # Line 16: sysop alias

    def test_sysop_name_defaults_when_not_supplied(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[15], 'Sysop')

    def test_access_level_is_numeric_for_a_normal_user(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(is_admin=False), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[16], '50')  # Line 17: access level

    def test_access_level_is_the_sysop_token_for_an_admin(self):
        # BBSDEV.DRP is the one format here that defines a portable
        # "sysop" token -- more expressive than a magic security
        # number, and worth using since it's available.
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(is_admin=True), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[16], 'sysop')  # Line 17: access level

    def test_node_number_and_show_local_display(self):
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(self._user(), node_number=5)
        lines = content.split('\r\n')
        self.assertEqual(lines[17], '5')  # Line 18: node number
        self.assertEqual(lines[18], 'N')  # Line 19: show local display

    def test_cr_lf_injection_in_username_cannot_shift_fields(self):
        # Same field-injection class dropfile.py's own _u() helper was
        # built to close for every other format (see its docstring) --
        # confirm it also applies here, not just to the older formats.
        from anetbbs.games.dropfile import generate_bbsdev_drp
        content = generate_bbsdev_drp(
            self._user(username='evil\r\nsysop\r\n999'), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(len(lines), 20)
        self.assertEqual(lines[3], 'evilsysop999')


class WriteDropFileBbsdevDrpDispatchTests(unittest.TestCase):
    """Confirms bbsdev.drp is reachable through the same
    write_drop_file() dispatch every other format goes through, not
    just callable directly."""

    def test_bbsdev_drp_dispatches_and_writes_a_file(self):
        import tempfile
        import os
        from anetbbs.games.dropfile import write_drop_file

        class FakeGame:
            drop_file_type = 'bbsdev.drp'
            game_type = 'door_native'

            def __init__(self, path):
                self.drop_file_path = path

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'BBSDEV.DRP')
            user = {'id': 1, 'username': 'Test', 'is_admin': False}
            result = write_drop_file(user, FakeGame(path), node_number=1,
                                     sysop_name='RealSysop')
            self.assertEqual(result, path)
            self.assertTrue(os.path.isfile(path))
            with open(path, newline='') as f:
                content = f.read()
            self.assertIn('Test', content)
            self.assertIn('RealSysop', content)

    def test_bbsdev_drp_resolves_directory_path_to_correct_filename(self):
        import tempfile
        import os
        from anetbbs.games.dropfile import write_drop_file

        class FakeGame:
            drop_file_type = 'bbsdev.drp'
            game_type = 'door_native'

            def __init__(self, path):
                self.drop_file_path = path

        with tempfile.TemporaryDirectory() as tmp:
            user = {'id': 1, 'username': 'Test', 'is_admin': False}
            result = write_drop_file(user, FakeGame(tmp + '/'), node_number=1)
            self.assertEqual(os.path.basename(result), 'BBSDEV.DRP')
            self.assertTrue(os.path.isfile(result))


if __name__ == '__main__':
    unittest.main()
