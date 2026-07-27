"""Regression tests for HatchQueue failure tracking.

HatchQueue.retry_count/error_message/status ('pending'/'sent'/'failed')
were part of the model from the start (its own docstring: "Failures
bump retry_count; we cap at retry_max and mark failed"), but neither
delivery path (binkp.py's _send_hatch, used by poller.py's outbound-dial
direction; binkp_server.py's _send_hatch_items, used by its inbound-
listener direction) ever actually wrote to them -- a failed delivery
attempt was silently indistinguishable from one never even tried, and
the Hub admin page's "Failed" counter was permanently 0. Surfaced by a
sysop asking for a TIC-out log for hub management: there was nothing to
show beyond "N pending," no visibility into why something never went
out or how many times it had been tried.

record_hatch_attempt_failure() (anetbbs/echomail/tic.py) is the shared
bookkeeping helper both delivery paths now call.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _DbTestBase(unittest.TestCase):
    DB_SUFFIX = 'base'

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                          f'.hatch_failure_{cls.DB_SUFFIX}_test.db')
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

    def _make_item(self, suffix, tmpdir, retry_count=0):
        from anetbbs.models import db, FileArea, HatchQueue
        farea = FileArea(tag=f'FAILTEST{suffix}', name='x', is_active=True)
        db.session.add(farea)
        db.session.commit()
        binary_path = os.path.join(tmpdir, f'{suffix}.zip')
        with open(binary_path, 'wb') as f:
            f.write(b'x')
        item = HatchQueue(
            file_area_id=farea.id, peer_address='1200:1/9',
            binary_path=binary_path, filename=f'{suffix}.zip',
            description='x', crc32='x', size_bytes=1,
            seenby='[]', path='[]', status='pending', retry_count=retry_count,
        )
        db.session.add(item)
        db.session.commit()
        return item


class RecordHatchAttemptFailureTests(_DbTestBase):
    DB_SUFFIX = 'helper'

    def test_bumps_retry_count_and_stores_message_without_flipping_status(self):
        from anetbbs.echomail.tic import record_hatch_attempt_failure
        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                item = self._make_item('A', tmpdir)
                record_hatch_attempt_failure(item, "peer didn't ack binary")
                self.assertEqual(item.retry_count, 1)
                self.assertEqual(item.error_message, "peer didn't ack binary")
                self.assertEqual(item.status, 'pending')

    def test_flips_to_failed_once_retry_max_reached(self):
        from anetbbs.echomail.tic import (record_hatch_attempt_failure,
                                          HATCH_RETRY_MAX)
        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                item = self._make_item('B', tmpdir, retry_count=HATCH_RETRY_MAX - 1)
                record_hatch_attempt_failure(item, 'still failing')
                self.assertEqual(item.retry_count, HATCH_RETRY_MAX)
                self.assertEqual(item.status, 'failed')


class PollerAppliesHatchFailuresTests(_DbTestBase):
    """poller.py's two hatch-out call sites (_run_node_client,
    _run_client) must apply hatch_failures from the client's poll()
    result, not just hatched_ids."""

    DB_SUFFIX = 'poller_failures'

    def test_run_node_client_records_failure_for_unacked_item(self):
        from anetbbs.echomail import poller
        from anetbbs.models import db, EchomailNetwork, BinkPNode, HatchQueue

        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                item = self._make_item('NodeClient', tmpdir)
                net = EchomailNetwork(name='FailNet', network_type='binkp',
                                      our_address='1200:1/1', hub_address='1200:1/1',
                                      is_active=True)
                db.session.add(net)
                db.session.commit()
                node = BinkPNode(name='FailNode', ftn_address='1200:1/91',
                                 password='secret', network_id=net.id,
                                 binkp_host='node.example.test')
                db.session.add(node)
                db.session.commit()
                item.peer_address = node.ftn_address
                db.session.commit()
                item_id = item.id

                class _FakeClient:
                    def __init__(self, *a, **kw):
                        pass

                    def poll(self, outbound_messages, data_dir, hatch_items):
                        return {'received': [], 'sent': 0, 'hatched_ids': [],
                               'hatch_failures': [(hatch_items[0], 'peer rejected')]}

                with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                    poller._run_node_client(node, net, [], self.app)

                refreshed = HatchQueue.query.get(item_id)
                self.assertEqual(refreshed.retry_count, 1)
                self.assertEqual(refreshed.error_message, 'peer rejected')
                self.assertEqual(refreshed.status, 'pending')

    def test_run_client_records_failure_for_unacked_item(self):
        from anetbbs.echomail import poller
        from anetbbs.models import db, EchomailNetwork, HatchQueue

        with self.app.app_context():
            with tempfile.TemporaryDirectory() as tmpdir:
                item = self._make_item('RunClient', tmpdir)
                net = EchomailNetwork(name='FailNet2', network_type='binkp',
                                      our_address='1200:1/1', hub_address='1200:1/92',
                                      is_active=True)
                db.session.add(net)
                db.session.commit()
                item.peer_address = net.hub_address
                db.session.commit()
                item_id = item.id

                class _FakeClient:
                    def __init__(self, *a, **kw):
                        pass

                    def poll(self, outbound_messages, data_dir, hatch_items):
                        return {'received': [], 'sent': 0, 'hatched_ids': [],
                               'hatch_failures': [(hatch_items[0], 'no ack')]}

                with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                    poller._run_client(net, [], self.app)

                refreshed = HatchQueue.query.get(item_id)
                self.assertEqual(refreshed.retry_count, 1)
                self.assertEqual(refreshed.error_message, 'no ack')
                self.assertEqual(refreshed.status, 'pending')


class BinkpSendHatchReturnsFailuresTests(unittest.TestCase):
    """binkp.py's BinkPClient._send_hatch must report failures, not just
    silently skip them."""

    def test_unacked_binary_is_reported_as_a_failure_not_silently_dropped(self):
        from anetbbs.echomail.binkp import BinkPClient

        client = BinkPClient(host='x', port=1, our_address='1:1/1',
                             hub_address='1:1/2', password='')
        client._sock = None

        class _Item:
            id = 1
            filename = 'test.zip'
            peer_address = '1:1/2'

        with tempfile.TemporaryDirectory() as tmpdir:
            binary_path = os.path.join(tmpdir, 'test.zip')
            with open(binary_path, 'wb') as f:
                f.write(b'x')
            _Item.binary_path = binary_path

            with patch.object(client, '_send_cmd'), \
                 patch.object(client, '_send_data'), \
                 patch.object(client, '_wait_got', return_value=False):
                sent_ids, failures = client._send_hatch([_Item()])

        self.assertEqual(sent_ids, [])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0].id, 1)
        self.assertIn("didn't ack", failures[0][1])


if __name__ == '__main__':
    unittest.main()
