"""Tests for the GAP-style DOOR.SYS generator (generate_door_sys_gap),
added because ANetBBS's existing DOOR.SYS generator (generate_door_sys)
was built and verified against TW2002's own validation warnings, and a
different door (a live sysop report) reading DOOR.SYS via the OTHER
real convention -- what OpenDoors' own dropfile auto-detector calls
"GAP" internally -- read line 19 (the literal string 'GR' in the
existing generator's own convention) as its time-remaining field,
atoi("GR") == 0, and refused to launch with "no time left".

Field positions here are verified against OpenDoors' real parser
(third_party OpenDoors source: ODInEx1.c, DOORSYS_GAP branch,
"/* Read line N. */" source comments counted directly, not assumed) --
same real-consumer-verification discipline as the existing CHAIN.TXT/
SFDOORS.DAT tests.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class DoorSysGapTests(unittest.TestCase):
    def _user(self, **overrides):
        user = {'id': 42, 'username': 'Stingray', 'display_name': 'Stingray',
                'email': 'u@example.com', 'is_admin': False}
        user.update(overrides)
        return user

    def test_has_at_least_52_lines(self):
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertGreaterEqual(len(lines), 52)

    def test_node_number_on_line_4(self):
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(), node_number=7)
        lines = content.split('\r\n')
        self.assertEqual(lines[3], '7')

    def test_user_name_on_line_10(self):
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[9], 'Stingray')

    def test_security_level_reflects_admin_flag(self):
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(is_admin=True), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[14], '200')  # Line 15: security level

    def test_times_called_on_line_16_not_time_remaining(self):
        """This is the field the OTHER DOOR.SYS convention
        (generate_door_sys) puts the time-remaining value on --
        confirming this generator does NOT make that same mistake."""
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(), node_number=1,
                                        minutes_remaining=999)
        lines = content.split('\r\n')
        self.assertNotEqual(lines[15], '999')

    def test_time_remaining_is_minutes_on_line_19_not_seconds(self):
        """The actual bug this generator exists to fix: OpenDoors'
        GAP-style parser reads user_timelimit directly from line 19
        with no /60 conversion (unlike CHAIN.TXT's line 16, which IS
        divided by 60) -- confirmed against ODInEx1.c directly."""
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(), node_number=1,
                                        minutes_remaining=90)
        lines = content.split('\r\n')
        self.assertEqual(lines[18], '90')  # Line 19: minutes, not *60

    def test_line_19_is_never_the_literal_GR_string(self):
        """Direct regression guard for the confirmed live bug: the
        OTHER DOOR.SYS generator's line 19 is the literal string 'GR'
        -- atoi("GR") == 0, which is exactly why a GAP-style-reading
        door reported "no time left" immediately on launch."""
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(), node_number=1,
                                        minutes_remaining=90)
        lines = content.split('\r\n')
        self.assertNotEqual(lines[18], 'GR')

    def test_graphics_marker_on_line_20(self):
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[19], 'GR')

    def test_screen_length_on_line_21(self):
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[20], '24')

    def test_user_record_number_on_line_26(self):
        from anetbbs.games.dropfile import generate_door_sys_gap
        content = generate_door_sys_gap(self._user(id=123), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[25], '123')


class WriteDropFileDoorSysGapDispatchTests(unittest.TestCase):
    """Confirms 'door.sys.gap' is reachable through the same
    write_drop_file() dispatch every other format goes through, and
    resolves to a real DOOR.SYS-named file when given a directory."""

    def test_door_sys_gap_dispatches_and_writes_a_file(self):
        import tempfile
        import os
        from anetbbs.games.dropfile import write_drop_file

        class FakeGame:
            drop_file_type = 'door.sys.gap'
            game_type = 'door_native'

            def __init__(self, path):
                self.drop_file_path = path

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'DOOR.SYS')
            user = {'id': 1, 'username': 'Test', 'is_admin': False}
            result = write_drop_file(user, FakeGame(path), node_number=1)
            self.assertEqual(result, path)
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                self.assertIn('Test', f.read())

    def test_door_sys_gap_resolves_to_real_filename_when_given_a_directory(self):
        import tempfile
        import os
        from anetbbs.games.dropfile import write_drop_file

        class FakeGame:
            drop_file_type = 'door.sys.gap'
            game_type = 'door_native'

            def __init__(self, path):
                self.drop_file_path = path

        with tempfile.TemporaryDirectory() as tmp:
            user = {'id': 1, 'username': 'Test', 'is_admin': False}
            result = write_drop_file(user, FakeGame(tmp + '/'), node_number=1)
            self.assertEqual(os.path.basename(result), 'DOOR.SYS')
            self.assertTrue(os.path.isfile(result))


if __name__ == '__main__':
    unittest.main()
