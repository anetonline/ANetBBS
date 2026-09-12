"""Regression test for a real Low/Medium-severity finding from a
security/performance audit: install.sh and update.sh both build
/etc/sudoers.d/anetbbs by `sed`-ing deploy/sudoers.anetbbs into a
$SUDOERS_DST.tmp (and, on the I/O-logging-unsupported retry path,
$SUDOERS_DST.tmp2) file with a plain `sed ... > file` redirection --
created at the ambient umask (typically 644, world-readable) and only
locked down to 0440 by the `chmod 0440 "$SUDOERS_DST"` that runs AFTER
`mv` install it to its final location. Between creation and that final
chmod, the pending sudoers fragment (revealing the real INSTALL_DIR
path and exactly which commands SERVICE_USER may run passwordless as
root) sits world-readable under /etc/sudoers.d/ -- the same race-window
class already fixed elsewhere in both scripts for .env and anetbbs.db
(see their own "umask" sections).

Fixed by chmod'ing each .tmp/.tmp2 file to 0440 immediately after it is
created, before visudo -cf even reads it, so there is no window where
it is readable by anyone but root.

There's no shell-execution test harness for install.sh/update.sh in
this repo (root, apt/dnf, a real systemd, etc. are all needed), so this
is a structural source check on the script text itself, matching this
project's own established practice (see test_install_sh_db_umask.py's
module docstring).
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / 'install.sh'
UPDATE_SH = REPO_ROOT / 'update.sh'


class SudoersTmpFilePermissionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.install_text = INSTALL_SH.read_text()
        cls.update_text = UPDATE_SH.read_text()

    def _assert_chmod_precedes_visudo(self, script_text, script_name,
                                       tmp_var, visudo_snippet):
        """Find the first `visudo -cf "$SUDOERS_DST.<tmp_var>"` call and
        assert a `chmod 0440 "$SUDOERS_DST.<tmp_var>"` appears somewhere
        before it in the script (i.e. the file is locked down before
        visudo -- or anyone else -- ever gets a chance to read it at a
        looser mode)."""
        visudo_match = re.search(re.escape(visudo_snippet), script_text)
        self.assertIsNotNone(
            visudo_match,
            f'{script_name}: could not find {visudo_snippet!r} -- this '
            'test needs updating to match a script restructure')

        chmod_snippet = f'chmod 0440 "$SUDOERS_DST.{tmp_var}"'
        chmod_match = re.search(re.escape(chmod_snippet), script_text)
        self.assertIsNotNone(
            chmod_match,
            f'{script_name}: expected {chmod_snippet!r} somewhere in the '
            f'script -- $SUDOERS_DST.{tmp_var} must be locked to 0440 '
            'right after creation, not only after the final mv, or it '
            'sits world-readable under /etc/sudoers.d/ in the meantime')

        self.assertLess(
            chmod_match.start(), visudo_match.start(),
            f'{script_name}: {chmod_snippet!r} appears AFTER '
            f'{visudo_snippet!r} instead of before it -- the temp '
            'sudoers fragment is already world-readable by the time '
            "it's locked down")

    def test_install_sh_tmp_locked_before_visudo(self):
        self._assert_chmod_precedes_visudo(
            self.install_text, 'install.sh', 'tmp',
            'if visudo -cf "$SUDOERS_DST.tmp" >/dev/null 2>&1; then')

    def test_install_sh_tmp2_locked_before_visudo(self):
        self._assert_chmod_precedes_visudo(
            self.install_text, 'install.sh', 'tmp2',
            'if visudo -cf "$SUDOERS_DST.tmp2" >/dev/null 2>&1; then')

    def test_update_sh_tmp_locked_before_visudo(self):
        self._assert_chmod_precedes_visudo(
            self.update_text, 'update.sh', 'tmp',
            'VISUDO_ERR=$(visudo -cf "$SUDOERS_DST.tmp" 2>&1 >/dev/null)')

    def test_update_sh_tmp2_locked_before_visudo(self):
        self._assert_chmod_precedes_visudo(
            self.update_text, 'update.sh', 'tmp2',
            'if visudo -cf "$SUDOERS_DST.tmp2" >/dev/null 2>&1; then')


if __name__ == '__main__':
    unittest.main()
