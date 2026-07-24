"""Regression tests for a real live bug: queued outbound NetmailMessage
rows were NEVER flushed via the BinkP inbound listener (binkp_server.py,
used whenever a peer connects IN to us) -- only the echomail hold queue
was. poller.py's own outbound-dial path (_do_poll) has always gathered
both EchomailMessage and NetmailMessage in one batch, but for a
downstream node whose only real-world relationship is "it calls its
hub" (the normal FTN pattern -- a hub does not usually dial a leaf node
on a schedule), that outbound-dial path never runs at all, so anything
queued for that node was stuck forever.

Confirmed live: a sysop's own reply to a downstream node's test
netmail, and every AreaFix bot auto-reply queued for that same node,
sat in NetmailMessage.status='queued' for over a week with zero error
anywhere -- the BinkP sessions with that node looked completely
successful the whole time.

This exercises the actual query/gathering functions
(get_pending_netmail_for_node / get_pending_netmail_for_network /
mark_netmail_sent, added to anetbbs/echomail/tosser.py) directly
against a real DB, rather than mocking the full async BinkP handshake
binkp_server.py's inbound listener wires them into -- the gathering
logic (in particular the per-node address matching, which must not
cross-deliver one node's mail to a different node on the same network)
is where a real bug could hide; the send/mark-sent orchestration around
it mirrors already-tested code (mark_sent_for_node, poller.py's
_do_poll) closely enough not to need re-proving here.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _DbTestBase(unittest.TestCase):
    DB_SUFFIX = 'base'

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                          f'.netmail_flush_{cls.DB_SUFFIX}_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

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


class GetPendingNetmailForNodeTests(_DbTestBase):
    DB_SUFFIX = 'for_node'

    def _make_network_and_node(self, suffix, ftn_address):
        from anetbbs.models import db, EchomailNetwork, BinkPNode
        net = EchomailNetwork(name=f'Net{suffix}', network_type='binkp',
                              our_address='1200:1/1', is_active=True)
        db.session.add(net)
        db.session.commit()
        node = BinkPNode(name=f'Node{suffix}', ftn_address=ftn_address,
                         password='secret', network_id=net.id,
                         binkp_host='node.example.test', binkp_port=24554)
        db.session.add(node)
        db.session.commit()
        return net, node

    def _make_queued_netmail(self, network_id, to_address, subject='Test'):
        from anetbbs.models import db, NetmailMessage
        from datetime import datetime
        nm = NetmailMessage(
            network_id=network_id, direction='outbound', status='queued',
            from_name='Sysop', to_name='Someone', to_address=to_address,
            subject=subject, body='body', created_at=datetime.utcnow())
        db.session.add(nm)
        db.session.commit()
        return nm

    def test_queued_netmail_addressed_to_node_exact_address_is_returned(self):
        from anetbbs.echomail.tosser import get_pending_netmail_for_node
        with self.app.app_context():
            net, node = self._make_network_and_node('A', '1200:1/401@anet')
            nm = self._make_queued_netmail(net.id, '1200:1/401@anet')

            result = get_pending_netmail_for_node(node)
            self.assertEqual([r.id for r in result], [nm.id])

    def test_queued_netmail_addressed_to_bare_form_still_matches(self):
        """The exact live bug shape: the netmail row's to_address and the
        node's own ftn_address aren't guaranteed to share the same one
        of the two equally-valid (@domain vs bare) forms."""
        from anetbbs.echomail.tosser import get_pending_netmail_for_node
        with self.app.app_context():
            net, node = self._make_network_and_node('B', '1200:1/402@anet')
            nm = self._make_queued_netmail(net.id, '1200:1/402')  # bare, no @domain

            result = get_pending_netmail_for_node(node)
            self.assertEqual([r.id for r in result], [nm.id])

    def test_netmail_for_a_different_node_on_same_network_is_excluded(self):
        """Must not cross-deliver -- a network can have multiple
        downstream nodes, each with mail queued only for it."""
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail.tosser import get_pending_netmail_for_node
        with self.app.app_context():
            net, node = self._make_network_and_node('C', '1200:1/403@anet')
            other_node = BinkPNode(name='OtherNode', ftn_address='1200:1/499@anet',
                                   password='x', network_id=net.id,
                                   binkp_host='other.example.test')
            db.session.add(other_node)
            db.session.commit()
            self._make_queued_netmail(net.id, '1200:1/499@anet')  # for other_node

            result = get_pending_netmail_for_node(node)
            self.assertEqual(result, [])

    def test_already_sent_netmail_is_excluded(self):
        from anetbbs.models import db
        from anetbbs.echomail.tosser import get_pending_netmail_for_node
        with self.app.app_context():
            net, node = self._make_network_and_node('D', '1200:1/404@anet')
            nm = self._make_queued_netmail(net.id, '1200:1/404@anet')
            nm.status = 'sent'
            db.session.commit()

            result = get_pending_netmail_for_node(node)
            self.assertEqual(result, [])

    def test_inbound_netmail_is_excluded(self):
        from anetbbs.models import db, NetmailMessage
        from anetbbs.echomail.tosser import get_pending_netmail_for_node
        from datetime import datetime
        with self.app.app_context():
            net, node = self._make_network_and_node('E', '1200:1/405@anet')
            db.session.add(NetmailMessage(
                network_id=net.id, direction='inbound', status='received',
                from_name='Them', to_name='Sysop', from_address='1200:1/405@anet',
                subject='Test', body='body', created_at=datetime.utcnow()))
            db.session.commit()

            result = get_pending_netmail_for_node(node)
            self.assertEqual(result, [])

    def test_node_with_no_ftn_address_falls_back_to_network_only(self):
        """Fail-open, matching this codebase's established stance for
        AKA/address resolution elsewhere in the BinkP listener: better
        to deliver broadly than leave mail silently stuck again."""
        from anetbbs.models import db, EchomailNetwork, BinkPNode
        from anetbbs.echomail.tosser import get_pending_netmail_for_node
        with self.app.app_context():
            net = EchomailNetwork(name='NetF', network_type='binkp',
                                  our_address='1200:1/1', is_active=True)
            db.session.add(net)
            db.session.commit()
            node = BinkPNode(name='NodeF', ftn_address='', password='secret',
                             network_id=net.id, binkp_host='node.example.test')
            db.session.add(node)
            db.session.commit()
            nm = self._make_queued_netmail(net.id, '1200:1/406@anet')

            result = get_pending_netmail_for_node(node)
            self.assertEqual([r.id for r in result], [nm.id])


class GetPendingNetmailForNetworkTests(_DbTestBase):
    DB_SUFFIX = 'for_network'

    def test_returns_all_queued_outbound_netmail_for_network(self):
        from anetbbs.models import db, EchomailNetwork, NetmailMessage
        from anetbbs.echomail.tosser import get_pending_netmail_for_network
        from datetime import datetime
        with self.app.app_context():
            net = EchomailNetwork(name='NetG', network_type='binkp',
                                  our_address='1:1/1', hub_address='1:1/0',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            nm1 = NetmailMessage(network_id=net.id, direction='outbound',
                                 status='queued', from_name='Sysop',
                                 to_name='A', to_address='1:1/2', subject='A',
                                 body='b', created_at=datetime.utcnow())
            nm2 = NetmailMessage(network_id=net.id, direction='outbound',
                                 status='queued', from_name='Sysop',
                                 to_name='B', to_address='1:1/3', subject='B',
                                 body='b', created_at=datetime.utcnow())
            db.session.add_all([nm1, nm2])
            db.session.commit()

            result = get_pending_netmail_for_network(net.id)
            self.assertEqual(sorted(r.id for r in result), sorted([nm1.id, nm2.id]))


class MarkNetmailSentTests(_DbTestBase):
    DB_SUFFIX = 'mark_sent'

    def test_marks_status_sent_at_and_is_sent(self):
        from anetbbs.models import db, EchomailNetwork, NetmailMessage
        from anetbbs.echomail.tosser import mark_netmail_sent
        from datetime import datetime
        with self.app.app_context():
            net = EchomailNetwork(name='NetH', network_type='binkp',
                                  our_address='1:1/1', is_active=True)
            db.session.add(net)
            db.session.commit()
            nm = NetmailMessage(network_id=net.id, direction='outbound',
                               status='queued', from_name='Sysop',
                               to_name='A', to_address='1:1/2', subject='A',
                               body='b', created_at=datetime.utcnow())
            db.session.add(nm)
            db.session.commit()
            nm_id = nm.id

            count = mark_netmail_sent([nm])
            self.assertEqual(count, 1)

            refreshed = NetmailMessage.query.get(nm_id)
            self.assertEqual(refreshed.status, 'sent')
            self.assertTrue(refreshed.is_sent)
            self.assertIsNotNone(refreshed.sent_at)

    def test_empty_list_is_a_safe_noop(self):
        from anetbbs.echomail.tosser import mark_netmail_sent
        with self.app.app_context():
            self.assertEqual(mark_netmail_sent([]), 0)


if __name__ == '__main__':
    unittest.main()
