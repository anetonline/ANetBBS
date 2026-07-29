"""Regression test for a real gap Jerry flagged live: the network-join
approval email (approve_join_request() in anetbbs/web/hub_admin.py) gave
a new node's full FTN address and BinkP session password, but never the
BinkP port, never mentioned that a fresh node starts with ZERO echo/file
area subscriptions until they send an AreaFix/FileFix request (or the
exact syntax to do so), and buried the auto-assigned node number inside
the zone:net/node address string instead of calling it out plainly.

Fixed by adding: BinkP port, an explicit "Assigned node number" line, and
-- when the hub's own address is known -- ready-to-use AreaFix/FileFix
netmail instructions (recipient, password, and example commands).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class JoinApprovalEmailAreafixInstructionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.join_approval_email_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, HubIdentity, EchomailNetwork,
                                    NetworkJoinRequest)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()

            admin = User(username='joinapprovaladmin', is_admin=True,
                        email='admin@example.com')
            admin.set_password('x')
            db.session.add(admin)

            identity = HubIdentity(name='TestHub', slug='testhub-email',
                                   binkp_zone=1200, binkp_net=1,
                                   is_default=True, is_active=True)
            db.session.add(identity)
            db.session.flush()

            net = EchomailNetwork(
                name='TestHubNet', network_type='binkp', is_active=True,
                our_address='1200:1/1', hub_address='1200:1/1',
                binkp_port=24555, areafix_password='hubareapw',
                binkp_password='hubbinkpw', hub_identity_id=identity.id)
            db.session.add(net)

            req = NetworkJoinRequest(
                hub_identity_id=identity.id,
                name='Codefenix', bbs_name='ConstructiveChaos BBS',
                email='codefenix@example.com',
                binkp_ftn_address='1200:1/999',  # overwritten by auto-numbering
                rules_ack=True)
            db.session.add(req)
            db.session.commit()
            cls.req_id = req.id
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_approval_email_includes_port_node_number_and_areafix_instructions(self):
        client = self._admin_client()
        with patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             patch('anetbbs.mailer.send_email', return_value=(True, None)) as mock_send:
            client.post(
                f'/admin/echomail/hub/join/requests/{self.req_id}/approve',
                follow_redirects=True)

        self.assertTrue(mock_send.called, 'send_email was never called')
        call_args = mock_send.call_args[0]
        body = call_args[2]

        self.assertIn('BinkP port: 24555', body)
        self.assertIn('Assigned node number: 2', body,
                      'node 1 is the hub itself, so the first approved '
                      'downstream node must be numbered 2')
        self.assertIn('AreaFix', body)
        self.assertIn('FileFix', body)
        self.assertIn('1200:1/1', body, 'hub address must be given so the '
                      'applicant knows where to address AreaFix/FileFix netmail')
        self.assertIn('hubareapw', body,
                      'the network-specific areafix_password must be used, '
                      'not the plain BinkP session password')
        self.assertIn('+ALL', body)
        self.assertIn('%LIST', body)


if __name__ == '__main__':
    unittest.main()
