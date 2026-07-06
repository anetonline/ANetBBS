"""Regression test: a network configured to dial its own address (the
same misconfiguration Jerry hit live on bbs.a-net.fyi -- activating the
seeded "ANotherNetwork" hub-pointing row on the hub's own install)
should be skipped by the poller, not attempted and logged as a failure.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SelfReferentialPollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poller_selfref_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

        from anetbbs.models import db
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_binkp_network_dialing_its_own_address_gets_skipped(self):
        from anetbbs.models import db, EchomailNetwork, EchomailPollLog
        from anetbbs.echomail.poller import _do_poll

        with self.app.app_context():
            net = EchomailNetwork(
                name='ANotherNetwork', network_type='binkp',
                binkp_host='bbs.a-net.fyi', binkp_port=24554,
                our_address='1200:1/1', hub_address='1200:1/1',
                is_active=True,
            )
            db.session.add(net)
            db.session.commit()
            net_id = net.id

            _do_poll(self.app, net)

            log = (EchomailPollLog.query.filter_by(network_id=net_id)
                   .order_by(EchomailPollLog.id.desc()).first())
            self.assertEqual(log.status, 'skipped')
            self.assertIn('our_address', log.error_message)

    def test_binkp_network_with_different_addresses_is_not_flagged(self):
        from anetbbs.echomail.poller import _self_referential_reason

        class _FakeNet:
            network_type = 'binkp'
            our_address = '1:234/567'
            hub_address = '1200:1/1'

        self.assertIsNone(_self_referential_reason(_FakeNet(), self.app))

    def test_qwk_network_matching_our_own_public_host_is_flagged(self):
        from anetbbs.echomail.poller import _self_referential_reason

        self.app.config['BBS_PUBLIC_HOST'] = 'bbs.a-net.fyi'

        class _FakeNet:
            network_type = 'qwk'
            qwk_host = 'bbs.a-net.fyi'

        try:
            reason = _self_referential_reason(_FakeNet(), self.app)
            self.assertIsNotNone(reason)
            self.assertIn('qwk_host', reason)
        finally:
            self.app.config.pop('BBS_PUBLIC_HOST', None)


if __name__ == '__main__':
    unittest.main()
