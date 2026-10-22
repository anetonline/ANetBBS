"""Regression test for a security/performance audit finding: all 5
generated systemd units (install.sh, update.sh) and their deploy/*.service
reference templates lacked ProtectSystem=/ProtectClock=/ProtectHostname=/
MemoryDenyWriteExecute=/SystemCallFilter= -- directives the project's own
newer deploy/anetbbs-mrc-irc-bridge@.service template already demonstrated.

anetbbs.service (terminal/door-games) and anetbbs-web.service (self-upgrade)
deliberately get a narrower subset than the 3 pure-network-daemon units
(mrc-bridge/finger/binkp) -- see the inline comments at each site for why
(DOSBox's dynamic-core JIT needs writable+executable memory; self-upgrade
writes across the whole install tree, not just data/). This test checks
each unit gets exactly the subset it's supposed to, not a blanket "every
directive everywhere" assertion.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STRICT_DIRECTIVES = (
    'ProtectSystem=strict',
    'ProtectClock=yes',
    'ProtectHostname=yes',
    'MemoryDenyWriteExecute=yes',
    'SystemCallFilter=@system-service',
    'SystemCallErrorNumber=EPERM',
)

WEB_DIRECTIVES = (
    'ProtectSystem=full',
    'ProtectClock=yes',
    'ProtectHostname=yes',
    'MemoryDenyWriteExecute=yes',
    'SystemCallFilter=@system-service',
    'SystemCallErrorNumber=EPERM',
)

TERMINAL_DIRECTIVES = (
    'ProtectClock=yes',
    'ProtectHostname=yes',
)

TERMINAL_MUST_NOT_HAVE = (
    'ProtectSystem=',
    'MemoryDenyWriteExecute=',
    'SystemCallFilter=',
)


def _directive_lines(body):
    """Strip comment lines before a 'must not contain' check -- the
    explanatory comments deliberately mention directive names like
    ProtectSystem=strict when explaining why they're NOT applied."""
    return '\n'.join(
        line for line in body.splitlines()
        if not line.lstrip().startswith('#')
    )


def _extract_heredoc(script_text, unit_name):
    pattern = re.compile(
        r'cat > .*' + re.escape(unit_name) + r'\.service << SVCEOF\n'
        r'(.*?)\nSVCEOF',
        re.DOTALL,
    )
    m = pattern.search(script_text)
    assert m, f'could not find a {unit_name}.service heredoc'
    return m.group(1)


