"""Tests for hub-initiated outbound polling of a downstream BinkP node
("Poll Now" for nodes, not just upstream networks) -- a real gap: a
sysop running a hub had no way to push mail to a specific downstream
node on demand, only wait for the node to call in. Mirrors the existing
poll_network_now()/_do_poll() path for upstream EchomailNetwork rows,
reusing the same BinkPHoldQueue mechanism binkp_server.py already uses
when a node polls IN, and the same net_id resolution (BinkPNode.network_id
/ routing.self_hub_binkp_network) fixed for the inbound-misattribution bug.

Uses the same real-Flask-app-plus-sqlite pattern as
test_poller_ack_gated_stamping.py, patching poller._run_node_client
directly so no real network I/O happens.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class NodePollNowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poller_nodepoll_test.db')
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

    def _make_node_with_queued_message(self, name, ftn, host='node.example.test'):
        from anetbbs.models import (db, EchomailNetwork, EchoArea,
                                    EchomailMessage, BinkPNode, BinkPHoldQueue)

        net = EchomailNetwork(
            name=f'{name}Net', network_type='binkp',
            our_address='1200:1/1', hub_address='1200:1/1',
            is_active=True,
        )
        db.session.add(net)
        db.session.commit()

        area = EchoArea(network_id=net.id, tag='TEST.ECHO', name='Test Echo')
        db.session.add(area)
        db.session.commit()

        msg = EchomailMessage(
            area_id=area.id, network_id=net.id,
            from_name='Sysop', to_name='All', subject='queued for node',
            body='hello node', direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()

        node = BinkPNode(
            name=name, ftn_address=ftn, password='secret',
            network_id=net.id, binkp_host=host, binkp_port=24554,
        )
        db.session.add(node)
        db.session.commit()

        hold = BinkPHoldQueue(node_id=node.id, message_id=msg.id, status='pending')
        db.session.add(hold)
        db.session.commit()

        return net, node, msg

    def test_no_binkp_host_does_not_poll_or_crash(self):
        """A node with no binkp_host configured (poll-in only) must be a
        clean no-op -- no EchomailPollLog row, no crash."""
        from anetbbs.models import db, EchomailNetwork, BinkPNode, EchomailPollLog
        from anetbbs.echomail import poller

        with self.app.app_context():
            net = EchomailNetwork(name='PollInOnlyNet', network_type='binkp',
                                  our_address='1:1/1', hub_address='1:1/1',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            node = BinkPNode(name='PollInOnly', ftn_address='1:1/50',
                             password='x', network_id=net.id, binkp_host=None)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

            before = EchomailPollLog.query.count()
            poller.poll_node_now(self.app, node_id)
            after = EchomailPollLog.query.count()
            self.assertEqual(before, after,
                             'no BinkP host must mean no poll attempted at all')

    def test_hub_ack_marks_hold_queue_sent_and_logs_success(self):
        """The node acknowledging the batch (result['sent'] nonzero) must
        mark the hold-queue message sent and record a success poll log
        under the CORRECT network_id."""
        from anetbbs.models import db, EchomailMessage, EchomailPollLog
        from anetbbs.echomail import poller

        from anetbbs.models import BinkPHoldQueue

        with self.app.app_context():
            net, node, msg = self._make_node_with_queued_message('AckNode', '1200:1/2')
            net_id, node_id, msg_id = net.id, node.id, msg.id

            with patch.object(poller, '_run_node_client',
                              return_value={'sent': 1, 'received': [],
                                            'hatched_ids': []}):
                poller.poll_node_now(self.app, node_id)

            hold_row = BinkPHoldQueue.query.filter_by(
                node_id=node_id, message_id=msg_id).first()
            self.assertEqual(hold_row.status, 'sent',
                             'hold-queue row must be marked sent once the '
                             'node acknowledges the batch')

            log = (EchomailPollLog.query
                  .order_by(EchomailPollLog.id.desc()).first())
            self.assertEqual(log.network_id, net_id,
                             'poll log must be attributed to the node\'s '
                             'actual resolved network')
            self.assertEqual(log.status, 'success')
            self.assertEqual(log.messages_sent, 1)

    def test_no_ack_leaves_hold_queue_message_pending(self):
        """result['sent'] == 0 (node didn't ack, e.g. SKIP/ERR/dropped
        connection) must NOT mark the hold-queue message sent -- it
        should still be retried next time, mirroring the network-poll
        ack-gating fix (test_poller_ack_gated_stamping.py)."""
        from anetbbs.models import db, BinkPHoldQueue
        from anetbbs.echomail import poller

        with self.app.app_context():
            net, node, msg = self._make_node_with_queued_message('NoAckNode', '1200:1/3')
            node_id, msg_id = node.id, msg.id

            with patch.object(poller, '_run_node_client',
                              return_value={'sent': 0, 'received': [],
                                            'hatched_ids': []}):
                poller.poll_node_now(self.app, node_id)

            hold_row = BinkPHoldQueue.query.filter_by(
                node_id=node_id, message_id=msg_id).first()
            self.assertEqual(hold_row.status, 'pending',
                             'hold-queue row must stay pending for retry '
                             'when the node did not acknowledge the batch')

    def test_node_with_no_resolvable_network_is_skipped_not_crashed(self):
        """A node whose hub identity has no unambiguous binkp network
        (network_id unset AND ambiguous fallback) must not crash --
        skipped with a log warning, same fail-safe shape as the
        no-binkp-host case."""
        from anetbbs.models import db, EchomailNetwork, BinkPNode, HubIdentity, EchomailPollLog
        from anetbbs.echomail import poller

        with self.app.app_context():
            identity = HubIdentity(name='AmbiguousIdentity', slug='ambiguous-identity',
                                   binkp_zone=9999, binkp_net=1, binkp_hub_node=1,
                                   is_active=True, is_default=False)
            db.session.add(identity)
            db.session.commit()

            # Two leaf-membership networks under the same identity, neither
            # self-hub -- genuinely ambiguous, matches no single candidate.
            net_a = EchomailNetwork(name='LeafA', network_type='binkp',
                                    our_address='9999:1/50', hub_address='9999:1/0',
                                    hub_identity_id=identity.id, is_active=True)
            net_b = EchomailNetwork(name='LeafB', network_type='binkp',
                                    our_address='9999:1/51', hub_address='9999:1/1',
                                    hub_identity_id=identity.id, is_active=True)
            db.session.add_all([net_a, net_b])
            db.session.commit()

            node = BinkPNode(name='Ambiguous', ftn_address='9999:1/60',
                             password='x', hub_identity_id=identity.id,
                             network_id=None, binkp_host='ambiguous.example.test')
            db.session.add(node)
            db.session.commit()
            node_id = node.id

            before = EchomailPollLog.query.count()
            poller.poll_node_now(self.app, node_id)
            after = EchomailPollLog.query.count()
            self.assertEqual(before, after,
                             'genuinely ambiguous network resolution must '
                             'skip cleanly, not crash or guess')


if __name__ == '__main__':
    unittest.main()
