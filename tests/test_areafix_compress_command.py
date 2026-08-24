"""Regression test: AreaFix's "%COMPRESS GZIP" / "%COMPRESS OFF"
command was documented and parsed but never actually did anything
("no-op (we always send uncompressed bundles)") -- see
anetbbs/echomail/binkp.py's _build_outbound_bundle() for the wire-
format half of this same follow-up ask. This is the per-node
preference toggle half: the command must now flip
BinkPNode.compress_outbound.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AreafixCompressCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.areafix_compress_test.db')
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

    def _make_node(self, address, password='testpw123'):
        from anetbbs.models import db, BinkPNode
        node = BinkPNode(name='CompressTestNode', ftn_address=address,
                         password=password, is_active=True)
        db.session.add(node)
        db.session.commit()
        return node

    def test_compress_gzip_turns_it_on(self):
        from anetbbs.echomail.areafix import _process_node_request
        with self.app.app_context():
            node = self._make_node('9:9/1')
            self.assertFalse(node.compress_outbound)
            response, _log = _process_node_request(
                node, '9:9/1', 'testpw123', '%COMPRESS GZIP\n')
            self.assertTrue(node.compress_outbound)
            self.assertIn('COMPRESS', response.upper())

    def test_compress_off_turns_it_back_off(self):
        from anetbbs.echomail.areafix import _process_node_request
        from anetbbs.models import db
        with self.app.app_context():
            node = self._make_node('9:9/2')
            node.compress_outbound = True
            db.session.commit()
            _process_node_request(node, '9:9/2', 'testpw123', '%COMPRESS OFF\n')
            self.assertFalse(node.compress_outbound)

    def test_bare_compress_defaults_to_on(self):
        # FTS-0006/original AreaFix convention doesn't strictly require
        # an argument -- a bare "%COMPRESS" with no recognized OFF/NONE/NO
        # argument should still turn it on, not silently no-op.
        from anetbbs.echomail.areafix import _process_node_request
        with self.app.app_context():
            node = self._make_node('9:9/3')
            _process_node_request(node, '9:9/3', 'testpw123', '%COMPRESS\n')
            self.assertTrue(node.compress_outbound)

    def test_leaf_side_compress_request_is_a_no_op(self):
        # A leaf polling an upstream hub has no control over what the
        # HUB sends it -- %COMPRESS from a leaf must stay a documented
        # no-op, matching %RESCAN's own leaf-side no-op precedent.
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.areafix import process_request
        with self.app.app_context():
            net = EchomailNetwork(name='CompressLeafNet', network_type='binkp',
                                  our_address='9:9/4', areafix_password='leafpw')
            db.session.add(net)
            db.session.commit()
            response, _log = process_request(net, '9:9/5', 'leafpw', '%COMPRESS GZIP\n')
            self.assertIn('ignored', response.lower())


if __name__ == '__main__':
    unittest.main()
