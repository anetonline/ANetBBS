"""Regression test for a real bug Jerry hit live: a single-word
username (the normal case -- almost nobody's BBS handle has a space
in it) showed up in doors as "<username> User", e.g. "Stingray" became
"Stingray User". generate_dorinfo() and generate_door32() both used
the literal string 'User' as a placeholder last name whenever
splitting the username produced no second word, instead of an empty
string. generate_door_sys() already got this right; the other two are
now brought in line with it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class DropfileUsernameFallbackTests(unittest.TestCase):
    def _user(self, username):
        return {'id': 1, 'username': username, 'display_name': username,
                'email': 'u@example.com', 'access_level': 10}

    def test_door_sys_single_word_username_has_no_placeholder_last_name(self):
        from anetbbs.games.dropfile import generate_door_sys
        content = generate_door_sys(self._user('Stingray'), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[6], 'Stingray')  # Line 7: first name
        self.assertEqual(lines[7], '')           # Line 8: last name

    def test_dorinfo_single_word_username_has_no_placeholder_last_name(self):
        from anetbbs.games.dropfile import generate_dorinfo
        content = generate_dorinfo(self._user('Stingray'), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[6], 'Stingray')  # User first name
        self.assertEqual(lines[7], '')           # User last name -- was 'User'

    def test_door32_single_word_username_shows_plain_username(self):
        from anetbbs.games.dropfile import generate_door32
        content = generate_door32(self._user('Stingray'), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[5], 'Stingray')  # User's real name -- was 'Stingray User'

    def test_two_word_username_still_splits_normally(self):
        from anetbbs.games.dropfile import generate_door32
        content = generate_door32(self._user('Jerry Reed'), node_number=1)
        lines = content.split('\r\n')
        self.assertEqual(lines[5], 'Jerry Reed')


if __name__ == '__main__':
    unittest.main()
