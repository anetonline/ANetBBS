"""Regression test for ECHOMAIL_ORIGIN_LINE's default value --
requested live: a plain BBS name isn't as useful in a real FTN origin
line as the common sysop convention "<hostname>, Telnet:N SSH:N
HTTP:N" (e.g. "JoesBBS.Com, Telnet:23 SSH:22 HTTP:80"), which tells a
peer reading the message how to actually reach the BBS. The FTN
address itself is deliberately NOT part of this string -- see
anetbbs/echomail/binkp.py's _build_ftn_packet, which appends it
separately per network (test_binkp_origin_line_address.py covers
that half).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.config import _default_echomail_origin


class DefaultEchomailOriginTests(unittest.TestCase):
    def test_uses_bbs_domain_when_set(self):
        with patch.dict(os.environ, {'BBS_DOMAIN': 'joesbbs.com',
                                     'BBS_NAME': 'JoesBBS'}, clear=False):
            origin = _default_echomail_origin()
        self.assertTrue(origin.startswith('joesbbs.com,'))

    def test_falls_back_to_bbs_name_without_a_domain(self):
        env = os.environ.copy()
        env.pop('BBS_DOMAIN', None)
        env['BBS_NAME'] = 'JoesBBS'
        with patch.dict(os.environ, env, clear=True):
            origin = _default_echomail_origin()
        self.assertTrue(origin.startswith('JoesBBS,'))

    def test_includes_configured_ports(self):
        env = os.environ.copy()
        env.update({'BBS_DOMAIN': 'joesbbs.com', 'TELNET_PORT': '23',
                    'SSH_PORT': '22', 'WEB_PORT': '80',
                    'TELNET_ENABLED': 'true', 'SSH_ENABLED': 'true'})
        with patch.dict(os.environ, env, clear=False):
            origin = _default_echomail_origin()
        self.assertEqual(origin, 'joesbbs.com, Telnet:23 SSH:22 HTTP:80')

    def test_omits_disabled_protocols(self):
        env = os.environ.copy()
        env.update({'BBS_DOMAIN': 'joesbbs.com', 'TELNET_ENABLED': 'false',
                    'SSH_ENABLED': 'false', 'WEB_PORT': '80'})
        with patch.dict(os.environ, env, clear=False):
            origin = _default_echomail_origin()
        self.assertEqual(origin, 'joesbbs.com, HTTP:80')
        self.assertNotIn('Telnet', origin)
        self.assertNotIn('SSH', origin)

    def test_does_not_include_an_ftn_address(self):
        """The address is appended separately, per network, at
        packet-build time -- this default must never bake one in."""
        origin = _default_echomail_origin()
        self.assertNotIn('(', origin)
        self.assertNotIn(')', origin)


if __name__ == '__main__':
    unittest.main()
