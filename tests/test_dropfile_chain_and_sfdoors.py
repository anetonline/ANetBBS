"""Tests for the CHAIN.TXT and SFDOORS.DAT drop-file generators, added
so ANetBBS can launch doors expecting those formats (OpenDoors-based
doors like ANetCHESS support both natively). Field positions are
verified against OpenDoors' own real parser
(third_party OpenDoors source: ODInEx1.c's `FOUND_CHAIN_TXT` branch
and `ODInitReadSFDoorsDAT()`), not just checked for "looks reasonable"
-- the same real-consumer-verification discipline used for the
existing DOOR.SYS/DORINFO1.DEF/DOOR32.SYS generators.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ChainTxtTests(unittest.TestCase):
    def _user(self, **overrides):
        user = {'id': 42, 'username': 'Stingray', 'display_name': 'Stingray',
                'email': 'u@example.com', 'is_admin': False}
        user.update(overrides)
        return user

    def test_has_at_least_30_lines(self):
        from anetbbs.games.dropfile import generate_chain_txt
        content = generate_chain_txt(self._user(), node_number=1)
        lines = content.split('\r\n')
        # 30 real lines + one trailing empty element from the final \r\n
        self.assertGreaterEqual(len(lines), 30)

    def test_user_number_and_handle_and_name(self):
        from anetbbs.games.dropfile import generate_chain_txt
        content = generate_chain_txt(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[0], '42')          # Line 1: user number
        self.assertEqual(lines[1], 'Stingray')     # Line 2: handle
        self.assertEqual(lines[2], 'Stingray')     # Line 3: name (no space -> no 'User' suffix)

    def test_time_remaining_is_in_seconds_not_minutes(self):
        # OpenDoors divides line 16 by 60 immediately after reading --
        # confirmed against ODInEx1.c, not assumed. Passing minutes
        # directly here would silently give the user 1/60th of the
        # session time they were supposed to get.
        from anetbbs.games.dropfile import generate_chain_txt
        content = generate_chain_txt(self._user(), node_number=1, minutes_remaining=60)
        lines = content.split('\r\n')
        self.assertEqual(lines[15], '3600')  # Line 16: 60 minutes = 3600 seconds

    def test_security_level_reflects_admin_flag(self):
        from anetbbs.games.dropfile import generate_chain_txt
        content = generate_chain_txt(self._user(is_admin=True), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[10], '200')  # Line 11: security level
        self.assertEqual(lines[11], '1')    # Line 12: is sysop


class SfdoorsDatTests(unittest.TestCase):
    def _user(self, **overrides):
        user = {'id': 7, 'username': 'Keyop', 'display_name': 'Keyop',
                'email': 'u@example.com', 'is_admin': False}
        user.update(overrides)
        return user

    def test_has_at_least_33_required_lines(self):
        # Lines 1-33 are strictly required by OpenDoors' parser (it
        # fails outright if any are missing); 34+ are best-effort.
        from anetbbs.games.dropfile import generate_sfdoors_dat
        content = generate_sfdoors_dat(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertGreaterEqual(len(lines), 33)

    def test_user_number_and_name(self):
        from anetbbs.games.dropfile import generate_sfdoors_dat
        content = generate_sfdoors_dat(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[0], '7')       # Line 1: user number
        self.assertEqual(lines[1], 'Keyop')   # Line 2: user name

    def test_time_remaining_is_in_minutes_not_seconds(self):
        # Unlike CHAIN.TXT, SFDOORS.DAT's own parser reads this field
        # directly with no /60 conversion -- confirmed against the
        # real ODInitReadSFDoorsDAT() implementation.
        from anetbbs.games.dropfile import generate_sfdoors_dat
        content = generate_sfdoors_dat(self._user(), node_number=1, minutes_remaining=45)
        lines = content.split('\r\n')
        self.assertEqual(lines[6], '45')  # Line 7: time remaining, minutes

    def test_ansi_flag_is_capital_t(self):
        # OpenDoors checks szIFTemp[0] == 'T' after strupr() -- any
        # other value is treated as false.
        from anetbbs.games.dropfile import generate_sfdoors_dat
        content = generate_sfdoors_dat(self._user(), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[9], 'T')  # Line 10: ANSI mode

    def test_sysop_next_flag_reflects_admin(self):
        from anetbbs.games.dropfile import generate_sfdoors_dat
        content = generate_sfdoors_dat(self._user(is_admin=True), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[16], 'T')  # Line 17: sysop-next flag
        self.assertEqual(lines[10], '200')  # Line 11: security level


class WriteDropFileDispatchTests(unittest.TestCase):
    """Confirms the two new formats are reachable through the same
    write_drop_file() dispatch every other format goes through, not
    just callable directly."""

    def test_chain_txt_dispatches_and_writes_a_file(self):
        import tempfile
        import os
        from anetbbs.games.dropfile import write_drop_file

        class FakeGame:
            drop_file_type = 'chain.txt'
            game_type = 'door_native'

            def __init__(self, path):
                self.drop_file_path = path

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'CHAIN.TXT')
            user = {'id': 1, 'username': 'Test', 'is_admin': False}
            result = write_drop_file(user, FakeGame(path), node_number=1)
            self.assertEqual(result, path)
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                self.assertIn('Test', f.read())

    def test_sfdoors_dat_dispatches_and_writes_a_file(self):
        import tempfile
        import os
        from anetbbs.games.dropfile import write_drop_file

        class FakeGame:
            drop_file_type = 'sfdoors.dat'
            game_type = 'door_native'

            def __init__(self, path):
                self.drop_file_path = path

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'SFDOORS.DAT')
            user = {'id': 1, 'username': 'Test', 'is_admin': False}
            result = write_drop_file(user, FakeGame(path), node_number=1)
            self.assertEqual(result, path)
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                self.assertIn('Test', f.read())


if __name__ == '__main__':
    unittest.main()
