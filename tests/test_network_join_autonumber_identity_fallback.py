"""Regression test: Network Join approval's BinkP auto-numbering
(_next_binkp_node_address, added in v1.0b2.83) only ever fired when
NetworkJoinConfig.binkp_zone/binkp_net were set -- a SEPARATE pair of
fields from HubIdentity.binkp_zone/binkp_net, which a sysop typically
already configures once (it drives nodelist() and the default outbound-
address fallback elsewhere in hub_admin.py). A sysop who'd already told
the system its own zone:net had no reason to expect a second, redundant
place to set it for join approvals specifically, and got surprised when
an approved node kept whatever address the applicant typed into the
public form verbatim instead of being auto-numbered.

Real report: "the node number should be automatically given when
approved, i.e. if the last current node is 1200:1/6, ... 1200:1/7
should be assigned" -- the auto-numbering logic itself
(_next_binkp_node_address) was already correct (see
test_network_join.py's own coverage of it); this fixes it not actually
running for the common case.

Isolated DB (own test class/file) since this creates a default
HubIdentity, which other Network Join tests deliberately don't have.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class NetworkJoinAutonumberIdentityFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                          '.network_join_autonum_identity_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self, username='autonum_admin'):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=True,
                        email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def test_approval_auto_numbers_from_hub_identitys_own_zone_net(self):
        """NetworkJoinConfig.binkp_zone/net are deliberately left UNSET
        here -- only the HubIdentity's own zone/net are configured,
        matching the realistic single-identity setup. Approval must
        still auto-number, not fall through to the applicant's own
        proposed address."""
        from anetbbs.models import (db, HubIdentity, BinkPNode,
                                    NetworkJoinRequest)
        with self.app.app_context():
            identity = HubIdentity(name='Default', slug='default', is_default=True,
                                   binkp_zone=1200, binkp_net=1)
            db.session.add(identity)
            db.session.commit()

            # Existing downstream node at 1200:1/6 -- next approval should
            # land on 1200:1/7, the sysop's own reported expectation.
            existing = BinkPNode(name='Existing', ftn_address='1200:1/6',
                                 password='x', hub_identity_id=identity.id)
            db.session.add(existing)
            db.session.commit()

            req = NetworkJoinRequest(
                bbs_name='FallbackBBS', name='Applicant', email='a@example.com',
                binkp_ftn_address='9:9/9999',  # must be IGNORED
                hub_identity_id=identity.id, rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client()
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'approved')
            node = BinkPNode.query.get(req.binkp_node_id)
            self.assertEqual(node.ftn_address, '1200:1/7')
            self.assertNotEqual(node.ftn_address, '9:9/9999')

    def test_explicit_networkjoinconfig_zone_net_still_overrides_identity(self):
        """NetworkJoinConfig's own zone/net, when explicitly set, still
        wins over the identity's -- the fallback must not remove that
        override capability for the unusual case of wanting join-
        approved nodes numbered under a different zone:net."""
        from anetbbs.models import (db, HubIdentity, BinkPNode,
                                    NetworkJoinRequest, NetworkJoinConfig)
        with self.app.app_context():
            identity = HubIdentity(name='Default2', slug='default2', is_default=True,
                                   binkp_zone=1200, binkp_net=1)
            db.session.add(identity)
            db.session.commit()

            cfg = NetworkJoinConfig.get(hub_identity_id=identity.id)
            cfg.binkp_zone = 9999
            cfg.binkp_net = 99
            db.session.commit()

            req = NetworkJoinRequest(
                bbs_name='OverrideBBS', name='Applicant2', email='b@example.com',
                binkp_ftn_address='5:5/555', hub_identity_id=identity.id,
                rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('override_admin')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            node = BinkPNode.query.get(req.binkp_node_id)
            self.assertEqual(node.ftn_address, '9999:99/2')


if __name__ == '__main__':
    unittest.main()
