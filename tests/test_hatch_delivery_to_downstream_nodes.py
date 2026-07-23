"""Regression tests for the most severe finding in a full echomail-
subsystem audit: file-echo hatch-out to downstream BinkP nodes was
never actually transmitted, on EITHER direction a node's mail can
flow.

The whole rest of the pipeline was already correct -- FileFix
subscriptions (FileEchoSubscription), HatchQueue rows created on
upload/TIC-in (tic.py's process_tic()/hatch_local_file()), and
build_tic_text() manifest rendering -- but nothing ever read those
HatchQueue rows back out for delivery to a DOWNSTREAM node specifically
(as opposed to a leaf's own upstream hub, which poller.py's
_run_client() already handled correctly). A sysop subscribes a node to
a file echo, a file gets hatched, the hub admin's "Pending" counter
goes up, and it just sits there forever with zero error anywhere.

Two independent delivery paths needed the identical fix:
  1. Hub dials OUT to a node (poller.py's _run_node_client) -- used to
     unconditionally pass hatch_items=[].
  2. A node dials IN to the hub (binkp_server.py's inbound listener,
     the downstream_node_id branch) -- never queried HatchQueue at all.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _DbTestBase(unittest.TestCase):
    DB_SUFFIX = 'base'

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                          f'.hatch_delivery_{cls.DB_SUFFIX}_test.db')
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


class RunNodeClientHatchOutTests(_DbTestBase):
    """poller.py's _run_node_client -- the hub-dials-OUT-to-a-node
    direction."""

    DB_SUFFIX = 'run_node_client'

    def _make_node_and_hatch_item(self, tmpdir, suffix=''):
        from anetbbs.models import (db, EchomailNetwork, EchoArea, FileArea,
                                    BinkPNode, HatchQueue)

        net = EchomailNetwork(name=f'HatchOutNet{suffix}', network_type='binkp',
                              our_address='1200:1/1', hub_address='1200:1/1',
                              is_active=True)
        db.session.add(net)
        db.session.commit()

        node = BinkPNode(name=f'HatchNode{suffix}', ftn_address=f'1200:1/{2000 + hash(suffix) % 1000}',
                         password='secret', network_id=net.id,
                         binkp_host='node.example.test', binkp_port=24554)
        db.session.add(node)
        db.session.commit()

        farea = FileArea(tag=f'HATCHTEST{suffix}', name='Hatch Test Area', is_active=True)
        db.session.add(farea)
        db.session.commit()

        binary_path = os.path.join(tmpdir, 'testfile.zip')
        with open(binary_path, 'wb') as f:
            f.write(b'fake zip contents')

        item = HatchQueue(
            file_area_id=farea.id, peer_address=node.ftn_address,
            binary_path=binary_path, filename='testfile.zip',
            description='test', crc32='deadbeef', size_bytes=18,
            seenby='[]', path='[]', status='pending',
        )
        db.session.add(item)
        db.session.commit()

        return net, node, item

    def test_pending_hatch_item_is_passed_to_binkp_client_poll(self):
        import tempfile
        from anetbbs.echomail import poller

        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                net, node, item = self._make_node_and_hatch_item(tmpdir, suffix='A')

                captured = {}

                class _FakeClient:
                    def __init__(self, *a, **kw):
                        pass

                    def poll(self, outbound_messages, data_dir, hatch_items):
                        captured['hatch_items'] = hatch_items
                        return {'received': [], 'sent': 0, 'hatched_ids': []}

                with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                    poller._run_node_client(node, net, [], self.app)

                self.assertEqual(len(captured['hatch_items']), 1)
                self.assertEqual(captured['hatch_items'][0].id, item.id)

    def test_hatched_ids_returned_get_marked_sent(self):
        import tempfile
        from anetbbs.echomail import poller
        from anetbbs.models import HatchQueue

        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                net, node, item = self._make_node_and_hatch_item(tmpdir, suffix='B')
                item_id = item.id

                class _FakeClient:
                    def __init__(self, *a, **kw):
                        pass

                    def poll(self, outbound_messages, data_dir, hatch_items):
                        return {'received': [], 'sent': 0,
                               'hatched_ids': [hatch_items[0].id]}

                with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                    poller._run_node_client(node, net, [], self.app)

                refreshed = HatchQueue.query.get(item_id)
                self.assertEqual(refreshed.status, 'sent')
                self.assertIsNotNone(refreshed.sent_at)

    def test_hatch_item_for_a_different_node_is_not_included(self):
        import tempfile
        from anetbbs.echomail import poller
        from anetbbs.models import db, BinkPNode

        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                net, node, item = self._make_node_and_hatch_item(tmpdir, suffix='C')
                other_node = BinkPNode(name='OtherNode', ftn_address='1200:1/99',
                                       password='x', network_id=net.id,
                                       binkp_host='other.example.test')
                db.session.add(other_node)
                db.session.commit()

                captured = {}

                class _FakeClient:
                    def __init__(self, *a, **kw):
                        pass

                    def poll(self, outbound_messages, data_dir, hatch_items):
                        captured['hatch_items'] = hatch_items
                        return {'received': [], 'sent': 0, 'hatched_ids': []}

                with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                    poller._run_node_client(other_node, net, [], self.app)

                self.assertEqual(captured['hatch_items'], [])


class SendHatchItemsInboundListenerTests(_DbTestBase):
    """binkp_server.py's _send_hatch_items -- the node-dials-IN-to-the-
    hub direction."""

    DB_SUFFIX = 'send_hatch_items'

    def test_binary_and_tic_are_both_sent_and_item_marked_shippable(self):
        import asyncio
        import tempfile
        from anetbbs.echomail import binkp_server
        from anetbbs.models import db, FileArea, HatchQueue

        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                farea = FileArea(tag='INHATCH', name='Inbound Hatch Area',
                                 is_active=True)
                db.session.add(farea)
                db.session.commit()

                binary_path = os.path.join(tmpdir, 'inbound_test.zip')
                with open(binary_path, 'wb') as f:
                    f.write(b'inbound hatch contents')

                item = HatchQueue(
                    file_area_id=farea.id, peer_address='1200:1/2',
                    binary_path=binary_path, filename='inbound_test.zip',
                    description='x', crc32='deadbeef', size_bytes=23,
                    seenby='[]', path='[]', status='pending',
                )
                db.session.add(item)
                db.session.commit()

                sent_filenames = []

                async def _fake_send_pkt_file(reader, writer, filename, payload,
                                              peer, state, files, transcript=None):
                    sent_filenames.append(filename)
                    return True

                with patch.object(binkp_server, '_send_pkt_file', _fake_send_pkt_file):
                    sent_ids = asyncio.run(binkp_server._send_hatch_items(
                        None, None, [item], '1200:1/1', '1200:1/2',
                        {'name': None, 'size': 0, 'buf': bytearray()}, []))

                self.assertEqual(sent_ids, [item.id])
                self.assertIn('inbound_test.zip', sent_filenames)
                self.assertIn('inbound_test.tic', sent_filenames)

    def test_peer_not_acking_binary_skips_the_item_not_marked_sent(self):
        import asyncio
        import tempfile
        from anetbbs.echomail import binkp_server
        from anetbbs.models import db, FileArea, HatchQueue

        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                farea = FileArea(tag='NOACKHATCH', name='No Ack Hatch Area',
                                 is_active=True)
                db.session.add(farea)
                db.session.commit()

                binary_path = os.path.join(tmpdir, 'noack.zip')
                with open(binary_path, 'wb') as f:
                    f.write(b'x')

                item = HatchQueue(
                    file_area_id=farea.id, peer_address='1200:1/2',
                    binary_path=binary_path, filename='noack.zip',
                    description='x', crc32='x', size_bytes=1,
                    seenby='[]', path='[]', status='pending',
                )
                db.session.add(item)
                db.session.commit()

                async def _fake_send_pkt_file(*a, **kw):
                    return False

                with patch.object(binkp_server, '_send_pkt_file', _fake_send_pkt_file):
                    sent_ids = asyncio.run(binkp_server._send_hatch_items(
                        None, None, [item], '1200:1/1', '1200:1/2',
                        {'name': None, 'size': 0, 'buf': bytearray()}, []))

                self.assertEqual(sent_ids, [])


if __name__ == '__main__':
    unittest.main()
