"""Regression test for a real Critical finding from a security/
performance audit (2026-08-31): anetbbs/web/finger.py's _do_finger()
connected to any logged-in user's supplied host:port with NO SSRF
guard at all -- socket.create_connection((host, port), ...) straight
from request.form, no validation whatsoever. Any authenticated user
(not an admin-only feature) could point it at internal infrastructure
(a database port on localhost, another container on the same Docker
network, the 169.254.169.254 cloud metadata endpoint) and read back up
to 64KB of the raw response -- a full authenticated SSRF + internal-
port-scanning primitive.

Fixed using the same shared guard (core.net_safety.resolve_safe_
destination) already used by web_terminal.py's outbound telnet client
and RSS's feed-URL fetches -- see tests/test_net_safety.py for that
helper's own coverage. This file only verifies finger.py's own call
site actually uses it and never reaches socket.connect() for a
rejected destination.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.web.finger import _do_finger


class FingerSsrfGuardTests(unittest.TestCase):
    def test_private_address_is_rejected_without_ever_connecting(self):
        with mock.patch('socket.socket') as mock_socket_cls:
            result = _do_finger('192.168.1.1', user='root', port=80)
        self.assertIn('not allowed', result)
        mock_socket_cls.assert_not_called()

    def test_loopback_is_rejected_without_ever_connecting(self):
        with mock.patch('socket.socket') as mock_socket_cls:
            result = _do_finger('127.0.0.1', user='root', port=5432)
        self.assertIn('not allowed', result)
        mock_socket_cls.assert_not_called()

    def test_cloud_metadata_address_is_rejected_without_ever_connecting(self):
        with mock.patch('socket.socket') as mock_socket_cls:
            result = _do_finger('169.254.169.254', user='', port=80)
        self.assertIn('not allowed', result)
        mock_socket_cls.assert_not_called()

    def test_public_address_reaches_a_real_connect_attempt(self):
        """Confirms the fix doesn't just reject everything -- a real
        public destination still gets a real connect() call, using the
        resolved sockaddr (not re-resolving host at connect time,
        which would reopen the DNS-rebinding gap the guard exists to
        close)."""
        fake_sock = mock.MagicMock()
        fake_sock.recv.return_value = b''
        with mock.patch('socket.socket', return_value=fake_sock) as mock_socket_cls:
            _do_finger('8.8.8.8', user='someone', port=79)
        mock_socket_cls.assert_called_once()
        fake_sock.connect.assert_called_once()
        connected_addr = fake_sock.connect.call_args[0][0]
        self.assertEqual(connected_addr[0], '8.8.8.8')
        self.assertEqual(connected_addr[1], 79)


if __name__ == '__main__':
    unittest.main()
