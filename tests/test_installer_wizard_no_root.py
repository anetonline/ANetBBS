"""Tests for the anetbbs-install wizard's rootless install path.

Real gap addressed here: the wizard (anetbbs/installer/wizard.py) has
been capable of a fully rootless install for a while -- a sysop-chosen
install directory (no /opt requirement), a plain user-space venv, and
an explicit yes/no on installing systemd units at all -- but it was
never documented as such, and its own final "Next steps" message
always printed `sudo systemctl start anetbbs-web anetbbs` even when
the sysop had just said no to installing any systemd units a few steps
earlier. This adds a genuine third option -- systemd USER units
(systemctl --user, no root anywhere), which close the real remaining
gap between "runs in a terminal you have to babysit" and "a real,
persistent background service" while staying rootless -- and fixes the
misleading final message to match whichever of the three choices was
actually made.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.installer.wizard import (
    next_steps_lines, build_env_lines, _install_user_systemd_units, C_CYAN,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_env_kwargs(**overrides):
    kwargs = dict(
        secret_key='deadbeef', db_uri='sqlite:///data/anetbbs.db',
        data_dir='/home/sysop/anetbbs/data', install_dir='/home/sysop/anetbbs',
        web_port='5000', bbs_name='Test BBS', bbs_desc='A test BBS',
        sysop_name='Sysop', bbs_nodes='8', telnet_enabled=True,
        telnet_port='2233', ssh_enabled=True, ssh_port='2234',
        rlogin_enabled=False, rlogin_port='513', echo_enabled=True,
        domain='',
    )
    kwargs.update(overrides)
    return kwargs


class BuildEnvLinesTests(unittest.TestCase):
    """Real live gap this closes: MSP_ENABLED/SYSTAT_ENABLED default to
    true in config.py when unset, but this wizard never asks about
    either -- every install it created was guaranteed to log a
    permission-denied MSP/SYSTAT bind failure on every boot (confirmed
    live: booting a real install produced exactly this). install.sh's
    own wizard already writes *_ENABLED=false for a declined feature;
    this brings anetbbs-install to the same safe default."""

    def test_msp_and_systat_always_disabled(self):
        lines = build_env_lines(**_default_env_kwargs())
        self.assertIn('MSP_ENABLED=false', lines)
        self.assertIn('SYSTAT_ENABLED=false', lines)

    def test_domain_only_included_when_set(self):
        without = build_env_lines(**_default_env_kwargs(domain=''))
        self.assertFalse(any(l.startswith('BBS_DOMAIN=') for l in without))
        with_domain = build_env_lines(**_default_env_kwargs(domain='bbs.example.com'))
        self.assertIn('BBS_DOMAIN=bbs.example.com', with_domain)

    def test_telnet_ssh_rlogin_reflect_the_chosen_flags(self):
        lines = build_env_lines(**_default_env_kwargs(
            telnet_enabled=True, ssh_enabled=False, rlogin_enabled=True))
        self.assertIn('TELNET_ENABLED=true', lines)
        self.assertIn('SSH_ENABLED=false', lines)
        self.assertIn('RLOGIN_ENABLED=true', lines)

    def test_secret_key_and_ports_are_written_through(self):
        lines = build_env_lines(**_default_env_kwargs(
            secret_key='my-real-secret', web_port='9999'))
        self.assertIn('SECRET_KEY=my-real-secret', lines)
        self.assertIn('WEB_PORT=9999', lines)

    def test_output_is_a_valid_dotenv_file(self):
        """Every line must be KEY=value with no blank/malformed lines
        -- write it out and load it back with a real .env parser to
        confirm, rather than just eyeballing the format."""
        lines = build_env_lines(**_default_env_kwargs(domain='bbs.example.com'))
        for line in lines:
            self.assertRegex(line, r'^[A-Z_]+=.*$', f'malformed line: {line!r}')
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / '.env'
            env_path.write_text('\n'.join(lines) + '\n')
            from dotenv import dotenv_values
            parsed = dotenv_values(str(env_path))
            self.assertEqual(parsed['MSP_ENABLED'], 'false')
            self.assertEqual(parsed['BBS_DOMAIN'], 'bbs.example.com')


class NextStepsLinesTests(unittest.TestCase):
    def test_system_mode_prints_sudo_systemctl_start(self):
        lines = next_steps_lines('1', '/opt/anetbbs')
        joined = '\n'.join(lines)
        self.assertIn('sudo systemctl start anetbbs-web anetbbs', joined)
        self.assertNotIn('systemctl --user', joined)
        self.assertNotIn('loginctl', joined)

    def test_user_systemd_mode_never_mentions_sudo(self):
        lines = next_steps_lines('2', '/home/sysop/anetbbs')
        joined = '\n'.join(lines)
        self.assertIn('systemctl --user start anetbbs-web anetbbs', joined)
        self.assertIn('loginctl enable-linger $USER', joined)
        self.assertNotIn('sudo', joined,
                         'the no-root path must never suggest sudo anywhere')

    def test_skip_mode_gives_direct_run_commands_not_a_systemd_reference(self):
        lines = next_steps_lines('3', '/home/sysop/anetbbs')
        joined = '\n'.join(lines)
        self.assertNotIn('systemctl', joined)
        self.assertNotIn('sudo', joined)
        self.assertIn('venv/bin/python', joined)
        self.assertIn('deploy/serve.py', joined)
        self.assertIn('venv/bin/anetbbs', joined)

    def test_skip_mode_uses_the_real_install_dir_not_a_placeholder(self):
        lines = next_steps_lines('3', '/home/sysop/my-bbs')
        joined = '\n'.join(lines)
        self.assertIn('/home/sysop/my-bbs', joined)

    def test_every_mode_ends_with_the_same_checklist_and_control_panel_lines(self):
        for mode in ('1', '2', '3'):
            lines = next_steps_lines(mode, '/opt/anetbbs')
            joined = '\n'.join(lines)
            self.assertIn('/admin/checklist', joined)
            self.assertIn('/admin/control', joined)

    def test_step_numbers_are_sequential_starting_at_one(self):
        """Regression guard for the exact bug class this fixes: the
        step numbers must reflect however many lines actually printed
        for the chosen mode, not a number baked in for a different
        mode's line count."""
        for mode in ('1', '2', '3'):
            lines = next_steps_lines(mode, '/opt/anetbbs')
            # Extract the leading "N." from each line that starts a
            # numbered step (ignores continuation lines like the
            # loginctl parenthetical, which don't start with "N.").
            step_lines = [l for l in lines
                         if len(l) > 2 and l.lstrip()[0:1].isdigit()
                         and l.lstrip()[1:2] == '.']
            step_numbers = [int(l.lstrip()[0]) for l in step_lines]
            self.assertEqual(step_numbers, list(range(1, len(step_numbers) + 1)),
                             f'mode {mode}: step numbers not sequential: '
                             f'{step_numbers}')


class InstallUserSystemdUnitsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake_home = Path(self._tmp.name) / 'home'
        self.fake_home.mkdir()
        self.install_dir = Path(self._tmp.name) / 'anetbbs'
        (self.install_dir / 'deploy' / 'user').mkdir(parents=True)
        # Copy the real templates so this test can't drift from what's
        # actually shipped -- same reasoning as this project's other
        # "extract and run/read the real file" tests.
        for unit in ('anetbbs-web.service', 'anetbbs.service'):
            src = REPO_ROOT / 'deploy' / 'user' / unit
            (self.install_dir / 'deploy' / 'user' / unit).write_text(
                src.read_text())

    def _run(self, tel=True, ssh=True, rl=False):
        with patch('anetbbs.installer.wizard.Path.home',
                  return_value=self.fake_home), \
             patch('anetbbs.installer.wizard.subprocess.run') as fake_run:
            _install_user_systemd_units(self.install_dir, '5000', tel, ssh, rl)
        return fake_run

    def test_writes_units_under_the_users_own_config_dir_not_etc(self):
        self._run()
        target = self.fake_home / '.config' / 'systemd' / 'user'
        self.assertTrue((target / 'anetbbs-web.service').exists())
        self.assertTrue((target / 'anetbbs.service').exists())

    def test_install_dir_substituted_in_place_of_opt_anetbbs(self):
        self._run()
        target = self.fake_home / '.config' / 'systemd' / 'user'
        body = (target / 'anetbbs-web.service').read_text()
        self.assertNotIn('/opt/anetbbs', body)
        self.assertIn(str(self.install_dir), body)

    def test_no_user_or_group_directive_written(self):
        """User units always run as whichever account owns the user
        systemd instance -- a User=/Group= directive would be
        meaningless there (and the shipped templates never had one to
        begin with; this locks that in)."""
        self._run()
        target = self.fake_home / '.config' / 'systemd' / 'user'
        for unit in ('anetbbs-web.service', 'anetbbs.service'):
            body = (target / unit).read_text()
            self.assertNotIn('User=', body)
            self.assertNotIn('Group=', body)

    def test_no_ambient_capabilities_granted(self):
        """User units can't bind privileged ports without extra
        systemd privilege delegation most distros don't enable by
        default -- deliberately not attempted here (see the function's
        own docstring)."""
        self._run()
        target = self.fake_home / '.config' / 'systemd' / 'user'
        body = (target / 'anetbbs-web.service').read_text()
        self.assertNotIn('AmbientCapabilities', body)

    def test_wanted_by_default_target_not_multi_user_target(self):
        self._run()
        target = self.fake_home / '.config' / 'systemd' / 'user'
        for unit in ('anetbbs-web.service', 'anetbbs.service'):
            body = (target / unit).read_text()
            self.assertIn('WantedBy=default.target', body)
            self.assertNotIn('multi-user.target', body)

    def test_subprocess_calls_use_systemctl_user_never_sudo(self):
        fake_run = self._run()
        for call in fake_run.call_args_list:
            args = call.args[0]
            self.assertNotIn('sudo', args)
            if args[0] == 'systemctl':
                self.assertIn('--user', args)

    def test_terminal_unit_enabled_only_when_a_protocol_is_active(self):
        fake_run = self._run(tel=False, ssh=False, rl=False)
        enable_calls = [c.args[0] for c in fake_run.call_args_list
                       if 'enable' in c.args[0]]
        enabled_units = {c[-1] for c in enable_calls}
        self.assertIn('anetbbs-web.service', enabled_units)
        self.assertNotIn('anetbbs.service', enabled_units)

    def test_terminal_unit_enabled_when_telnet_is_active(self):
        fake_run = self._run(tel=True, ssh=False, rl=False)
        enable_calls = [c.args[0] for c in fake_run.call_args_list
                       if 'enable' in c.args[0]]
        enabled_units = {c[-1] for c in enable_calls}
        self.assertIn('anetbbs.service', enabled_units)


class UserServiceTemplateContentTests(unittest.TestCase):
    """Static checks on the shipped templates themselves, independent
    of the installer code that consumes them."""

    def test_templates_exist(self):
        for unit in ('anetbbs-web.service', 'anetbbs.service'):
            self.assertTrue((REPO_ROOT / 'deploy' / 'user' / unit).exists())

    def test_neither_template_hardcodes_a_system_user(self):
        for unit in ('anetbbs-web.service', 'anetbbs.service'):
            body = (REPO_ROOT / 'deploy' / 'user' / unit).read_text()
            self.assertNotIn('User=anetbbs', body)
            self.assertNotIn('Group=anetbbs', body)

    def test_neither_template_references_etc_systemd(self):
        for unit in ('anetbbs-web.service', 'anetbbs.service'):
            body = (REPO_ROOT / 'deploy' / 'user' / unit).read_text()
            self.assertNotIn('/etc/systemd', body)


if __name__ == '__main__':
    unittest.main()
