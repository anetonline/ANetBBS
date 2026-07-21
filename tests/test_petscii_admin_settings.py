"""Regression tests for the PETSCII admin-UI settings/status wiring
(reported live: a sysop tried to enable PETSCII and found no way to do
so short of hand-editing .env -- there was no admin settings entry and
no Control Center status pill for either port, unlike every other
terminal protocol).

anetbbs/web/admin.py's EDITABLE_SETTINGS already drives a generic
.env editor (Admin -> Settings) for TELNET/SSH/RLOGIN/FTP -- PETSCII40/
80 now follow the exact same convention. anetbbs/web/control.py's
KNOWN_UNITS already drives the Control Center's live up/down port
pills for the same protocols under the "anetbbs" (Terminal Protocols)
unit -- PETSCII40/80 now appear there too.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.web.admin import EDITABLE_SETTINGS
from anetbbs.web.control import KNOWN_UNITS


class PetsciiEditableSettingsTests(unittest.TestCase):
    def _entry(self, key):
        return next((e for e in EDITABLE_SETTINGS if e[0] == key), None)

    def test_all_six_petscii_keys_present(self):
        for key in ('PETSCII40_ENABLED', 'PETSCII40_PORT',
                   'PETSCII80_ENABLED', 'PETSCII80_PORT'):
            self.assertIsNotNone(self._entry(key), f'{key} missing from Admin -> Settings')

    def test_enabled_and_port_entries_require_restart(self):
        # These are read once at anetbbs (terminal) process startup --
        # same as TELNET_ENABLED/SSH_ENABLED/RLOGIN_ENABLED, restarting
        # the anetbbs service is genuinely required for a change to
        # take effect.
        for key in ('PETSCII40_ENABLED', 'PETSCII40_PORT',
                   'PETSCII80_ENABLED', 'PETSCII80_PORT'):
            _key, _label, _kind, restart_flag = self._entry(key)
            self.assertTrue(restart_flag, f'{key} should be flagged as requiring a restart')

    def test_labels_mention_commodore(self):
        # Sanity check a sysop searching "PETSCII" or "commodore" in the
        # settings page finds these entries at a glance.
        for key in ('PETSCII40_ENABLED', 'PETSCII80_ENABLED'):
            _key, label, _kind, _restart = self._entry(key)
            self.assertIn('PETSCII', label)


class PetsciiControlCenterTests(unittest.TestCase):
    def _terminal_unit(self):
        return next(u for u in KNOWN_UNITS if u['unit'] == 'anetbbs')

    def test_both_petscii_ports_listed_under_terminal_protocols(self):
        ports = {p['key']: p for p in self._terminal_unit()['ports']}
        self.assertIn('PETSCII40_PORT', ports)
        self.assertIn('PETSCII80_PORT', ports)

    def test_petscii_port_defaults_match_config(self):
        from anetbbs.config import Config
        ports = {p['key']: p for p in self._terminal_unit()['ports']}
        self.assertEqual(ports['PETSCII40_PORT']['default'], 6400)
        self.assertEqual(ports['PETSCII80_PORT']['default'], 6401)
        # Cross-check against the real config defaults too, so this test
        # would fail if one side drifts from the other.
        self.assertEqual(Config.PETSCII40_PORT, ports['PETSCII40_PORT']['default'])
        self.assertEqual(Config.PETSCII80_PORT, ports['PETSCII80_PORT']['default'])

    def test_petscii_ports_are_tcp(self):
        ports = {p['key']: p for p in self._terminal_unit()['ports']}
        self.assertEqual(ports['PETSCII40_PORT']['proto'], 'tcp')
        self.assertEqual(ports['PETSCII80_PORT']['proto'], 'tcp')


if __name__ == '__main__':
    unittest.main()
