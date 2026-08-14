"""Regression test for a real gap found in a security audit: QWK hub
authentication (anetbbs.web.qwk_hub._auth_node(), HTTP Basic Auth
against QWKNode) had no rate limiting at all on repeated failed
attempts, unlike /auth/login. Fixed using the same underlying
sliding-window limiter (features/rate_limit.py) the login route
already uses, keyed by source IP.
"""
import base64
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _basic_auth_header(username, password):
    token = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return {'Authorization': f'Basic {token}'}


class QwkHubAuthRateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_rate_limit_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, QWKNode
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            db.session.add(QWKNode(packet_id='RATENODE', name='Rate Test Node',
                                   password='correctpw', is_active=True))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def test_repeated_bad_auth_attempts_get_rate_limited(self):
        client = self.app.test_client()
        statuses = []
        for _ in range(15):
            resp = client.get('/qwkhub/RATENODE.qwk',
                              headers=_basic_auth_header('RATENODE', 'wrongpw'))
            statuses.append(resp.status_code)
        self.assertIn(401, statuses, 'early attempts should be rejected as bad credentials')
        self.assertIn(429, statuses, 'later attempts should be rate-limited')
        # Once rate-limited, it must STAY limited for the rest of the burst,
        # not flip back to 401 (which would mean the limiter isn't holding).
        first_429 = statuses.index(429)
        self.assertTrue(all(s == 429 for s in statuses[first_429:]),
                        'once limited, all further attempts in the same window must stay 429')

    def test_legitimate_auth_still_works_under_the_limit(self):
        client = self.app.test_client()
        resp = client.get('/qwkhub/RATENODE.qwk',
                          headers=_basic_auth_header('RATENODE', 'correctpw'))
        self.assertEqual(resp.status_code, 200)

    def test_rate_limit_is_keyed_per_ip_not_global(self):
        # Two different source IPs each get their own budget -- one
        # exhausting its attempts must not lock out the other.
        client = self.app.test_client()
        for _ in range(10):
            client.get('/qwkhub/RATENODE.qwk',
                       headers=_basic_auth_header('RATENODE', 'wrongpw'),
                       environ_overrides={'REMOTE_ADDR': '10.0.0.1'})
        blocked = client.get('/qwkhub/RATENODE.qwk',
                             headers=_basic_auth_header('RATENODE', 'wrongpw'),
                             environ_overrides={'REMOTE_ADDR': '10.0.0.1'})
        self.assertEqual(blocked.status_code, 429)

        still_ok = client.get('/qwkhub/RATENODE.qwk',
                              headers=_basic_auth_header('RATENODE', 'correctpw'),
                              environ_overrides={'REMOTE_ADDR': '10.0.0.2'})
        self.assertEqual(still_ok.status_code, 200)


if __name__ == '__main__':
    unittest.main()
