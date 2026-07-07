"""Regression test for a live-caught path-doubling bug in the FTP
server's QWK node authentication.

`qwk_root` is set to `<DATA_DIR>/qwk-hub` when `AnetbbsAuthorizer` is
constructed (see `run()` in anetbbs/ftp/server.py). But
`validate_authentication()` used to pass that already-suffixed path
into `ensure_node_dir()`, which appends its OWN 'qwk-hub' segment and
expects the bare DATA_DIR instead -- doubling the path to
`<DATA_DIR>/qwk-hub/qwk-hub/<PACKET_ID>`.

Meanwhile `on_login()`'s call to `generate_node_packet()` correctly
receives the bare DATA_DIR and writes to `<DATA_DIR>/qwk-hub/<PACKET_ID>/
<HUB_ID>.qwk`. Net effect, live-caught testing the real ANotherNetwork
QWK flow end-to-end: login always succeeded, packet generation always
"succeeded" (no exception), but the FTP client's session was rooted at
a directory one level away from wherever the packet actually landed --
so `RETR` always failed with "No such file" no matter how correctly
everything else was configured.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class QwkNodeHomeDirTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ftp_qwk_home_test.db')
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

    def test_login_time_home_dir_matches_where_packet_generation_writes(self):
        import tempfile
        from anetbbs.ftp.server import AnetbbsAuthorizer
        from anetbbs.echomail.qwk_hub_ftp import generate_node_packet
        from anetbbs.models import db, QWKNode

        with tempfile.TemporaryDirectory() as data_dir:
            qwk_root = os.path.join(data_dir, 'qwk-hub')
            os.makedirs(qwk_root, exist_ok=True)

            with self.app.app_context():
                node = QWKNode(packet_id='PI3ANET', name='Pi3 Test',
                              password='hunter2', is_active=True)
                db.session.add(node)
                db.session.commit()

                # Exercise generate_node_packet() exactly as on_login() does --
                # passing the BARE data_dir, matching real code.
                written_path = generate_node_packet(node, data_dir, 'ANET')
                self.assertTrue(written_path, 'packet generation reported failure')
                self.assertTrue(os.path.isfile(written_path),
                                'generate_node_packet() did not actually write a file')

            # Now exercise the authorizer's login-time home-dir logic
            # exactly as validate_authentication() does -- constructed
            # with the SAME already-suffixed qwk_root run() passes it.
            authorizer = AnetbbsAuthorizer(
                self.app, anon_root=data_dir, user_root=data_dir,
                admin_root=data_dir, anon_enabled=False, qwk_root=qwk_root)
            handler = MagicMock()

            with self.app.app_context():
                authorizer.validate_authentication('PI3ANET', 'hunter2', handler)

            # add_user() is inherited from DummyAuthorizer -- read back
            # what homedir it was actually registered with.
            registered_home = authorizer.get_home_dir('PI3ANET')

            written_dir = os.path.dirname(written_path)
            self.assertEqual(
                os.path.normpath(registered_home), os.path.normpath(written_dir),
                f'FTP session home dir ({registered_home!r}) does not match '
                f'where the packet was actually written ({written_dir!r}) -- '
                f'this is the exact bug that made RETR always 550 regardless '
                f'of correct credentials/config.')

    def test_no_path_doubling_in_registered_home_dir(self):
        import tempfile
        from anetbbs.ftp.server import AnetbbsAuthorizer
        from anetbbs.models import db, QWKNode

        with tempfile.TemporaryDirectory() as data_dir:
            qwk_root = os.path.join(data_dir, 'qwk-hub')
            os.makedirs(qwk_root, exist_ok=True)

            with self.app.app_context():
                node = QWKNode(packet_id='NODOUBL', name='No Double Test',
                              password='x', is_active=True)
                db.session.add(node)
                db.session.commit()

            authorizer = AnetbbsAuthorizer(
                self.app, anon_root=data_dir, user_root=data_dir,
                admin_root=data_dir, anon_enabled=False, qwk_root=qwk_root)
            handler = MagicMock()

            with self.app.app_context():
                authorizer.validate_authentication('NODOUBL', 'x', handler)

            registered_home = authorizer.get_home_dir('NODOUBL')
            self.assertEqual(registered_home.count('qwk-hub'), 1,
                             f'qwk-hub appears more than once in {registered_home!r}')


if __name__ == '__main__':
    unittest.main()
