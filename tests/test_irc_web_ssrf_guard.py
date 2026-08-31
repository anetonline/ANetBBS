"""Regression test for a real High-severity finding from a security/
performance audit (2026-08-31): web/irc_web.py's _IrcSession.connect()
had NO SSRF guard at all -- socket.create_connection((self.server,
self.port), ...) straight from user-supplied "IRC server"/"port"
fields (the irc_connect socketio event, reachable by any logged-in
user, not just admins). A malicious value could point it at internal
infrastructure and read back banner/response data via the
irc_error/irc_raw/irc_system events -- a semi-blind SSRF + internal
port-scan oracle.

Fixed using the same shared guard (core.net_safety.resolve_safe_
destination) already used by web_terminal.py's outbound telnet client
and (this same audit round) web/finger.py -- see
tests/test_net_safety.py for that helper's own coverage. This file
only verifies irc_web.py's own call site actually uses it and never
reaches socket.connect() for a rejected destination.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web.irc_web import _IrcSession


def _bare_session(server, port):
    sess = object.__new__(_IrcSession)
    sess.sid = 'test-sid'
    sess.user_id = None
    sess.server = server
    sess.port = port
    sess.use_ssl = False
    sess.nick = 'tester'
    sess.username = 'tester'
    sess.realname = 'Test User'
    sess.sock = None
    sess.sasl_state = None
    sess.sasl_mechanism = 'PLAIN'
    sess.client_cert_path = None
    sess.client_key_path = None
    return sess


class IrcWebSsrfGuardTests(unittest.TestCase):
    def test_private_address_is_rejected_without_ever_connecting(self):
        sess = _bare_session('192.168.1.1', 6667)
        with mock.patch('socket.socket') as mock_socket_cls, \
             mock.patch.object(_IrcSession, '_emit') as mock_emit:
            result = sess.connect()
        self.assertFalse(result)
        mock_socket_cls.assert_not_called()
        self.assertTrue(mock_emit.called)
        self.assertIn('not allowed', mock_emit.call_args[0][1]['message'])

    def test_loopback_is_rejected_without_ever_connecting(self):
        sess = _bare_session('127.0.0.1', 5432)
        with mock.patch('socket.socket') as mock_socket_cls, \
             mock.patch.object(_IrcSession, '_emit'):
            result = sess.connect()
        self.assertFalse(result)
        mock_socket_cls.assert_not_called()

    def test_cloud_metadata_address_is_rejected_without_ever_connecting(self):
        sess = _bare_session('169.254.169.254', 80)
        with mock.patch('socket.socket') as mock_socket_cls, \
             mock.patch.object(_IrcSession, '_emit'):
            result = sess.connect()
        self.assertFalse(result)
        mock_socket_cls.assert_not_called()

    def test_public_address_reaches_a_real_connect_attempt(self):
        fake_sock = mock.MagicMock()
        sess = _bare_session('8.8.8.8', 6667)
        with mock.patch('socket.socket', return_value=fake_sock) as mock_socket_cls, \
             mock.patch.object(_IrcSession, '_emit'):
            sess.connect()
        mock_socket_cls.assert_called_once()
        fake_sock.connect.assert_called_once()
        connected_addr = fake_sock.connect.call_args[0][0]
        self.assertEqual(connected_addr[0], '8.8.8.8')
        self.assertEqual(connected_addr[1], 6667)


if __name__ == '__main__':
    unittest.main()
