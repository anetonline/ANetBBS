"""Tests for the BinkP session transcript feature, built after a sysop
complained: "would like to see full logs of a failure session for
binkp for a better idea of what is breaking."

Two real gaps this closes:
  1. anetbbs/echomail/poller.py's generic exception handler stored
     only str(exc), which can be empty/unhelpful for some exception
     types (bare socket.timeout, some ConnectionErrors).
  2. anetbbs/echomail/binkp.py had zero frame-level logging anywhere
     -- _send_cmd()/_send_data() logged nothing at all, received
     frames were only logged sporadically during the handshake.

Fix: capture a timestamped, frame-by-frame transcript into a
caller-owned list (so it survives even if poll() raises partway
through, since the BinkPClient instance itself goes out of scope with
the exception but the list -- owned by poller.py's _do_poll(), not the
client -- does not), store it on EchomailPollLog.transcript, and show
it from a new admin route.
"""
import os
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BinkPTranscriptCaptureTests(unittest.TestCase):
    """Pure binkp.py tests -- no Flask app needed, no real network
    beyond a local loopback fake server."""

    def test_log_transcript_appends_timestamped_lines(self):
        from anetbbs.echomail.binkp import BinkPClient
        transcript = []
        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1:1/0', transcript=transcript)
        client._log_transcript('TEST LINE')
        self.assertEqual(len(transcript), 1)
        # HH:MM:SS.mmm prefix, then the line content.
        self.assertRegex(transcript[0], r'^\d{2}:\d{2}:\d{2}\.\d{3} TEST LINE$')

    def test_send_cmd_logs_direction_and_command_name(self):
        from anetbbs.echomail.binkp import BinkPClient, CMD_ADR
        transcript = []
        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1:1/0', transcript=transcript)
        client._sock = _FakeSocketSendOnly()
        client._send_cmd(CMD_ADR, '1:1/1')
        self.assertEqual(len(transcript), 1)
        self.assertIn('>> CMD ADR: 1:1/1', transcript[0])

    def test_send_data_logs_byte_count_not_raw_bytes(self):
        from anetbbs.echomail.binkp import BinkPClient
        transcript = []
        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1:1/0', transcript=transcript)
        client._sock = _FakeSocketSendOnly()
        client._send_data(b'x' * 500)
        self.assertEqual(len(transcript), 1)
        self.assertIn('>> DATA: 500 bytes', transcript[0])
        self.assertNotIn('x' * 500, transcript[0])  # never dump raw bytes

    def test_full_session_transcript_over_real_socket(self):
        """End-to-end: real loopback TCP connection, fake peer sends an
        M_ERR frame, confirms full send+receive+disconnect sequence is
        captured correctly and readably."""
        from anetbbs.echomail.binkp import BinkPClient, CMD_ERR

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', 0))
        port = srv.getsockname()[1]
        srv.listen(1)

        def fake_peer():
            conn, _ = srv.accept()
            time.sleep(0.1)
            body = bytes([CMD_ERR]) + b'simulated peer error'
            header = struct.pack('>H', 0x8000 | len(body))
            conn.sendall(header + body)
            time.sleep(0.1)
            conn.close()

        t = threading.Thread(target=fake_peer, daemon=True)
        t.start()

        transcript = []
        client = BinkPClient(host='127.0.0.1', port=port, our_address='1:1/1',
                            hub_address='1:1/0', password='x', timeout=3,
                            transcript=transcript)
        with self.assertRaises(Exception):
            client.poll()
        t.join(timeout=2)

        joined = '\n'.join(transcript)
        self.assertIn('CONNECT 127.0.0.1', joined)
        self.assertIn('CONNECTED', joined)
        self.assertIn('>> CMD ADR: 1:1/1', joined)
        self.assertIn('<< CMD ERR: simulated peer error', joined)
        self.assertIn('DISCONNECT', joined)
        # Sent lines before received lines before disconnect, in order.
        connect_idx = joined.index('CONNECTED')
        adr_idx = joined.index('>> CMD ADR')
        err_idx = joined.index('<< CMD ERR')
        disconnect_idx = joined.index('DISCONNECT')
        self.assertTrue(connect_idx < adr_idx < err_idx < disconnect_idx)

    def test_transcript_survives_connection_failure(self):
        """The core design property: even when poll() raises before a
        socket is ever established, the caller-owned list still holds
        whatever was captured -- proving the transcript isn't lost with
        the BinkPClient instance when the exception propagates."""
        from anetbbs.echomail.binkp import BinkPClient
        transcript = []
        client = BinkPClient(host='127.0.0.1', port=1, our_address='1:1/1',
                            hub_address='1:1/0', password='x', timeout=1,
                            transcript=transcript)
        with self.assertRaises(Exception):
            client.poll()
        self.assertTrue(any('CONNECT 127.0.0.1:1' in line for line in transcript))


class _FakeSocketSendOnly:
    def sendall(self, data):
        pass


