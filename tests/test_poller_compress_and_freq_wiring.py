"""Regression tests for poller.py's wiring of two new BinkPClient
options -- compress_outbound and freq_requests -- into both dial-out
directions (_run_client: leaf dialing its upstream hub; _run_node_client:
hub dialing a downstream node), plus FreqRequest status updates after
a poll (_mark_freq_requests_sent).

Mirrors the exact harness pattern test_hold_flavor_delivery.py already
established for verifying what gets passed into BinkPClient's
constructor without opening a real socket.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PollerCompressAndFreqWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poller_compress_freq_test.db')
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

    def _make_network(self, name, our='1200:2/1', hub='1200:2/2', compress=False):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(
            name=name, network_type='binkp',
            our_address=our, hub_address=hub, is_active=True,
            compress_outbound=compress)
        db.session.add(net)
        db.session.commit()
        return net

    def test_run_client_passes_network_compress_flag_to_client(self):
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        with self.app.app_context():
            net = self._make_network('CompressOnNet', compress=True)
            captured = {}

            class _FakeClient:
                def __init__(self, **kw):
                    captured.update(kw)
                def poll(self, outbound_messages=None, data_dir=None, hatch_items=None):
                    return {'sent': 0, 'received': [], 'freq_sent_ids': [], 'freq_failed_ids': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller_mod._run_client(net, [], self.app)
            self.assertTrue(captured['compress_outbound'])

    def test_run_client_passes_false_when_network_compress_is_off(self):
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        with self.app.app_context():
            net = self._make_network('CompressOffNet', compress=False)
            captured = {}

            class _FakeClient:
                def __init__(self, **kw):
                    captured.update(kw)
                def poll(self, outbound_messages=None, data_dir=None, hatch_items=None):
                    return {'sent': 0, 'received': [], 'freq_sent_ids': [], 'freq_failed_ids': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller_mod._run_client(net, [], self.app)
            self.assertFalse(captured['compress_outbound'])

    def test_run_node_client_passes_node_compress_flag_not_network(self):
        # Compression is a per-node preference (matches AreaFix's own
        # per-peer %COMPRESS scope) -- the node's own flag must be used
        # here, not the network's (which is deliberately left False).
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, BinkPNode
        with self.app.app_context():
            net = self._make_network('NodeCompressNet', compress=False)
            node = BinkPNode(name='CompressNode', ftn_address='1200:2/50',
                             password='x', network_id=net.id,
                             binkp_host='node.example.test',
                             compress_outbound=True)
            db.session.add(node)
            db.session.commit()
            captured = {}

            class _FakeClient:
                def __init__(self, **kw):
                    captured.update(kw)
                def poll(self, outbound_messages=None, data_dir=None, hatch_items=None):
                    return {'sent': 0, 'received': [], 'freq_sent_ids': [], 'freq_failed_ids': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller_mod._run_node_client(node, net, [], self.app)
            self.assertTrue(captured['compress_outbound'])

    def test_run_client_gathers_pending_freq_requests_for_the_hub(self):
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, FreqRequest
        with self.app.app_context():
            net = self._make_network('FreqGatherNet', hub='1200:2/99')
            other = FreqRequest(target_address='9:9/9', filename_pattern='not-this.zip')
            mine = FreqRequest(target_address='1200:2/99', filename_pattern='wanted.zip')
            db.session.add_all([other, mine])
            db.session.commit()
            captured = {}

            class _FakeClient:
                def __init__(self, **kw):
                    captured.update(kw)
                def poll(self, outbound_messages=None, data_dir=None, hatch_items=None):
                    return {'sent': 0, 'received': [], 'freq_sent_ids': [], 'freq_failed_ids': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller_mod._run_client(net, [], self.app)
            patterns = {r.filename_pattern for r in captured['freq_requests']}
            self.assertEqual(patterns, {'wanted.zip'})

    def test_successful_freq_send_marks_request_sent(self):
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, FreqRequest
        with self.app.app_context():
            net = self._make_network('FreqSentNet', hub='1200:2/100')
            req = FreqRequest(target_address='1200:2/100', filename_pattern='get.zip')
            db.session.add(req)
            db.session.commit()
            req_id = req.id

            class _FakeClient:
                def __init__(self, **kw): pass
                def poll(self, outbound_messages=None, data_dir=None, hatch_items=None):
                    return {'sent': 0, 'received': [],
                           'freq_sent_ids': [req_id], 'freq_failed_ids': []}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller_mod._run_client(net, [], self.app)
            self.assertEqual(FreqRequest.query.get(req_id).status, 'sent')

    def test_failed_freq_send_marks_request_failed_with_reason(self):
        from unittest.mock import patch
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, FreqRequest
        with self.app.app_context():
            net = self._make_network('FreqFailNet', hub='1200:2/101')
            req = FreqRequest(target_address='1200:2/101', filename_pattern='get2.zip')
            db.session.add(req)
            db.session.commit()
            req_id = req.id

            class _FakeClient:
                def __init__(self, **kw): pass
                def poll(self, outbound_messages=None, data_dir=None, hatch_items=None):
                    return {'sent': 0, 'received': [],
                           'freq_sent_ids': [], 'freq_failed_ids': [req_id]}

            with patch('anetbbs.echomail.binkp.BinkPClient', _FakeClient):
                poller_mod._run_client(net, [], self.app)
            row = FreqRequest.query.get(req_id)
            self.assertEqual(row.status, 'failed')
            self.assertTrue(row.error_message)


if __name__ == '__main__':
    unittest.main()
