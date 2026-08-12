"""Regression/coverage tests for the BinkP outbound spool directory.

Real gap found live: Jerry asked "where is the outbound directory for
binkp?" while about to start testing ANetCHESS (a separate InterBBS
chess door that writes real FTS-0001 .pkt netmail packets straight to
disk via its own `-ibbs out` command, the same way any traditional FTN
mailer's flat-file outbound spool works). ANetBBS's own echomail is
entirely DB-queue-driven (EchomailMessage rows for netmail/echomail,
HatchQueue rows for file distribution via TIC) -- there was no hook
anywhere for a loose file dropped by an external program to ever be
picked up and transmitted.

resolve_outbound_dir()/list_outbound_dir_files()/
archive_sent_outbound_file() (anetbbs/echomail/binkp.py) are the new
pure helpers; BinkPClient._send_outbound_dir_files() (binkp.py, the
dial-out direction used by both poller.py's _run_client and
_run_node_client) and _send_outbound_dir_items() (binkp_server.py, the
dial-in/listener direction) are the two delivery paths, mirroring the
existing HatchQueue delivery pattern but with no DB row at all -- a
file is sent exactly as found and archived to a `sent/` subfolder on
success, never deleted outright (same "keep it recoverable" convention
as tic.py's inbound processed/).

Keyed PER PEER (not one shared directory like BINKP_INBOUND_DIR)
because a loose file has no address of its own to route by, and more
than one EchomailNetwork/BinkPNode peer can be configured at once --
matches the same peer_address-keyed convention HatchQueue already
uses, and ANetCHESS's own netmail_config.h NetmailNode.outbound_dir
per-node design.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class SanitizePeerDirnameTests(unittest.TestCase):
    def test_ftn_address_slash_is_replaced(self):
        from anetbbs.echomail.binkp import _sanitize_peer_dirname
        result = _sanitize_peer_dirname('111:111/1')
        self.assertNotIn('/', result)

    def test_result_is_stable_and_deterministic(self):
        from anetbbs.echomail.binkp import _sanitize_peer_dirname
        self.assertEqual(_sanitize_peer_dirname('1200:1/1'),
                         _sanitize_peer_dirname('1200:1/1'))

    def test_point_address_dot_is_preserved_safely(self):
        from anetbbs.echomail.binkp import _sanitize_peer_dirname
        result = _sanitize_peer_dirname('1200:1/1.5')
        self.assertNotIn('/', result)
        # Must still resolve to a single path component (no traversal).
        self.assertNotIn(os.sep, result)

    def test_empty_or_garbage_falls_back_to_safe_default(self):
        from anetbbs.echomail.binkp import _sanitize_peer_dirname
        self.assertEqual(_sanitize_peer_dirname(''), 'unknown')
        self.assertEqual(_sanitize_peer_dirname('///...'), 'unknown')

    def test_different_addresses_do_not_collide(self):
        from anetbbs.echomail.binkp import _sanitize_peer_dirname
        a = _sanitize_peer_dirname('111:111/1')
        b = _sanitize_peer_dirname('111:111/2')
        self.assertNotEqual(a, b)


class ResolveOutboundDirTests(unittest.TestCase):
    def test_default_path_under_data_dir(self):
        from anetbbs.echomail.binkp import resolve_outbound_dir
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('BINKP_OUTBOUND_DIR', None)
            result = resolve_outbound_dir('data', '1200:1/1')
        self.assertTrue(result.startswith(os.path.join('data', 'binkp', 'outbound')))

    def test_env_override_takes_precedence(self):
        from anetbbs.echomail.binkp import resolve_outbound_dir
        with patch.dict(os.environ, {'BINKP_OUTBOUND_DIR': '/custom/root'}):
            result = resolve_outbound_dir('data', '1200:1/1')
        self.assertTrue(result.startswith('/custom/root'))

    def test_different_peers_get_different_directories(self):
        from anetbbs.echomail.binkp import resolve_outbound_dir
        os.environ.pop('BINKP_OUTBOUND_DIR', None)
        a = resolve_outbound_dir('data', '1200:1/1')
        b = resolve_outbound_dir('data', '1200:1/2')
        self.assertNotEqual(a, b)


class ListOutboundDirFilesTests(unittest.TestCase):
    def test_missing_directory_returns_empty(self):
        from anetbbs.echomail.binkp import list_outbound_dir_files
        self.assertEqual(list_outbound_dir_files('/nonexistent/path/xyz'), [])

    def test_lists_regular_files_only(self):
        from anetbbs.echomail.binkp import list_outbound_dir_files
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, 'a.pkt'), 'wb').close()
            open(os.path.join(tmpdir, 'b.pkt'), 'wb').close()
            os.makedirs(os.path.join(tmpdir, 'subdir'))
            names = [n for n, _ in list_outbound_dir_files(tmpdir)]
        self.assertEqual(sorted(names), ['a.pkt', 'b.pkt'])

    def test_sent_subfolder_itself_is_excluded(self):
        from anetbbs.echomail.binkp import list_outbound_dir_files
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, 'a.pkt'), 'wb').close()
            sent_dir = os.path.join(tmpdir, 'sent')
            os.makedirs(sent_dir)
            open(os.path.join(sent_dir, 'already-sent.pkt'), 'wb').close()
            names = [n for n, _ in list_outbound_dir_files(tmpdir)]
        self.assertEqual(names, ['a.pkt'])


class ArchiveSentOutboundFileTests(unittest.TestCase):
    def test_moves_file_into_sent_subfolder(self):
        from anetbbs.echomail.binkp import archive_sent_outbound_file
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'a.pkt')
            with open(path, 'wb') as f:
                f.write(b'data')
            archive_sent_outbound_file(tmpdir, 'a.pkt', path)
            self.assertFalse(os.path.exists(path))
            dst = os.path.join(tmpdir, 'sent', 'a.pkt')
            self.assertTrue(os.path.isfile(dst))
            with open(dst, 'rb') as f:
                self.assertEqual(f.read(), b'data')

    def test_existing_destination_removes_source_instead_of_failing(self):
        from anetbbs.echomail.binkp import archive_sent_outbound_file
        with tempfile.TemporaryDirectory() as tmpdir:
            sent_dir = os.path.join(tmpdir, 'sent')
            os.makedirs(sent_dir)
            with open(os.path.join(sent_dir, 'a.pkt'), 'wb') as f:
                f.write(b'already here')
            path = os.path.join(tmpdir, 'a.pkt')
            with open(path, 'wb') as f:
                f.write(b'new copy')
            archive_sent_outbound_file(tmpdir, 'a.pkt', path)
            self.assertFalse(os.path.exists(path))
            with open(os.path.join(sent_dir, 'a.pkt'), 'rb') as f:
                self.assertEqual(f.read(), b'already here')


class BinkPClientSendOutboundDirFilesTests(unittest.TestCase):
    def test_empty_directory_sends_nothing(self):
        from anetbbs.echomail.binkp import BinkPClient
        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1:1/2', password='')
        client._outbound_dir = '/nonexistent/xyz'
        sent, failures = client._send_outbound_dir_files()
        self.assertEqual(sent, 0)
        self.assertEqual(failures, [])

    def test_file_is_sent_and_archived_on_ack(self):
        from anetbbs.echomail.binkp import BinkPClient
        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1:1/2', password='')
        with tempfile.TemporaryDirectory() as tmpdir:
            client._outbound_dir = tmpdir
            path = os.path.join(tmpdir, 'game.pkt')
            with open(path, 'wb') as f:
                f.write(b'packet-bytes')

            with patch.object(client, '_send_cmd') as mock_cmd, \
                 patch.object(client, '_send_data') as mock_data, \
                 patch.object(client, '_wait_got', return_value=True):
                sent, failures = client._send_outbound_dir_files()

            self.assertEqual(sent, 1)
            self.assertEqual(failures, [])
            mock_cmd.assert_called_once()
            mock_data.assert_called_once_with(b'packet-bytes')
            self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, 'sent', 'game.pkt')))

    def test_unacked_file_is_reported_as_failure_and_left_in_place(self):
        from anetbbs.echomail.binkp import BinkPClient
        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1:1/2', password='')
        with tempfile.TemporaryDirectory() as tmpdir:
            client._outbound_dir = tmpdir
            path = os.path.join(tmpdir, 'game.pkt')
            with open(path, 'wb') as f:
                f.write(b'packet-bytes')

            with patch.object(client, '_send_cmd'), \
                 patch.object(client, '_send_data'), \
                 patch.object(client, '_wait_got', return_value=False):
                sent, failures = client._send_outbound_dir_files()

            self.assertEqual(sent, 0)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0][0], 'game.pkt')
            # Not archived and not deleted -- still there for the next attempt.
            self.assertTrue(os.path.isfile(path))

    def test_poll_resolves_outbound_dir_from_hub_address_and_calls_sender(self):
        """poll() must key the outbound dir off self.hub_address (the
        actual peer being dialed, whether that's an upstream hub via
        _run_client or a downstream node via _run_node_client -- both
        construct BinkPClient the same way) with no extra caller wiring."""
        from anetbbs.echomail.binkp import BinkPClient, _sanitize_peer_dirname
        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1200:1/99', password='')

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(client, '_connect'), \
                 patch.object(client, '_handshake'), \
                 patch.object(client, '_disconnect'), \
                 patch.object(client, '_receive_messages', return_value=[]), \
                 patch.object(client, '_send_outbound_dir_files',
                              return_value=(0, [])) as mock_sender:
                client.poll(data_dir=tmpdir)

            mock_sender.assert_called_once()
            expected_suffix = os.path.join(
                'binkp', 'outbound', _sanitize_peer_dirname('1200:1/99'))
            self.assertTrue(client._outbound_dir.endswith(expected_suffix))

    def test_poll_result_includes_outbound_dir_keys(self):
        from anetbbs.echomail.binkp import BinkPClient
        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1200:1/99', password='')
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(client, '_connect'), \
                 patch.object(client, '_handshake'), \
                 patch.object(client, '_disconnect'), \
                 patch.object(client, '_receive_messages', return_value=[]), \
                 patch.object(client, '_send_outbound_dir_files',
                              return_value=(3, [('x.pkt', 'boom')])):
                result = client.poll(data_dir=tmpdir)

        self.assertEqual(result['outbound_dir_sent'], 3)
        self.assertEqual(result['outbound_dir_failures'], [('x.pkt', 'boom')])


if __name__ == '__main__':
    unittest.main()
