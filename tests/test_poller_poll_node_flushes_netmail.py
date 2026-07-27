"""Regression test: poller.py's _do_poll_node() -- the hub-initiated
"Poll Node" dial-OUT direction -- only ever gathered the echomail hold
queue (get_pending_for_node), never queued outbound NetmailMessage rows.

binkp_server.py's inbound-listener downstream_node_id branch already
gathers BOTH for this exact node (see tosser.py's
get_pending_netmail_for_node() docstring for the original live bug that
closed -- a sysop's own reply, and every AreaFix auto-reply, stuck in
NetmailMessage.status='queued' indefinitely), but the "Poll Node" admin
button (a node dial-OUT, added later) never got the same fix -- queued
netmail for a node with binkp_host set only went out if that node
happened to dial IN to us first, not when the sysop explicitly clicked
Poll Node.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PollNodeFlushesNetmailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poll_node_netmail_test.db')
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

    def _make_node_with_queued_netmail(self, suffix):
        from anetbbs.models import db, EchomailNetwork, BinkPNode, NetmailMessage
        from datetime import datetime

        net = EchomailNetwork(name=f'PollNodeNet{suffix}', network_type='binkp',
                              our_address='1200:1/1', hub_address='1200:1/1',
                              is_active=True)
        db.session.add(net)
        db.session.commit()
        node = BinkPNode(name=f'PollNodeTarget{suffix}',
                         ftn_address=f'1200:1/{500 + hash(suffix) % 100}',
                         password='secret', network_id=net.id,
                         binkp_host='node.example.test', binkp_port=24554)
        db.session.add(node)
        db.session.commit()
        nm = NetmailMessage(
            network_id=net.id, direction='outbound', status='queued',
            from_name='Sysop', to_name='Them', to_address=node.ftn_address,
            subject='Test reply', body='hi', created_at=datetime.utcnow())
        db.session.add(nm)
        db.session.commit()
        return net, node, nm

    def test_queued_netmail_is_included_in_the_poll_node_batch(self):
        from anetbbs.echomail import poller

        with self.app.app_context():
            net, node, nm = self._make_node_with_queued_netmail('A')

            captured = {}

            class _FakeClient:
                def __init__(self, *a, **kw):
                    pass

                def poll(self, outbound_messages, data_dir, hatch_items):
                    captured['outbound'] = outbound_messages
                    return {'received': [], 'sent': 0, 'hatched_ids': [],
                           'hatch_failures': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller._do_poll_node(self.app, node)

            to_addrs = [getattr(m, 'to_address', None) for m in captured['outbound']]
            self.assertIn(node.ftn_address, to_addrs,
                          'queued netmail for this node must be included when '
                          'Poll Node dials out, not just its echomail hold queue')

    def test_netmail_marked_sent_only_after_node_acks(self):
        from anetbbs.echomail import poller
        from anetbbs.models import NetmailMessage

        with self.app.app_context():
            net, node, nm = self._make_node_with_queued_netmail('B')
            nm_id = nm.id

            class _AckingClient:
                def __init__(self, *a, **kw):
                    pass

                def poll(self, outbound_messages, data_dir, hatch_items):
                    return {'received': [], 'sent': len(outbound_messages),
                           'hatched_ids': [], 'hatch_failures': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _AckingClient):
                poller._do_poll_node(self.app, node)

            refreshed = NetmailMessage.query.get(nm_id)
            self.assertEqual(refreshed.status, 'sent')
            self.assertTrue(refreshed.is_sent)

    def test_netmail_not_marked_sent_when_node_does_not_ack(self):
        from anetbbs.echomail import poller
        from anetbbs.models import NetmailMessage

        with self.app.app_context():
            net, node, nm = self._make_node_with_queued_netmail('C')
            nm_id = nm.id

            class _NoAckClient:
                def __init__(self, *a, **kw):
                    pass

                def poll(self, outbound_messages, data_dir, hatch_items):
                    return {'received': [], 'sent': 0, 'hatched_ids': [],
                           'hatch_failures': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _NoAckClient):
                poller._do_poll_node(self.app, node)

            refreshed = NetmailMessage.query.get(nm_id)
            self.assertEqual(refreshed.status, 'queued',
                             'unacked netmail must stay queued for retry')


if __name__ == '__main__':
    unittest.main()
