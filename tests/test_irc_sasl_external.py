"""Regression test for a real Low finding from a security/performance
audit (2026-09-02): anetbbs/web/irc_web.py's _IrcSession already fully
supported SASL EXTERNAL (TLS client-cert / CertFP) authentication --
connect()'s own ctx.load_cert_chain() call -- but nothing ever wired a
way to actually reach it: no UI field, no socketio payload handling.
Deliberately wired up rather than deleted, since the underlying
capability was real and working.

Security-relevant design point: the client never supplies a server-
side FILE PATH for its certificate (that would let any logged-in user
probe/reference arbitrary files readable by the anetbbs process) --
only PEM TEXT, which the server writes to a fresh, per-connection temp
dir it names itself, mode 0600, cleaned up on every session-teardown
path (explicit disconnect, socket.io disconnect event, and the natural
end-of-connection path in _IrcSession.run()'s own finally block).
"""
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.web.irc_web import (
    _IrcSession, _validate_client_cert_request, _prepare_client_cert,
    _sessions, _scrollback,
)


_FAKE_CERT = '-----BEGIN CERTIFICATE-----\nfakecertdata\n-----END CERTIFICATE-----'
_FAKE_KEY = '-----BEGIN PRIVATE KEY-----\nfakekeydata\n-----END PRIVATE KEY-----'


class ValidateClientCertRequestTests(unittest.TestCase):
    def test_missing_key_is_rejected(self):
        err = _validate_client_cert_request(_FAKE_CERT, '', True)
        self.assertIsNotNone(err)
        self.assertIn('both', err.lower())

    def test_missing_cert_is_rejected(self):
        err = _validate_client_cert_request('', _FAKE_KEY, True)
        self.assertIsNotNone(err)

    def test_without_ssl_is_rejected(self):
        err = _validate_client_cert_request(_FAKE_CERT, _FAKE_KEY, False)
        self.assertIsNotNone(err)
        self.assertIn('ssl', err.lower())

    def test_oversized_pem_is_rejected(self):
        err = _validate_client_cert_request('x' * 20000, _FAKE_KEY, True)
        self.assertIsNotNone(err)
        self.assertIn('large', err.lower())

    def test_valid_request_passes(self):
        err = _validate_client_cert_request(_FAKE_CERT, _FAKE_KEY, True)
        self.assertIsNone(err)


class PrepareClientCertTests(unittest.TestCase):
    def test_writes_cert_and_key_with_correct_content_and_permissions(self):
        temp_dir, cert_path, key_path = _prepare_client_cert(_FAKE_CERT, _FAKE_KEY)
        try:
            self.assertTrue(os.path.isdir(temp_dir))
            self.assertEqual(Path(cert_path).read_text().strip(), _FAKE_CERT)
            self.assertEqual(Path(key_path).read_text().strip(), _FAKE_KEY)
            # mode 0600 -- owner read/write only, no group/other access,
            # since this holds real private key material.
            cert_mode = stat.S_IMODE(os.stat(cert_path).st_mode)
            key_mode = stat.S_IMODE(os.stat(key_path).st_mode)
            self.assertEqual(cert_mode, 0o600)
            self.assertEqual(key_mode, 0o600)
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_never_accepts_a_path_only_ever_writes_pem_text(self):
        """Documents the actual security property: passing something
        that LOOKS like a path is just treated as literal PEM text
        content (written verbatim to a file this function names
        itself), never opened/read as a path -- there is no code path
        here that could ever reference an arbitrary existing file."""
        temp_dir, cert_path, key_path = _prepare_client_cert(
            '/etc/passwd', '/etc/shadow')
        try:
            self.assertEqual(Path(cert_path).read_text().strip(), '/etc/passwd')
            self.assertNotEqual(cert_path, '/etc/passwd')
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class TempCertCleanupTests(unittest.TestCase):
    def setUp(self):
        _sessions.clear()
        _scrollback.clear()
        self.addCleanup(_sessions.clear)
        self.addCleanup(_scrollback.clear)

    def _make_session_with_cert(self):
        temp_dir, cert_path, key_path = _prepare_client_cert(_FAKE_CERT, _FAKE_KEY)
        sess = _IrcSession(
            sid='sid-cert', server='irc.example.com', port=6697,
            use_ssl=True, nick='tester', username='tester', realname='Tester',
            sasl_mechanism='EXTERNAL', client_cert_path=cert_path,
            client_key_path=key_path)
        sess._temp_cert_dir = temp_dir
        return sess, temp_dir

    def test_quit_removes_the_temp_cert_dir(self):
        sess, temp_dir = self._make_session_with_cert()
        self.assertTrue(os.path.isdir(temp_dir))
        sess.quit()
        self.assertFalse(os.path.exists(temp_dir),
                         'quit() must clean up the temp cert/key dir')

    def test_natural_disconnect_in_run_also_removes_the_temp_cert_dir(self):
        """The finally: block in run() (server closed the connection,
        etc.) is a SEPARATE teardown path from quit() -- must also
        clean up, not just the explicit-quit path."""
        class _FakeEOFSocket:
            def recv(self, n):
                return b''
            def close(self):
                pass

        sess, temp_dir = self._make_session_with_cert()
        sess.sock = _FakeEOFSocket()
        sess.connected = True
        with patch.object(_IrcSession, '_emit'):
            sess.run()
        self.assertFalse(os.path.exists(temp_dir),
                         'the natural end-of-connection path must also clean '
                         'up the temp cert/key dir')

    def test_no_temp_dir_is_a_safe_no_op(self):
        """A plain PLAIN-auth session (no cert ever written) must not
        error out when torn down."""
        sess = _IrcSession(sid='sid-plain', server='irc.example.com',
                           port=6667, use_ssl=False, nick='t', username='t',
                           realname='T')
        sess.quit()  # must not raise


if __name__ == '__main__':
    unittest.main()
