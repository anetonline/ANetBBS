"""Regression test for a real, live bug found while auditing docs/INSTALL.md:
install.sh and update.sh's MRC bridge config.json generation wrote
info_web/info_sysop/info_desc (and never wrote telnet/ssh contact info
at all) -- but mrc/bridge/main.py (the actual running bridge) reads
bbs_website/bbs_telnet/bbs_ssh/bbs_sysop/bbs_description, per the
shipped reference config mrc/bridge/config.example.json. Every fresh
install's BBS info therefore always showed blank to every other
MRC-network BBS looking this one up via /info -- not a rare edge case,
the default outcome for every install with MRC enabled, regardless of
what the sysop actually entered in the wizard.

Fixed by correcting the key names (and, for install.sh specifically,
adding real telnet/ssh contact strings derived from the wizard's own
DOMAIN/TELNET_PORT/SSH_PORT/ENABLE_TELNET/ENABLE_SSH inputs -- those
aren't available to update.sh's own generation path, since DOMAIN was
never persisted to .env; that field stays honestly blank there rather
than guessed).

This extracts and runs the REAL generation blocks out of install.sh/
update.sh in actual bash + python3, not a reimplementation, so the
test can't drift from what's actually shipped -- same established
pattern as test_mrc_platform_info_version_decoupling.py and
test_update_sh_mrc_nginx_port_extraction.py.
"""
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The real keys mrc/bridge/main.py actually reads (see its own
# self.config[...]/self.config.get(...) call sites).
REAL_KEYS = {'bbs_website', 'bbs_telnet', 'bbs_ssh', 'bbs_sysop', 'bbs_description'}
# The old, never-read keys the bug wrote instead.
STALE_KEYS = {'info_web', 'info_sysop', 'info_desc'}


def _run_block(block_text, extra_env_lines):
    """Run *block_text* (real bash extracted from install.sh/update.sh)
    in a real bash subprocess, after the given extra setup lines, and
    return the parsed config.json it wrote."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / 'config.json'
        script = (
            'set -e\n'
            + extra_env_lines
            + f'\nMRC_CONFIG_FILE="{config_path}"\n'
            f'MRC_BRIDGE_CONFIG="{config_path}"\n'
            + block_text
        )
        result = subprocess.run(['bash', '-c', script],
                                 capture_output=True, text=True)
        assert result.returncode == 0, (
            f'block exited {result.returncode}\n'
            f'--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}')
        assert config_path.exists(), (
            f'block did not write a config file\n'
            f'--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}')
        return json.loads(config_path.read_text())


class InstallShMrcConfigFieldNamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (REPO_ROOT / 'install.sh').read_text()
        # From the INFO_WEB conditional through the end of the MRCPYEOF
        # heredoc -- everything that actually builds and writes config.json.
        m = re.search(
            r'    # Build info_web:.*?\nMRCPYEOF\n',
            cls.text, re.DOTALL)
        assert m is not None, "couldn't find the MRC config-generation block in install.sh"
        cls.block = m.group(0)

    def _generate(self, **shell_vars):
        base_env = {
            'DOMAIN': 'bbs.example.com',
            'ENABLE_SSL': 'y',
            'ENABLE_TELNET': 'y',
            'TELNET_PORT': '2233',
            'ENABLE_SSH': 'y',
            'SSH_PORT': '2234',
            'MRC_BACKEND': 'native',
            'BBS_NAME': 'Test BBS',
            'MRC_CLIENT_COMPAT_VERSION': '1.3.9',
            'ADMIN_USER': 'sysop',
            'BBS_DESC': 'A friendly test BBS',
            'MRC_BRIDGE_PORT_DEFAULT': '5001',
            'INSTALL_DIR': '/opt/anetbbs',
        }
        base_env.update(shell_vars)
        setup = ''.join(f'{k}="{v}"\n' for k, v in base_env.items())
        return _run_block(self.block, setup)

    def test_uses_the_real_keys_the_bridge_actually_reads(self):
        config = self._generate()
        self.assertTrue(REAL_KEYS.issubset(config.keys()),
                        f'missing keys: {REAL_KEYS - config.keys()}')

    def test_no_longer_writes_the_stale_unread_keys(self):
        config = self._generate()
        self.assertFalse(STALE_KEYS & config.keys(),
                         f'still writing stale keys: {STALE_KEYS & config.keys()}')

    def test_website_sysop_description_populated_correctly(self):
        config = self._generate()
        self.assertEqual(config['bbs_website'], 'https://bbs.example.com')
        self.assertEqual(config['bbs_sysop'], 'sysop')
        self.assertEqual(config['bbs_description'], 'A friendly test BBS')

    def test_telnet_and_ssh_built_from_domain_and_real_ports(self):
        config = self._generate()
        self.assertEqual(config['bbs_telnet'], 'bbs.example.com:2233')
        self.assertEqual(config['bbs_ssh'], 'bbs.example.com:2234')

    def test_telnet_blank_when_telnet_disabled(self):
        config = self._generate(ENABLE_TELNET='n')
        self.assertEqual(config['bbs_telnet'], '')
        # ssh unaffected by telnet being off
        self.assertEqual(config['bbs_ssh'], 'bbs.example.com:2234')

    def test_ssh_blank_when_ssh_disabled(self):
        config = self._generate(ENABLE_SSH='n')
        self.assertEqual(config['bbs_ssh'], '')

    def test_everything_blank_on_a_local_only_test_install(self):
        config = self._generate(DOMAIN='localhost')
        self.assertEqual(config['bbs_website'], '')
        self.assertEqual(config['bbs_telnet'], '')
        self.assertEqual(config['bbs_ssh'], '')

    def test_http_used_when_ssl_not_enabled(self):
        config = self._generate(ENABLE_SSL='n')
        self.assertEqual(config['bbs_website'], 'http://bbs.example.com')


class UpdateShMrcConfigFieldNamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (REPO_ROOT / 'update.sh').read_text()
        m = re.search(
            r'    BBS_NAME="\$\{EXISTING_ENV\[BBS_NAME\].*?\nMRCPYEOF\n',
            cls.text, re.DOTALL)
        assert m is not None, "couldn't find the MRC config-generation block in update.sh"
        cls.block = m.group(0)

    def _generate(self, existing_env_assignments=''):
        setup = (
            'declare -A EXISTING_ENV\n'
            + existing_env_assignments
            + '\nINSTALL_DIR="/opt/anetbbs"\n'
        )
        return _run_block(self.block, setup)

    def test_uses_the_real_keys_the_bridge_actually_reads(self):
        config = self._generate()
        self.assertTrue(REAL_KEYS.issubset(config.keys()),
                        f'missing keys: {REAL_KEYS - config.keys()}')

    def test_no_longer_writes_the_stale_unread_keys(self):
        config = self._generate()
        self.assertFalse(STALE_KEYS & config.keys(),
                         f'still writing stale keys: {STALE_KEYS & config.keys()}')

    def test_description_populated_from_existing_env(self):
        config = self._generate(
            'EXISTING_ENV[BBS_DESCRIPTION]="An upgraded test BBS"\n')
        self.assertEqual(config['bbs_description'], 'An upgraded test BBS')

    def test_website_telnet_ssh_sysop_left_honestly_blank(self):
        """DOMAIN and ADMIN_USER were never persisted to .env, so
        update.sh's own generation path (fires only when enabling MRC
        fresh on an already-existing install) has no reliable source
        for these -- must stay blank, not guessed."""
        config = self._generate()
        self.assertEqual(config['bbs_website'], '')
        self.assertEqual(config['bbs_telnet'], '')
        self.assertEqual(config['bbs_ssh'], '')
        self.assertEqual(config['bbs_sysop'], '')


if __name__ == '__main__':
    unittest.main()