class GeneratedUnitDirectiveTests(unittest.TestCase):
    """install.sh / update.sh's inline heredoc-generated units."""

    @classmethod
    def setUpClass(cls):
        cls.install_sh = (REPO_ROOT / 'install.sh').read_text()
        cls.update_sh = (REPO_ROOT / 'update.sh').read_text()

    def test_install_sh_strict_units_have_all_directives(self):
        for unit in ('anetbbs-mrc-bridge', 'anetbbs-binkp', 'anetbbs-finger'):
            body = _extract_heredoc(self.install_sh, unit)
            for directive in STRICT_DIRECTIVES:
                self.assertIn(directive, body, f'{unit} missing {directive}')

    def test_update_sh_strict_units_have_all_directives(self):
        for unit in ('anetbbs-mrc-bridge', 'anetbbs-binkp', 'anetbbs-finger'):
            body = _extract_heredoc(self.update_sh, unit)
            for directive in STRICT_DIRECTIVES:
                self.assertIn(directive, body, f'{unit} missing {directive}')

    def test_install_sh_web_unit_has_full_not_strict(self):
        body = _extract_heredoc(self.install_sh, 'anetbbs-web')
        for directive in WEB_DIRECTIVES:
            self.assertIn(directive, body, f'anetbbs-web missing {directive}')
        self.assertNotIn('ProtectSystem=strict', body)

    def test_update_sh_web_unit_has_full_not_strict(self):
        body = _extract_heredoc(self.update_sh, 'anetbbs-web')
        for directive in WEB_DIRECTIVES:
            self.assertIn(directive, body, f'anetbbs-web missing {directive}')
        self.assertNotIn('ProtectSystem=strict', body)

    def test_install_sh_terminal_unit_has_safe_subset_only(self):
        body = _extract_heredoc(self.install_sh, 'anetbbs')
        for directive in TERMINAL_DIRECTIVES:
            self.assertIn(directive, body, f'anetbbs missing {directive}')
        directive_only = _directive_lines(body)
        for forbidden in TERMINAL_MUST_NOT_HAVE:
            self.assertNotIn(
                forbidden, directive_only,
                f'anetbbs.service should not have {forbidden} -- it '
                'launches door-game subprocesses with sysop-configured '
                'paths and DOSBox needs writable+executable memory')

    def test_update_sh_terminal_unit_has_safe_subset_only(self):
        body = _extract_heredoc(self.update_sh, 'anetbbs')
        for directive in TERMINAL_DIRECTIVES:
            self.assertIn(directive, body, f'anetbbs missing {directive}')
        directive_only = _directive_lines(body)
        for forbidden in TERMINAL_MUST_NOT_HAVE:
            self.assertNotIn(forbidden, directive_only)

    def test_install_sh_readwritepaths_still_carve_out_data_and_logs(self):
        # Real gap found in a later audit, fixed, then refined once
        # more after a follow-up review asked whether the fix granted
        # more write access than necessary: only anetbbs-web.service
        # (which already needs full tree write for its self-upgrade
        # feature, /home or not) uses the broad $RWPATHS variable now.
        # The other 4 units keep this exact original literal
        # unconditionally, and separately gain a conditional
        # ReadOnlyPaths= line (see
        # tests/test_protecthome_home_install_rwpaths.py for the full
        # behavioral verification of both carve-outs).
        for unit in ('anetbbs-mrc-bridge', 'anetbbs-binkp', 'anetbbs-finger',
                     'anetbbs'):
            body = _extract_heredoc(self.install_sh, unit)
            self.assertIn('ReadWritePaths=$INSTALL_DIR/data $INSTALL_DIR/logs',
                           body, f'{unit} has the wrong ReadWritePaths')
        web_body = _extract_heredoc(self.install_sh, 'anetbbs-web')
        self.assertIn('ReadWritePaths=$RWPATHS', web_body,
                      'anetbbs-web should use the broad $RWPATHS')


class DeployTemplateDirectiveTests(unittest.TestCase):
    """The reference deploy/*.service templates (not generated at install
    time, kept in sync by hand -- same split as the heredocs above)."""

    def _read(self, relpath):
        return (REPO_ROOT / relpath).read_text()

    def test_strict_templates_have_all_directives(self):
        for name in ('anetbbs-mrc-bridge', 'anetbbs-binkp', 'anetbbs-finger'):
            body = self._read(f'deploy/{name}.service')
            for directive in STRICT_DIRECTIVES:
                self.assertIn(directive, body, f'{name} missing {directive}')

    def test_web_template_has_full_not_strict(self):
        body = self._read('deploy/anetbbs-web.service')
        for directive in WEB_DIRECTIVES:
            self.assertIn(directive, body)
        self.assertNotIn('ProtectSystem=strict', body)

    def test_terminal_template_has_safe_subset_only(self):
        body = self._read('deploy/anetbbs.service')
        for directive in TERMINAL_DIRECTIVES:
            self.assertIn(directive, body)
        directive_only = _directive_lines(body)
        for forbidden in TERMINAL_MUST_NOT_HAVE:
            self.assertNotIn(forbidden, directive_only)

    def test_user_mode_templates_have_baseline_but_no_protecthome(self):
        for name in ('anetbbs', 'anetbbs-web'):
            body = self._read(f'deploy/user/{name}.service')
            self.assertIn('NoNewPrivileges=yes', body)
            self.assertIn('PrivateTmp=yes', body)
            self.assertIn('SystemCallArchitectures=native', body)
            self.assertNotIn('ProtectHome=', body)


if __name__ == '__main__':
    unittest.main()
