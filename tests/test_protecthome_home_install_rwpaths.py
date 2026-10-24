"""Regression test for a real security/reliability audit finding
(2026-10-22, with a real production incident on 2026-10-24 that
corrected the original fix): install.sh and update.sh both generate
systemd units with ProtectHome=yes (hides /home, /root, /run/user from
the service entirely), but only ever carved back $INSTALL_DIR/data and
$INSTALL_DIR/logs via ReadWritePaths= -- never the venv/code tree
itself, and the terminal unit (anetbbs.service) had NO ReadWritePaths=
at all despite also setting ProtectHome=yes.

This is harmless for the default /opt/anetbbs install (entirely outside
ProtectHome's scope), but a REAL install under /home -- a common,
supported choice, and exactly how the project's own live production
instance is deployed -- would have its own venv/binary masked out from
under it by ProtectHome, failing every single generated unit's
ExecStart with ENOENT.

FIRST FIX ATTEMPT (now known broken, superseded): ProtectHome=yes +
ReadOnlyPaths=<whole tree> (to carve a readable hole back through the
empty/masked /home) + each unit's own narrow ReadWritePaths=data/logs.
This was verified against a manual `unshare --mount --user
--map-root-user` mount-namespace reproduction of the mechanism on a dev
machine, which reported EXEC/READ/WRITE-to-data all succeeding and
WRITE-to-code correctly failing -- but that reproduction did NOT match
real systemd's own behavior. Confirmed via an actual throwaway systemd
unit on production (systemd 249, Ubuntu 249.11-0ubuntu3.23) that
ProtectHome=yes unconditionally blocks exec of a binary under the
carved-out path regardless of ReadOnlyPaths=/ReadWritePaths= -- this
caused a real ~5 minute production outage during v1.1.54 staging (all
5 services failed to start with "Failed to locate executable ...: No
such file or directory") before being caught and fixed.

ACTUAL FIX, empirically verified against real systemd units on that
same production host (not just reasoned about): ProtectHome=read-only
instead of ProtectHome=yes for a home-rooted install. In read-only
mode, systemd does not mask /home as empty at all -- the whole tree
stays readable+executable by default, so ReadWritePaths= alone is
sufficient to carve out the write exceptions each unit needs. Confirmed
via three real throwaway systemd units on production: (1)
ProtectHome=read-only + narrow ReadWritePaths=data/logs -> exec
succeeds, (2) ProtectHome=read-only + broad ReadWritePaths=<whole
tree> -> exec succeeds, (3) ProtectHome=read-only with NO
ReadWritePaths at all -> a write to data/ correctly fails with
"Read-only file system". The separate HOME_ROPATH/ReadOnlyPaths=
carve-out the first attempt needed is gone entirely -- read-only mode
makes it redundant.

What varies by install location now is just two things:
  - RWPATHS: the WHOLE install tree, read-write. Used ONLY by
    anetbbs-web.service (its self-upgrade feature needs it regardless
    of install location).
  - PROTECTHOME_MODE: "read-only" for a home-rooted install, "yes"
    otherwise (default /opt/anetbbs install, entirely outside
    ProtectHome's scope regardless -- no behavior change there).

These tests actually EXTRACT the real case-statement text and heredocs
from the live script files and RUN/render them with a controlled
$INSTALL_DIR, rather than reimplementing the logic in Python (which
could silently drift from what the shipped script actually does) or
only checking that some static string is present (which would catch a
typo but not a logic bug in which branch fires for which path). This
still cannot catch the class of bug that actually caused the 2026-10-24
outage -- that a directive does what systemd's own real unit-execution
engine does, not what a hand-built mount-namespace reproduction
suggests -- see tests/test_update_sh_hardening_retrofit.py's docstring
for why that gap is now explicitly documented as a known limitation of
this whole test file's approach, not papered over.
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_CASE_BLOCK_RE = re.compile(
    r'_INSTALL_DIR_REAL="\$\(realpath -m "\$INSTALL_DIR".*?esac',
    re.DOTALL,
)


def _extract_case_block(script_text):
    m = _CASE_BLOCK_RE.search(script_text)
    assert m, 'could not find the RWPATHS/PROTECTHOME_MODE case-statement block'
    return m.group(0)


def _resolve_vars(script_text, install_dir):
    """Runs the REAL extracted case-statement from the script (not a
    reimplementation) in an actual bash subprocess with the given
    INSTALL_DIR, and returns (RWPATHS, PROTECTHOME_MODE)."""
    case_block = _extract_case_block(script_text)
    probe = (f'INSTALL_DIR={install_dir!r}\n{case_block}\n'
             'printf \'%s\\x1f%s\' "$RWPATHS" "$PROTECTHOME_MODE"\n')
    result = subprocess.run(
        ['bash', '-c', probe], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f'case-statement probe failed: {result.stderr}')
    rwpaths, _, mode = result.stdout.partition('\x1f')
    return rwpaths, mode


def _extract_heredoc(script_text, unit_name):
    pattern = re.compile(
        r'cat > .*' + re.escape(unit_name) + r'\.service << SVCEOF\n'
        r'(.*?)\nSVCEOF',
        re.DOTALL,
    )
    m = pattern.search(script_text)
    assert m, f'could not find a {unit_name}.service heredoc'
    return m.group(1)


def _render_heredoc(script_text, unit_name, install_dir, venv_dir,
                    rwpaths, protecthome_mode):
    """Renders a heredoc body the way bash's unquoted <<SVCEOF actually
    would, substituting the handful of variables these units reference
    -- so a test can assert on the REAL FINAL unit-file text a sysop
    would get, not just the pre-substitution template."""
    body = _extract_heredoc(script_text, unit_name)
    body = body.replace('$PROTECTHOME_MODE', protecthome_mode)
    body = body.replace('$RWPATHS', rwpaths)
    body = body.replace('$INSTALL_DIR', install_dir)
    body = body.replace('$VENV_DIR', venv_dir)
    return body


class RWPathsAndProtectHomeModeResolutionTests(unittest.TestCase):
    """Exercises the real case-statement logic with various INSTALL_DIR
    values, for both install.sh and update.sh independently (they carry
    separate copies of this block, not a shared library)."""

    @classmethod
    def setUpClass(cls):
        cls.install_sh = (REPO_ROOT / 'install.sh').read_text()
        cls.update_sh = (REPO_ROOT / 'update.sh').read_text()

    def test_default_opt_install_keeps_the_original_narrow_rwpaths_and_yes_mode(self):
        """No regression for the common case: RWPATHS is the exact same
        value as before this fix existed, and PROTECTHOME_MODE is
        "yes" (the original, stronger setting -- safe here since
        /opt/anetbbs is entirely outside ProtectHome's scope)."""
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/opt/anetbbs')
            self.assertEqual(rw, '/opt/anetbbs/data /opt/anetbbs/logs')
            self.assertEqual(mode, 'yes')

    def test_home_rooted_install_gets_full_rwpaths_and_readonly_mode(self):
        """The actual live-deployment shape (/home/stingray/anetbbs):
        RWPATHS (for the self-upgrading web unit) is the whole tree,
        and PROTECTHOME_MODE is "read-only" -- not "yes" -- so the
        whole tree stays readable+executable without needing any
        separate carve-out just to make ExecStart work."""
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/home/stingray/anetbbs')
            self.assertEqual(rw, '/home/stingray/anetbbs')
            self.assertEqual(mode, 'read-only')

    def test_different_home_user_also_gets_readonly_mode(self):
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/home/someoperator/bbs')
            self.assertEqual(rw, '/home/someoperator/bbs')
            self.assertEqual(mode, 'read-only')

    def test_root_rooted_install_also_gets_readonly_mode(self):
        """ProtectHome also covers /root -- an install directly under
        it needs the same read-only mode, not just /home."""
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/root/anetbbs')
            self.assertEqual(rw, '/root/anetbbs')
            self.assertEqual(mode, 'read-only')

    def test_a_path_merely_containing_the_word_home_is_not_mismatched(self):
        """Must not falsely match a path like /opt/home-brew-anetbbs or
        /srv/homebase -- only a real /home/* or /root* prefix."""
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/opt/home-brew-anetbbs')
            self.assertEqual(rw, '/opt/home-brew-anetbbs/data '
                                '/opt/home-brew-anetbbs/logs')
            self.assertEqual(mode, 'yes')
            rw2, mode2 = _resolve_vars(script, '/srv/homebase')
            self.assertEqual(rw2, '/srv/homebase/data /srv/homebase/logs')
            self.assertEqual(mode2, 'yes')

    def test_trailing_slash_and_dotdot_normalization_still_match_home(self):
        """realpath -m should normalize a messier path before the case
        match runs, so a trailing slash or an embedded .. doesn't dodge
        the /home/* pattern, and the normalized form is what RWPATHS
        ends up using."""
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/home/stingray/./anetbbs/')
            self.assertEqual(rw, '/home/stingray/anetbbs')
            self.assertEqual(mode, 'read-only')


class LeastPrivilegePerUnitTests(unittest.TestCase):
    """Confirms RWPATHS is assigned to the RIGHT unit -- only the
    self-upgrading web unit gets full read-write to the whole tree; the
    other four keep their pre-existing narrow data/logs write access,
    unchanged by this fix."""

    @classmethod
    def setUpClass(cls):
        cls.install_sh = (REPO_ROOT / 'install.sh').read_text()
        cls.update_sh = (REPO_ROOT / 'update.sh').read_text()

    def test_only_web_unit_uses_the_broad_rwpaths_variable(self):
        non_web_units = ('anetbbs', 'anetbbs-mrc-bridge',
                         'anetbbs-binkp', 'anetbbs-finger')
        for script, name in ((self.install_sh, 'install.sh'),
                             (self.update_sh, 'update.sh')):
            web_body = _extract_heredoc(script, 'anetbbs-web')
            self.assertIn('ReadWritePaths=$RWPATHS', web_body,
                          f'{name}: anetbbs-web should use the broad $RWPATHS')
            for unit in non_web_units:
                body = _extract_heredoc(script, unit)
                self.assertNotIn(
                    'ReadWritePaths=$RWPATHS', body,
                    f'{name}: {unit}.service should NOT get the broad '
                    '$RWPATHS (full read-write over the whole tree) -- '
                    'it never writes to its own code, only data/logs')

    def test_all_five_units_use_the_protecthome_mode_variable(self):
        units = ('anetbbs-web', 'anetbbs', 'anetbbs-mrc-bridge',
                 'anetbbs-binkp', 'anetbbs-finger')
        for script, name in ((self.install_sh, 'install.sh'),
                             (self.update_sh, 'update.sh')):
            for unit in units:
                body = _extract_heredoc(script, unit)
                self.assertIn(
                    'ProtectHome=$PROTECTHOME_MODE', body,
                    f'{name}: {unit}.service must set ProtectHome from '
                    'the $PROTECTHOME_MODE variable, not a hardcoded '
                    "literal, so a home-rooted install gets read-only "
                    "mode instead of the broken yes+carve-out approach")

    def test_the_four_non_upgrading_units_keep_their_narrow_rw(self):
        units = ('anetbbs', 'anetbbs-mrc-bridge', 'anetbbs-binkp',
                 'anetbbs-finger')
        for script, name in ((self.install_sh, 'install.sh'),
                             (self.update_sh, 'update.sh')):
            for unit in units:
                body = _extract_heredoc(script, unit)
                self.assertIn(
                    'ReadWritePaths=$INSTALL_DIR/data $INSTALL_DIR/logs',
                    body, f'{name}: {unit}.service lost its narrow '
                    'data/logs ReadWritePaths=')

    def test_no_unit_still_has_a_readonlypaths_carve_out(self):
        """The whole point of switching to ProtectHome=read-only is
        that the separate ReadOnlyPaths= carve-out this fix used to
        need is now unnecessary -- it must be gone from every unit,
        not just left as harmless-but-redundant leftover text."""
        units = ('anetbbs-web', 'anetbbs', 'anetbbs-mrc-bridge',
                 'anetbbs-binkp', 'anetbbs-finger')
        for script, name in ((self.install_sh, 'install.sh'),
                             (self.update_sh, 'update.sh')):
            for unit in units:
                body = _extract_heredoc(script, unit)
                self.assertNotIn(
                    'ReadOnlyPaths=', body,
                    f'{name}: {unit}.service still has a ReadOnlyPaths= '
                    'carve-out -- this should have been removed when '
                    'switching to ProtectHome=read-only')

    def test_no_unit_has_the_old_broad_rwpaths_applied_to_non_web_units(self):
        """Guards against a half-applied narrowing: no non-web heredoc
        should still contain a bare ReadWritePaths=$RWPATHS left over
        from the first draft of this fix."""
        units = ('anetbbs', 'anetbbs-mrc-bridge', 'anetbbs-binkp',
                 'anetbbs-finger')
        for script in (self.install_sh, self.update_sh):
            for unit in units:
                body = _extract_heredoc(script, unit)
                self.assertNotIn('ReadWritePaths=$RWPATHS', body)

    def test_terminal_unit_still_has_no_protectsystem(self):
        """The fix must not have accidentally added ProtectSystem= to
        anetbbs.service -- it deliberately has none (door-game
        subprocess paths can live anywhere on disk; DOSBox's JIT needs
        writable+executable memory)."""
        for script in (self.install_sh, self.update_sh):
            body = _extract_heredoc(script, 'anetbbs')
            directive_only = '\n'.join(
                line for line in body.splitlines()
                if not line.lstrip().startswith('#'))
            self.assertNotIn('ProtectSystem=', directive_only)
            self.assertIn('ProtectHome=$PROTECTHOME_MODE', directive_only)


class RenderedUnitFileContentTests(unittest.TestCase):
    """Renders the actual, final, post-substitution unit-file text for
    a /home-rooted install and checks the real end result a sysop would
    get written to /etc/systemd/system/ -- not just the template."""

    @classmethod
    def setUpClass(cls):
        cls.install_sh = (REPO_ROOT / 'install.sh').read_text()
        cls.update_sh = (REPO_ROOT / 'update.sh').read_text()

    def test_rendered_terminal_unit_for_a_home_install_uses_readonly_mode(self):
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/home/stingray/anetbbs')
            rendered = _render_heredoc(
                script, 'anetbbs', '/home/stingray/anetbbs',
                '/home/stingray/anetbbs/venv', rw, mode)
            self.assertIn('ProtectHome=read-only', rendered)
            self.assertNotIn('ProtectHome=yes', rendered)
            self.assertNotIn('ReadOnlyPaths=', rendered)
            self.assertIn(
                'ReadWritePaths=/home/stingray/anetbbs/data '
                '/home/stingray/anetbbs/logs', rendered)
            self.assertIn(
                'ExecStart=/home/stingray/anetbbs/venv/bin/anetbbs',
                rendered)

    def test_rendered_web_unit_for_a_home_install_keeps_full_tree_rw(self):
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/home/stingray/anetbbs')
            rendered = _render_heredoc(
                script, 'anetbbs-web', '/home/stingray/anetbbs',
                '/home/stingray/anetbbs/venv', rw, mode)
            self.assertIn('ProtectHome=read-only', rendered)
            self.assertIn('ReadWritePaths=/home/stingray/anetbbs', rendered)
            self.assertNotIn('ReadOnlyPaths=', rendered)

    def test_rendered_units_for_the_default_install_use_yes_mode_unchanged(self):
        """The strongest possible no-regression check: for the default
        /opt/anetbbs case, ProtectHome stays "yes" (the original,
        stronger setting) and the rendered RWPaths text is identical
        to what the pre-fix hardcoded literals would have produced."""
        for script in (self.install_sh, self.update_sh):
            rw, mode = _resolve_vars(script, '/opt/anetbbs')
            for unit in ('anetbbs-mrc-bridge', 'anetbbs-binkp',
                        'anetbbs-finger', 'anetbbs-web'):
                rendered = _render_heredoc(
                    script, unit, '/opt/anetbbs', '/opt/anetbbs/venv',
                    rw, mode)
                self.assertIn('ProtectHome=yes', rendered)
                self.assertIn(
                    'ReadWritePaths=/opt/anetbbs/data /opt/anetbbs/logs',
                    rendered)
                self.assertNotIn('ReadOnlyPaths=', rendered)


if __name__ == '__main__':
    unittest.main()
