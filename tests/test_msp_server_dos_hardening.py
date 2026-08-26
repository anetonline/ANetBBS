"""Regression tests for the three inbound-MSP DoS/flood findings from
the 2026-08-26 MSP security audit (anetbbs/msp/server.py) -- unlike
systat.py's UDP responder (already rate-limited after an earlier
audit), the TCP listener had NO cap on inbound message volume,
concurrent connections, or total connection lifetime, despite
accepting connections from any unauthenticated remote host.

Uses real sockets against a real (but ephemeral-port, isolated-DB)
running instance of the actual listener -- the same pattern this repo
already uses for MSP loopback testing (tests/msp_loopback_check.py),
just wrapped as a real pytest/unittest test instead of a manual
diagnostic script.
"""
import os
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod

TEST_MSP_PORT = 13918


def _raw_msp_packet(recipient='nobody', sender='tester', message='hi',
                    msg_type='S'):
    """Build a minimal valid RFC 1312 MSP packet: NUL-separated fields,
    matching anetbbs/msp/protocol.py's decode() expectations closely
    enough to parse successfully (msg_type, recipient, sender,
    sender_terminal, message, cookie -- 7 NULs total per NUM_FIELDS)."""
    from anetbbs.msp.protocol import encode
    return encode(recipient=recipient, sender=sender, message=message,
                 sender_terminal='', cookie='', msg_type=msg_type)


class MspServerDosHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.msp_server_dos_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['MSP_ENABLED'] = True
        cls.app.config['MSP_PORT'] = TEST_MSP_PORT
        cls.app.config['MSP_BIND_HOST'] = '127.0.0.1'
        with cls.app.app_context():
            from anetbbs.models import db
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    @staticmethod
    def _wait_until_stopped(msp_server, deadline=5.0):
        """stop_msp_server() only SIGNALS a stop -- the accept loop
        polls its stop socket.timeout(1.0) accept(), so it can take up
        to ~1s to actually notice and exit. _server_thread/_listen_sock
        are bare module globals shared across every test in this file
        (real sockets, no per-test isolation), so a fixed sleep() is
        not reliable -- actively wait for the thread to actually die
        before either starting a new one or ending the test, or a
        later test can silently connect to a STALE listener still
        running with a PREVIOUS test's patched constants."""
        start = time.monotonic()
        while (msp_server._server_thread is not None
              and msp_server._server_thread.is_alive()):
            if time.monotonic() - start > deadline:
                raise AssertionError('MSP server thread did not stop in time')
            time.sleep(0.05)

    @staticmethod
    def _wait_until_listening(msp_server, deadline=5.0):
        """`_listen_sock` is a module global that's never reset to None
        on stop -- after the first test in this file, it always
        points at SOME socket object (possibly a previous test's
        already-closed one), so "is it None" only distinguishes the
        very first call. Wait for a live server thread instead, then
        give the bind()/listen() syscalls inside it a brief moment
        (near-instant in practice) to actually complete."""
        start = time.monotonic()
        while not (msp_server._server_thread is not None
                  and msp_server._server_thread.is_alive()):
            if time.monotonic() - start > deadline:
                raise AssertionError('MSP server thread did not start in time')
            time.sleep(0.05)
        time.sleep(0.2)

    def setUp(self):
        # Clean rate-limit state so tests don't bleed into each other
        # (the limiter's bucket store is a module-level dict keyed by
        # e.g. "msp-inbound:127.0.0.1", which every test in this file
        # will hit since real sockets all originate from loopback).
        from anetbbs.features import rate_limit as rl
        rl._buckets.clear()
        from anetbbs.msp import server as msp_server
        msp_server.stop_msp_server()
        self._wait_until_stopped(msp_server)

    def tearDown(self):
        from anetbbs.msp import server as msp_server
        msp_server.stop_msp_server()
        self._wait_until_stopped(msp_server)

    def _connect_and_send(self, payload, timeout=3.0, read_reply=True):
        with socket.create_connection(('127.0.0.1', TEST_MSP_PORT), timeout=timeout) as s:
            s.sendall(payload)
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            if read_reply:
                try:
                    return s.recv(256)
                except socket.timeout:
                    return b''
            return b''

    # -- Finding: no rate limit on inbound delivery ----------------------

    def test_excess_messages_from_one_source_get_rate_limited(self):
        from anetbbs.msp import server as msp_server
        with patch.object(msp_server, '_MSP_PER_IP_LIMIT', 3), \
             patch.object(msp_server, '_MSP_PER_IP_WINDOW', 60), \
             patch.object(msp_server, '_MSP_GLOBAL_LIMIT', 1000):
            msp_server.start_msp_server(self.app)
            self._wait_until_listening(msp_server)

            replies = []
            for i in range(6):
                packet = _raw_msp_packet(message=f'msg{i}', msg_type='A')
                reply = self._connect_and_send(packet)
                replies.append(reply)

            rate_limited = [r for r in replies if b'rate-limited' in r]
            self.assertGreaterEqual(
                len(rate_limited), 1,
                f'expected at least one -rate-limited reply among {replies}')
            # And the first few (within the limit) must NOT have been
            # rate-limited -- confirms this is a real limit, not
            # everything just failing.
            self.assertNotIn(b'rate-limited', replies[0])

    # -- Finding: unbounded concurrent connections ------------------------

    def test_concurrency_cap_refuses_connections_beyond_the_limit(self):
        from anetbbs.msp import server as msp_server
        import threading
        small_sem = threading.Semaphore(2)
        with patch.object(msp_server, '_concurrency_sem', small_sem):
            msp_server.start_msp_server(self.app)
            self._wait_until_listening(msp_server)

            # Open connections that send nothing (hold the handler in
            # its read loop) so they occupy semaphore slots.
            holders = []
            for _ in range(2):
                s = socket.create_connection(('127.0.0.1', TEST_MSP_PORT), timeout=3.0)
                holders.append(s)
            time.sleep(0.3)  # let the accept loop actually acquire both slots

            # A 3rd connection should be refused (server closes it
            # immediately without ever spawning a handler thread).
            extra = socket.create_connection(('127.0.0.1', TEST_MSP_PORT), timeout=3.0)
            extra.settimeout(2.0)
            data = extra.recv(16)
            self.assertEqual(data, b'', 'over-the-cap connection should be closed immediately')

            for s in holders:
                s.close()
            extra.close()

    # -- Finding: no absolute deadline (slow-trickle DoS) -----------------

    def test_absolute_deadline_closes_a_slow_trickle_connection(self):
        from anetbbs.msp import server as msp_server
        with patch.object(msp_server, '_MSP_ABSOLUTE_DEADLINE_SEC', 1):
            msp_server.start_msp_server(self.app)
            self._wait_until_listening(msp_server)

            s = socket.create_connection(('127.0.0.1', TEST_MSP_PORT), timeout=5.0)
            s.settimeout(5.0)
            started = time.monotonic()
            try:
                # Trickle one byte every 0.3s, well inside the 20s idle
                # timeout on each individual recv, but past the 1s
                # absolute deadline in aggregate.
                for _ in range(10):
                    try:
                        s.sendall(b'S')
                    except OSError:
                        break
                    time.sleep(0.3)
                    # If the server closed the connection, a subsequent
                    # send will eventually raise (broken pipe/reset) or
                    # a recv returns EOF -- check for either.
                    s.settimeout(0.1)
                    try:
                        d = s.recv(16)
                        if d == b'':
                            break
                    except socket.timeout:
                        pass
                    s.settimeout(5.0)
            finally:
                elapsed = time.monotonic() - started
                s.close()
            self.assertLess(
                elapsed, 10.0,
                'connection should have been closed at the 1s absolute '
                'deadline, not allowed to run for the full trickle duration')


if __name__ == '__main__':
    unittest.main()
