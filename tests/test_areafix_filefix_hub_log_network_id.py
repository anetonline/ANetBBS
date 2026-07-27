"""Regression test: AreaFix/FileFix's hub-side handler
(_process_node_request) never set network_id on any of its log_kwargs
dicts (badpw, help, or the final success return), even though
node.network_id / the network_id parameter was available throughout.
The admin AreaFix Log page filters by network_id
(anetbbs/web/echomail_admin.py's areafix_log() route) -- so every
downstream-node AreaFix/FileFix interaction was invisible whenever a
sysop filtered the audit trail by network, only the leaf-side
(process_request) rows (which DID set it) ever showed up.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import anetbbs.config as cfg_mod


class HubSideLogNetworkIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.areafix_filefix_hub_log_test.db')
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

    def test_areafix_hub_side_badpw_and_success_carry_network_id(self):
        from anetbbs.models import db, EchomailNetwork, BinkPNode, EchoArea
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='HubLogNet', network_type='binkp',
                                  our_address='9:9/1')
            db.session.add(net)
            db.session.flush()
            node = BinkPNode(name='HubLogNode', ftn_address='9:9/2',
                             password='hublogpw', network_id=net.id)
            db.session.add(node)
            db.session.add(EchoArea(tag='HL.AREA', name='HL Area',
                                    network_id=net.id, is_active=True))
            db.session.commit()

            _, badpw_log = _process_node_request(node, '9:9/2', 'wrongpw', '+HL.AREA\n')
            self.assertEqual(badpw_log.get('network_id'), net.id)

            _, ok_log = _process_node_request(node, '9:9/2', 'hublogpw', '+HL.AREA\n')
            self.assertEqual(ok_log.get('network_id'), net.id)

    def test_filefix_hub_side_badpw_and_success_carry_network_id(self):
        from anetbbs.models import db, EchomailNetwork, FileArea
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='HubLogFileNet', network_type='binkp',
                                  our_address='9:9/10')
            db.session.add(net)
            db.session.flush()
            db.session.add(FileArea(tag='HLF.AREA', name='HLF Area',
                                    network_id=net.id, is_active=True))
            db.session.commit()

            _, badpw_log = _process_node_request(
                '9:9/11', '9:9/11', 'wrongpw', '+HLF.AREA\n', 'realpw', net.id)
            self.assertEqual(badpw_log.get('network_id'), net.id)

            _, ok_log = _process_node_request(
                '9:9/11', '9:9/11', 'realpw', '+HLF.AREA\n', 'realpw', net.id)
            self.assertEqual(ok_log.get('network_id'), net.id)


if __name__ == '__main__':
    unittest.main()
