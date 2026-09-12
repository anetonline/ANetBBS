"""Regression test: tools/fix_anet_import_credentials.py's audit listing
used to print the live A-Net Online door password in the clear
(`password={p!r}`) for every matched game, while the 'Correct config
found' block a few lines further down in the very same script already
masks the same category of secret. Since this tool is meant to be run
freely as a repeatable dry-run audit (no --apply needed) and its stdout
routinely ends up piped to a file or captured in a terminal/screen
session log, the live door password should never appear there just from
listing which games reference the A-Net Online server. Found in a
security/performance audit.

Deliberately imports the tool module directly (not through a Flask app
context) -- _describe_match() is a pure formatting helper with no DB/app
dependency, so this can verify the masking without spinning up a test
database.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.fix_anet_import_credentials import _describe_match  # noqa: E402


class DescribeMatchMasksPasswordTests(unittest.TestCase):
    def test_password_not_in_clear(self):
        g = SimpleNamespace(id=1, name='A-Net Game Server', slug='a-net-game-server')
        creds = ('game.a-net-online.lol:23', 'Zkzl49@ceRP1', 'ANETBBS')
        line = _describe_match(g, creds)
        self.assertNotIn('Zkzl49@ceRP1', line)

    def test_password_masked_with_length_shown(self):
        g = SimpleNamespace(id=1, name='A-Net Game Server', slug='a-net-game-server')
        creds = ('game.a-net-online.lol:23', 'Zkzl49@ceRP1', 'ANETBBS')
        line = _describe_match(g, creds)
        self.assertIn('*' * len('Zkzl49@ceRP1'), line)
        self.assertIn('(12 chars)', line)
        # Host and tag are not secret -- still shown in the clear.
        self.assertIn("host='game.a-net-online.lol:23'", line)
        self.assertIn("tag='ANETBBS'", line)

    def test_missing_credentials_still_flagged(self):
        g = SimpleNamespace(id=2, name='Broken Entry', slug='broken-entry')
        line = _describe_match(g, None)
        self.assertIn('MISSING server address or password', line)


if __name__ == '__main__':
    unittest.main()
