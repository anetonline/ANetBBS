"""Regression test for a real bug found live testing a fresh install:
the web MRC client couldn't connect to the upstream hub at all. The
bridge's own log showed the hub explicitly rejecting every attempt --
"MRC hub says our advertised version (ANETBBS/Linux.x86_64/v1.0.6) is
too old ... hub wants: 1.2.9".

Root cause, traced back through docs/CHANGELOG-beta.md: an earlier
pre-release audit found platform_info's version component hardcoded to
a stale, disconnected "1.3.7" in three places and "fixed" it by
deriving it from ANetBBS's own VERSION file instead -- on the mistaken
assumption the old value was meaningless drift. It wasn't.
platform_info's version segment identifies MRC client/protocol
compatibility to the hub, in the hub's OWN numbering scheme -- a
completely different series from ANetBBS's own v1.0.x release version.
Every fresh install therefore generated a platform_info the hub was
guaranteed to reject, since ANetBBS's version has nothing to do with
whatever floor the hub currently enforces.

Fixed by decoupling platform_info from BBS_VERSION/NEW_VERSION
entirely in both install.sh and update.sh, using a fixed, independent
value (matching the real reference client's own version, 1.3.9 --
comfortably above the hub's observed-live floor of 1.2.9).

This extracts the real generation lines out of install.sh/update.sh
and runs them in actual bash, not a reimplementation, so the test
can't drift from what's actually shipped.
"""
import re
import subprocess
import unittest
from pathlib import Path


class MrcPlatformInfoVersionDecouplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.install_sh = (cls.repo_root / 'install.sh').read_text()
        cls.update_sh = (cls.repo_root / 'update.sh').read_text()

    def _platform_info_line(self, script_text, script_name):
        m = re.search(r'"platform_info":\s*"[^"]*",?', script_text)
        self.assertIsNotNone(
            m, f"couldn't find a platform_info line in {script_name}")
        return m.group(0)

    def test_install_sh_platform_info_does_not_reference_bbs_version(self):
        line = self._platform_info_line(self.install_sh, 'install.sh')
        self.assertNotIn(
            '$BBS_VERSION', line,
            "platform_info must not be derived from ANetBBS's own release "
            "version -- it identifies MRC client/protocol compatibility "
            "in the hub's own separate numbering scheme")

    def test_update_sh_platform_info_does_not_reference_new_version(self):
        line = self._platform_info_line(self.update_sh, 'update.sh')
        self.assertNotIn(
            '$NEW_VERSION', line,
            "platform_info must not be derived from ANetBBS's own release "
            "version -- see install.sh's MRC_CLIENT_COMPAT_VERSION "
            "comment for the full story")

    def test_install_sh_emitted_platform_info_ignores_bbs_version_value(self):
        """Run the real platform_info line from install.sh in actual
        bash, with BBS_VERSION deliberately set to an obviously-fake
        release version -- confirms it can never leak into the emitted
        handshake string regardless of what ANetBBS version is
        installed."""
        line = self._platform_info_line(self.install_sh, 'install.sh')
        script = (
            'MRC_CLIENT_COMPAT_VERSION="1.3.9"\n'
            'BBS_VERSION="v9.9.9-should-never-appear"\n'
            f'echo {line}\n'
        )
        out = subprocess.run(['bash', '-c', script],
                              capture_output=True, text=True)
        self.assertNotIn('v9.9.9-should-never-appear', out.stdout)
        self.assertIn('1.3.9', out.stdout)

    def test_update_sh_emitted_platform_info_ignores_new_version_value(self):
        line = self._platform_info_line(self.update_sh, 'update.sh')
        script = (
            'NEW_VERSION="v9.9.9-should-never-appear"\n'
            f'echo {line}\n'
        )
        out = subprocess.run(['bash', '-c', script],
                              capture_output=True, text=True)
        self.assertNotIn('v9.9.9-should-never-appear', out.stdout)

    def test_config_example_json_placeholder_is_not_the_misleading_kind(self):
        """The shipped example config previously said
        "VERSION-HERE" for this field, which reads as an invitation to
        fill in ANetBBS's own version -- exactly the wrong instinct.
        Must be a real, usable value instead."""
        example = (self.repo_root / 'mrc' / 'bridge' /
                    'config.example.json').read_text()
        self.assertNotIn('VERSION-HERE', example)


if __name__ == '__main__':
    unittest.main()
