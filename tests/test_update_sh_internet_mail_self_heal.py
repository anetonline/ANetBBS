"""Regression test for update.sh's Internet Mail self-heal block --
mirrors the existing Finger self-heal block immediately above it in
the real script (same "create the unit file if missing, regardless of
whether the feature was ever enabled -- starting it is a separate,
later decision" shape). Extracts the REAL block out of update.sh and
runs it in actual bash against a throwaway fake "/etc/systemd/system"
directory, same technique as test_update_sh_hardening_retrofit.py,
rather than reimplementing or only grepping the script's text.
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BASH = shutil.which('bash') or '/bin/bash'

_BLOCK_RE = re.compile(
    r'^if \[\[ ! -f /etc/systemd/system/anetbbs-mail\.service \]\]; then\n'
    r'.*?^fi$',
    re.DOTALL | re.MULTILINE,
)


def _extract_self_heal_block():
    content = (REPO_ROOT / 'update.sh').read_text()
    m = _BLOCK_RE.search(content)
    assert m, 'could not find the Internet Mail self-heal block in update.sh'
    block = m.group(0)
    # Two literal references in this block (unlike the single-reference
    # retrofit block elsewhere): the "if [[ ! -f ... ]]" existence check,
    # and the "cat > ..." that writes the new unit file. Both get
    # retargeted below via a blanket string replace.
    occurrences = block.count('/etc/systemd/system')
    assert occurrences == 2, (
        f'expected exactly two literal /etc/systemd/system references to '
        f'retarget for testing, found {occurrences} -- update.sh changed '
        f'shape, this test needs a matching update')
    return block


class InternetMailSelfHealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block = _extract_self_heal_block()

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.fake_root = Path(self.tmpdir.name) / 'etc-systemd-system'
        self.fake_root.mkdir()

    def _run(self, install_dir='/opt/anetbbs', service_user='anetbbs',
             protecthome_mode='read-only', pre_existing=False):
        if pre_existing:
            (self.fake_root / 'anetbbs-mail.service').write_text(
                '[Unit]\nDescription=pre-existing\n')
        retargeted = self.block.replace(
            '/etc/systemd/system', str(self.fake_root))
        script = (
            'info() { echo "INFO: $*"; }\n'
            'ok() { echo "OK: $*"; }\n'
            f'SERVICE_USER={service_user!r}\n'
            f'INSTALL_DIR={install_dir!r}\n'
            f'ENV_FILE={install_dir!r}/.env\n'
            f'VENV_DIR={install_dir!r}/venv\n'
            f'PROTECTHOME_MODE={protecthome_mode!r}\n'
            f'{retargeted}\n'
        )
        result = subprocess.run([_BASH, '-c', script],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0,
                         f'self-heal block failed: {result.stderr}')
        return result

    def test_creates_the_unit_when_missing(self):
        self._run()
        unit = self.fake_root / 'anetbbs-mail.service'
        self.assertTrue(unit.exists())

    def test_unit_content_has_correct_capability_and_execstart(self):
        self._run(install_dir='/opt/anetbbs')
        text = (self.fake_root / 'anetbbs-mail.service').read_text()
        self.assertIn('AmbientCapabilities=CAP_NET_BIND_SERVICE', text)
        self.assertIn('ExecStart=/opt/anetbbs/venv/bin/python -m anetbbs.mail.smtp_server', text)
        self.assertIn('EnvironmentFile=/opt/anetbbs/.env', text)
        self.assertIn('ProtectSystem=strict', text)
        self.assertIn('ReadWritePaths=/opt/anetbbs/data /opt/anetbbs/logs', text)

    def test_protecthome_mode_variable_is_substituted_not_hardcoded(self):
        """Real past outage (v1.1.54): ProtectHome=yes on a /home-rooted
        install blocks exec under its own carve-out regardless of
        ReadWritePaths -- the fix was ProtectHome=read-only via a shared
        $PROTECTHOME_MODE variable, not a hardcoded value per unit.
        This new unit must use that same variable, not reintroduce a
        hardcoded ProtectHome=yes."""
        self._run(protecthome_mode='read-only')
        text = (self.fake_root / 'anetbbs-mail.service').read_text()
        self.assertIn('ProtectHome=read-only', text)
        self.assertNotIn('ProtectHome=yes', text)

    def test_does_not_overwrite_an_already_existing_unit(self):
        self._run(pre_existing=True)
        text = (self.fake_root / 'anetbbs-mail.service').read_text()
        self.assertEqual(text, '[Unit]\nDescription=pre-existing\n')


class EnabledFlagMappingTests(unittest.TestCase):
    """The unit-creation block above is unconditional (dormant until
    started) -- whether it actually gets enabled+started is decided by
    a separate case-statement mapping unit name -> .env flag name, a
    few lines long and not sensibly extractable/runnable on its own.
    Asserting its exact text is still meaningful: a typo'd or missing
    entry here would silently leave a freshly-created anetbbs-mail
    unit dormant forever, the same real bug class the surrounding
    comment in update.sh documents for the other services.
    """

    def test_mail_enabled_flag_mapping_present_in_update_sh(self):
        content = (REPO_ROOT / 'update.sh').read_text()
        self.assertIn('anetbbs-mail)       _enabled_flag="MAIL_ENABLED" ;;', content)

    def test_mail_included_in_the_optional_services_restart_loop(self):
        content = (REPO_ROOT / 'update.sh').read_text()
        self.assertIn(
            'for svc in anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp anetbbs-mail; do',
            content)


if __name__ == '__main__':
    unittest.main()
