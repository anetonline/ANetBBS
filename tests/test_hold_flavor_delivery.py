"""Tests making 'Hold' actually mean something for our OWN outbound
delivery, for both netmail and file-echo/TIC (HatchQueue).

Before this, NetmailMessage.is_hold (and the new HatchQueue.is_hold)
only ever set the FTS-0001 ATTR_HOLD bit in the outbound packet header
-- informational metadata for the RECEIVING tosser -- and had zero
effect on whether WE sent the item on our own scheduled/manual dial-out.
A hold-flagged item shipped on the very next poll exactly like a normal
one.

Real FTN hold-for-pickup semantics: a hold item must be excluded from
OUR OWN outbound dial (poller.py's _do_poll/_do_poll_node/_run_client/
_run_node_client), but MUST still ship the moment the peer polls IN to
us (binkp_server.py's inbound-listener branches) -- that's the pickup.
tosser.get_pending_netmail_for_node()/get_pending_netmail_for_network()
now take an include_hold flag to express exactly that distinction from
one shared implementation, since both directions call the same
functions.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class HoldFlavorDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.hold_flavor_test.db')
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

    def _make_network(self, name, our='1200:1/1', hub='1200:1/2'):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(
            name=name, network_type='binkp',
            our_address=our, hub_address=hub, is_active=True)
        db.session.add(net)
        db.session.commit()
        return net

    # -- get_pending_netmail_for_node / _for_network's include_hold -----

    def test_get_pending_netmail_for_network_excludes_hold_by_default(self):
        from anetbbs.models import db, NetmailMessage
        from anetbbs.echomail.tosser import get_pending_netmail_for_network

        with self.app.app_context():
            net = self._make_network('HoldExcludeNet')
            held = NetmailMessage(
                network_id=net.id, from_name='A', to_name='B',
                from_address='1200:1/1', to_address='1200:1/99',
                body='held', direction='outbound', status='queued',
                is_hold=True)
            normal = NetmailMessage(
                network_id=net.id, from_name='A', to_name='B',
                from_address='1200:1/1', to_address='1200:1/98',
                body='normal', direction='outbound', status='queued',
                is_hold=False)
            db.session.add_all([held, normal])
            db.session.commit()

            excluded = get_pending_netmail_for_network(net.id)
            ids = {m.id for m in excluded}
            self.assertNotIn(held.id, ids, 'hold-flagged netmail must not '
                             'ship on our own outbound dial by default')
            self.assertIn(normal.id, ids)

    def test_get_pending_netmail_for_network_includes_hold_when_asked(self):
        from anetbbs.models import db, NetmailMessage
        from anetbbs.echomail.tosser import get_pending_netmail_for_network

        with self.app.app_context():
            net = self._make_network('HoldIncludeNet')
            held = NetmailMessage(
                network_id=net.id, from_name='A', to_name='B',
                from_address='1200:1/1', to_address='1200:1/97',
                body='held', direction='outbound', status='queued',
                is_hold=True)
            db.session.add(held)
            db.session.commit()

            included = get_pending_netmail_for_network(net.id, include_hold=True)
            ids = {m.id for m in included}
            self.assertIn(held.id, ids, 'include_hold=True (the peer '
                          'polling IN to us) must release hold mail')

    def test_get_pending_netmail_for_node_excludes_hold_by_default(self):
        from anetbbs.models import db, NetmailMessage, BinkPNode
        from anetbbs.echomail.tosser import get_pending_netmail_for_node

        with self.app.app_context():
            net = self._make_network('NodeHoldNet')
            node = BinkPNode(name='HoldNode', ftn_address='1200:1/70',
                             password='x', network_id=net.id)
            db.session.add(node)
            db.session.commit()

            held = NetmailMessage(
                network_id=net.id, from_name='A', to_name='B',
                from_address='1200:1/1', to_address='1200:1/70',
                body='held', direction='outbound', status='queued',
                is_hold=True)
            normal = NetmailMessage(
                network_id=net.id, from_name='A', to_name='B',
                from_address='1200:1/1', to_address='1200:1/70',
                body='normal', direction='outbound', status='queued',
                is_hold=False)
            db.session.add_all([held, normal])
            db.session.commit()

            excluded = get_pending_netmail_for_node(node)
            ids = {m.id for m in excluded}
            self.assertNotIn(held.id, ids)
            self.assertIn(normal.id, ids)

            included = get_pending_netmail_for_node(node, include_hold=True)
            self.assertIn(held.id, {m.id for m in included})

    # -- _do_poll's inline netmail gather also excludes hold --------------

    def test_do_poll_gathers_only_non_hold_netmail(self):
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, NetmailMessage

        with self.app.app_context():
            net = self._make_network('DoPollHoldNet')
            held = NetmailMessage(
                network_id=net.id, from_name='A', to_name='B',
                from_address='1200:1/1', to_address='1200:1/96',
                body='held', direction='outbound', status='queued',
                is_hold=True)
            normal = NetmailMessage(
                network_id=net.id, from_name='A', to_name='B',
                from_address='1200:1/1', to_address='1200:1/95',
                body='normal', direction='outbound', status='queued',
                is_hold=False)
            db.session.add_all([held, normal])
            db.session.commit()

            captured = {}

            def _fake_run_client(network, outbound, app, transcript=None):
                captured['outbound'] = list(outbound)
                return {'sent': 0, 'received': []}

            with patch.object(poller_mod, '_run_client', _fake_run_client):
                poller_mod._do_poll(self.app, net)

            bodies = {getattr(m, 'body', None) for m in captured['outbound']}
            self.assertNotIn('held', bodies)
            self.assertIn('normal', bodies)

    # -- HatchQueue is_hold exclusion from our own outbound dial ----------

    def _make_file_area(self, network, tag='HOLDTEST'):
        from anetbbs.models import db, FileArea
        area = FileArea(tag=tag, name='Hold Test Area',
                        network_id=network.id, storage_path='/tmp')
        db.session.add(area)
        db.session.commit()
        return area

    def test_run_client_hatch_gather_excludes_hold(self):
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, HatchQueue

        with self.app.app_context():
            net = self._make_network('HatchHoldNet', hub='1200:1/3')
            area = self._make_file_area(net)
            held = HatchQueue(
                file_area_id=area.id, peer_address=net.hub_address,
                binary_path='/tmp/held.bin', filename='held.bin',
                status='pending', is_hold=True)
            normal = HatchQueue(
                file_area_id=area.id, peer_address=net.hub_address,
                binary_path='/tmp/normal.bin', filename='normal.bin',
                status='pending', is_hold=False)
            db.session.add_all([held, normal])
            db.session.commit()

            captured = {}

            class _FakeClient:
                def __init__(self, **kw): pass
                def poll(self, outbound_messages=None, data_dir=None,
                        hatch_items=None):
                    captured['hatch_items'] = list(hatch_items or [])
                    return {'sent': 0, 'received': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller_mod._run_client(net, [], self.app)

            names = {i.filename for i in captured['hatch_items']}
            self.assertNotIn('held.bin', names)
            self.assertIn('normal.bin', names)

    def test_run_node_client_hatch_gather_excludes_hold(self):
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, HatchQueue, BinkPNode

        with self.app.app_context():
            net = self._make_network('HatchNodeHoldNet', hub='1200:1/4')
            node = BinkPNode(name='HatchHoldNode', ftn_address='1200:1/80',
                             password='x', network_id=net.id,
                             binkp_host='hatchnode.example.test')
            db.session.add(node)
            db.session.commit()
            area = self._make_file_area(net, tag='HOLDTEST2')
            held = HatchQueue(
                file_area_id=area.id, peer_address=node.ftn_address,
                binary_path='/tmp/held2.bin', filename='held2.bin',
                status='pending', is_hold=True)
            normal = HatchQueue(
                file_area_id=area.id, peer_address=node.ftn_address,
                binary_path='/tmp/normal2.bin', filename='normal2.bin',
                status='pending', is_hold=False)
            db.session.add_all([held, normal])
            db.session.commit()

            captured = {}

            class _FakeClient:
                def __init__(self, **kw): pass
                def poll(self, outbound_messages=None, data_dir=None,
                        hatch_items=None):
                    captured['hatch_items'] = list(hatch_items or [])
                    return {'sent': 0, 'received': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller_mod._run_node_client(node, net, [], self.app)

            names = {i.filename for i in captured['hatch_items']}
            self.assertNotIn('held2.bin', names)
            self.assertIn('normal2.bin', names)

    # -- binkp_server.py's inbound-response branches release hold mail --

    def test_binkp_server_still_uses_include_hold_true_for_node_branch(self):
        """Static guard, not a live-session integration test: confirm the
        inbound-listener source still calls get_pending_netmail_for_node
        with include_hold=True for the downstream-node branch, so a
        future edit can't silently regress hold-release-on-poll-in back
        to the pre-fix (never releases) or a blanket always-release
        (defeats the point of hold on OUR OWN dial-out) behavior without
        a test noticing."""
        import inspect
        from anetbbs.echomail import binkp_server as bs_mod

        src = inspect.getsource(bs_mod)
        self.assertIn('get_pending_netmail_for_node(node, include_hold=True)', src)
        self.assertIn('get_pending_netmail_for_network(net_id, include_hold=True)', src)


if __name__ == '__main__':
    unittest.main()