class TranscriptFormattingTests(unittest.TestCase):
    """anetbbs/echomail/poller.py's _format_transcript() truncation logic."""

    def test_short_transcript_unmodified(self):
        from anetbbs.echomail.poller import _format_transcript
        lines = ['line one', 'line two', 'line three']
        result = _format_transcript(lines)
        self.assertEqual(result, 'line one\nline two\nline three')

    def test_line_count_truncation(self):
        from anetbbs.echomail.poller import _format_transcript, _TRANSCRIPT_MAX_LINES
        lines = [f'line {i}' for i in range(_TRANSCRIPT_MAX_LINES + 50)]
        result = _format_transcript(lines)
        self.assertIn('truncated', result)
        self.assertIn('50 earlier lines omitted', result)
        # Keeps the LAST lines (most useful context near a failure).
        self.assertIn(f'line {_TRANSCRIPT_MAX_LINES + 49}', result)
        self.assertNotIn('line 0\n', result)

    def test_char_count_truncation(self):
        from anetbbs.echomail.poller import _format_transcript, _TRANSCRIPT_MAX_CHARS
        # Few lines, but each one huge -- trips the char cap without
        # tripping the line-count cap.
        lines = ['x' * 60_000, 'y' * 60_000]
        result = _format_transcript(lines)
        self.assertIn('truncated', result)
        self.assertLessEqual(len(result), _TRANSCRIPT_MAX_CHARS + 200)


class PollerErrorMessageTests(unittest.TestCase):
    """anetbbs/echomail/poller.py's _do_poll() exception handling --
    confirms the type(exc).__name__ fix and transcript persistence,
    against a real Flask app + DB."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.binkp_transcript_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_error_message_includes_exception_type_even_when_str_is_empty(self):
        """A bare exception whose str() is empty (e.g. TimeoutError())
        used to leave log.error_message blank -- now the type name is
        always present."""
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.poller import _do_poll

        with self.app.app_context():
            net = EchomailNetwork(
                name='TranscriptErrTestNet', network_type='binkp',
                binkp_host='127.0.0.1', binkp_port=1,  # nothing listening
                our_address='1:1/1', hub_address='1:1/2',  # NOT self-referential
                is_active=True)
            db.session.add(net)
            db.session.commit()

            # _do_poll() logs then re-raises (existing behavior, so the
            # scheduler loop that calls it can decide how to handle a
            # failure at a higher level) -- the assertion is about what
            # gets logged before that re-raise, not whether it raises.
            with self.assertRaises(Exception):
                _do_poll(self.app, net)

            from anetbbs.models import EchomailPollLog
            log = (EchomailPollLog.query.filter_by(network_id=net.id)
                   .order_by(EchomailPollLog.id.desc()).first())
            self.assertEqual(log.status, 'error')
            self.assertIsNotNone(log.error_message)
            self.assertNotEqual(log.error_message.strip(), '')
            # Exception type name must always be present.
            self.assertRegex(log.error_message, r'^\w*Error')

    def test_transcript_saved_on_failed_poll(self):
        from anetbbs.models import db, EchomailNetwork, EchomailPollLog
        from anetbbs.echomail.poller import _do_poll

        with self.app.app_context():
            net = EchomailNetwork(
                name='TranscriptSaveTestNet', network_type='binkp',
                binkp_host='127.0.0.1', binkp_port=1,
                our_address='1:1/3', hub_address='1:1/4',
                is_active=True)
            db.session.add(net)
            db.session.commit()

            with self.assertRaises(Exception):
                _do_poll(self.app, net)

            log = (EchomailPollLog.query.filter_by(network_id=net.id)
                   .order_by(EchomailPollLog.id.desc()).first())
            self.assertIsNotNone(log.transcript)
            self.assertIn('CONNECT 127.0.0.1:1', log.transcript)


class TranscriptAdminRouteTests(unittest.TestCase):
    """GET /admin/echomail/logs/<id>/transcript."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.binkp_transcript_route_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_log(self, transcript=None):
        from anetbbs.models import db, EchomailNetwork, EchomailPollLog
        with self.app.app_context():
            net = EchomailNetwork.query.filter_by(name='RouteTestNet').first()
            if not net:
                net = EchomailNetwork(name='RouteTestNet', network_type='binkp')
                db.session.add(net)
                db.session.commit()
            log = EchomailPollLog(network_id=net.id, status='error',
                                  transcript=transcript)
            db.session.add(log)
            db.session.commit()
            return log.id

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username='transcriptrouteadmin').first()
            if not u:
                u = User(username='transcriptrouteadmin', is_admin=True,
                        email='transcriptrouteadmin@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def test_requires_login(self):
        log_id = self._make_log(transcript='some content')
        client = self.app.test_client()  # not logged in
        resp = client.get(f'/admin/echomail/logs/{log_id}/transcript')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_404_when_no_transcript(self):
        log_id = self._make_log(transcript=None)
        client = self._admin_client()
        resp = client.get(f'/admin/echomail/logs/{log_id}/transcript')
        self.assertEqual(resp.status_code, 404)

    def test_renders_transcript_content_when_present(self):
        log_id = self._make_log(transcript='12:00:00.000 CONNECT test:24554')
        client = self._admin_client()
        resp = client.get(f'/admin/echomail/logs/{log_id}/transcript')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('CONNECT test:24554', resp.get_data(as_text=True))

    def test_logs_page_shows_transcript_link_only_when_present(self):
        with_id = self._make_log(transcript='has content')
        without_id = self._make_log(transcript=None)
        client = self._admin_client()
        resp = client.get('/admin/echomail/logs')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn(f'/admin/echomail/logs/{with_id}/transcript', body)
        self.assertNotIn(f'/admin/echomail/logs/{without_id}/transcript', body)


if __name__ == '__main__':
    unittest.main()
