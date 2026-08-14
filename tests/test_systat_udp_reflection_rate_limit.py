"""Regression test: the SYSTAT/ActiveUser UDP responder
(anetbbs/msp/systat.py) answered EVERY inbound datagram, from any
source, with no rate limiting at all -- a classic UDP reflection/
amplification vector. The real request (query_systat()'s own
convention) is a bare 2-byte "\\r\\n" packet, while the reply is easily
10-50x that. An attacker who spoofs a victim's source IP onto tiny
request packets gets this server to blast amplified replies at the
victim -- no authentication is possible without breaking real
inter-BBS "who's online" discovery, so the fix bounds the damage
instead: per-source-IP AND a global cap. Found in a security/
performance audit.

Uses a real UDP socket end to end (not just checking the rate-limit
bucket directly) to prove requests past the per-IP cap genuinely get
NO reply at all -- silently dropped, not partially amplified.
"""
import os
import socket
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class SystatUdpReflectionRateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.systat_rl_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        # A non-privileged high port -- binding the real port 11
        # requires root, which a test process shouldn't need.
        cls.app.config['SYSTAT_PORT'] = 15011
        cls.app.config['SYSTAT_BIND_HOST'] = '127.0.0.1'

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.features.rate_limit import _buckets
        _buckets.clear()
        from anetbbs.msp import systat as systat_mod
        systat_mod.start_systat_server(self.app)
        self.addCleanup(systat_mod.stop_systat_server)
        # Give the server thread a moment to bind + start its accept loop.
        time.sleep(0.3)

    def _query_once(self, timeout=1.0):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(b'\r\n', ('127.0.0.1', 15011))
            try:
                data, _addr = s.recvfrom(8192)
                return data
            except socket.timeout:
                return None

    def test_requests_within_the_per_ip_limit_all_get_a_reply(self):
        from anetbbs.msp.systat import _SYSTAT_PER_IP_LIMIT
        for i in range(_SYSTAT_PER_IP_LIMIT):
            reply = self._query_once()
            self.assertIsNotNone(
                reply, f'request {i+1}/{_SYSTAT_PER_IP_LIMIT} (within the '
                       f'per-IP limit) must get a real reply')

    def test_requests_past_the_per_ip_limit_get_no_reply_at_all(self):
        from anetbbs.msp.systat import _SYSTAT_PER_IP_LIMIT
        for _ in range(_SYSTAT_PER_IP_LIMIT):
            self._query_once()
        # One more, past the limit -- must be silently dropped, not
        # amplified with a real (even if generic/error) reply.
        reply = self._query_once(timeout=0.5)
        self.assertIsNone(
            reply, 'a request past the per-IP rate limit must get NO reply '
                   'at all -- a rate-limited request must never still be '
                   'amplified')

    def test_global_cap_is_enforced_independently_of_per_ip(self):
        """Directly exercises the rate-limit bucket (not through the
        real socket, to avoid needing SYSTAT_GLOBAL_LIMIT-many distinct
        source ports/sockets) -- confirms the global key is a real,
        separate cap from the per-IP one."""
        from anetbbs.features.rate_limit import _check
        from anetbbs.msp.systat import (_SYSTAT_GLOBAL_LIMIT,
                                        _SYSTAT_GLOBAL_WINDOW)
        allowed = 0
        for _ in range(_SYSTAT_GLOBAL_LIMIT + 5):
            if _check('systat:global', _SYSTAT_GLOBAL_LIMIT, _SYSTAT_GLOBAL_WINDOW):
                allowed += 1
        self.assertEqual(allowed, _SYSTAT_GLOBAL_LIMIT,
                         'the global bucket must cap out at exactly '
                         '_SYSTAT_GLOBAL_LIMIT allowed requests')


if __name__ == '__main__':
    unittest.main()
