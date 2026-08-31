"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): install.sh's admin-setup Python
heredocs (the primary sudo -u $SERVICE_USER attempt and the root
fallback) call db.create_all() to give birth to anetbbs.db -- the
SQLite file holding password hashes, session tokens, and private
messages. It used to be created at whatever mode the ambient umask
left it (typically 644, world-readable) and only locked down to 600
by a `chmod 600 "$INSTALL_DIR/data"/*.db` much later in the script,
leaving a real window where the freshly-created DB is world-readable.
Same class of bug already fixed for the .env file (see its own
"umask 177" section higher up in the script) -- fixed the same way:
set the restrictive umask immediately before db.create_all(), so the
file is born at the right mode instead of being fixed up after the
fact.

There's no shell-execution test harness for install.sh in this repo
(it needs root, apt/dnf package installs, a real systemd, etc.), so
this is a structural source check on the script text itself, matching
this project's own established practice for shell-script-only fixes
(see test_install_update_reverify_v222.py's module docstring)."""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / 'install.sh'


def _heredoc_bodies(script_text, delimiter):
    """Extract the body text of every `<< 'DELIM' ... DELIM` heredoc in
    the script matching the given delimiter (install.sh uses ADMINEOF
    for the primary attempt and ADMINEOF2 for the root fallback)."""
    pattern = re.compile(
        r"<<\s*'" + re.escape(delimiter) + r"'\n(.*?\n)" + re.escape(delimiter) + r"\n",
        re.DOTALL)
    return pattern.findall(script_text)


class InstallShDbUmaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script_text = INSTALL_SH.read_text()

    def _assert_umask_precedes_create_all(self, delimiter):
        bodies = _heredoc_bodies(self.script_text, delimiter)
        self.assertEqual(
            len(bodies), 1,
            f'expected exactly one {delimiter} heredoc in install.sh -- '
            f'found {len(bodies)}, this test\'s extraction regex may need '
            'updating to match a script restructure')
        body = bodies[0]
        self.assertIn('db.create_all()', body,
                      f'{delimiter} heredoc no longer calls db.create_all() '
                      '-- this test needs updating to match wherever the '
                      'DB file is actually created now')
        # Match the actual CODE lines (anchored at start-of-line, after
        # optional indentation), not the explanatory comment text above
        # them, which also mentions both calls by name in prose.
        umask_match = re.search(r'^\s*os\.umask\(0o177\)\s*$', body, re.MULTILINE)
        create_all_match = re.search(r'^\s*db\.create_all\(\)\s*$', body, re.MULTILINE)
        umask_pos = umask_match.start() if umask_match else -1
        create_all_pos = create_all_match.start() if create_all_match else -1
        self.assertNotEqual(
            umask_pos, -1,
            f'{delimiter} heredoc must set a restrictive umask before '
            'creating anetbbs.db, the same fix already applied to the '
            '.env file -- otherwise the DB is born world-readable and '
            'only locked down by a chmod that runs much later')
        self.assertLess(
            umask_pos, create_all_pos,
            f'{delimiter} heredoc sets os.umask(0o177) AFTER '
            'db.create_all() instead of before it -- the DB file is '
            'already born with the wrong permissions by the time the '
            'umask takes effect')

    def test_primary_service_user_heredoc_sets_restrictive_umask_before_db_create(self):
        self._assert_umask_precedes_create_all('ADMINEOF')

    def test_root_fallback_heredoc_sets_restrictive_umask_before_db_create(self):
        self._assert_umask_precedes_create_all('ADMINEOF2')

    def test_umask_is_restrictive_enough_for_owner_only_access(self):
        # 0o177 masks out all group/other bits and the owner's execute
        # bit, leaving files born at exactly 600 -- correct for a
        # regular file (a directory would need the owner's execute bit
        # too, but nothing here creates directories under this umask).
        self.assertIn('os.umask(0o177)', self.script_text)


if __name__ == '__main__':
    unittest.main()
