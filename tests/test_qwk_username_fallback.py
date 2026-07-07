"""Regression test: QWK poll used the qwk_username field alone for the
FTP login, with no fallback -- for a QNET-FTP-style hub (like
ANotherNetwork), the login username IS the packet id, and a sysop who
filled in Packet ID but left QWK Username blank (a real, live-caught
mistake -- the admin form's old label didn't explain the two fields
needed to match) would get a silent, always-failing FTP login with no
useful error message.

Fixed with a defensive fallback: if qwk_username is blank, use
qwk_packet_id instead. A blank username always failed anyway, so this
can only help, never regress an already-working config.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class QwkUsernameFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_username_fallback_test.db')
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

    def _make_network(self, **overrides):
        from anetbbs.models import db, EchomailNetwork
        defaults = dict(
            name='ANotherNetwork (QWK)', network_type='qwk',
            qwk_host='bbs.a-net.fyi', qwk_port=21,
            qwk_username=None, qwk_password='hunter2',
            qwk_packet_id='TESTPI', qwk_hub_id='ANET',
        )
        defaults.update(overrides)
        with self.app.app_context():
            net = EchomailNetwork(**defaults)
            db.session.add(net)
            db.session.commit()
            return net.id

    def test_blank_username_falls_back_to_packet_id(self):
        from anetbbs.models import EchomailNetwork
        from anetbbs.echomail.poller import _run_client

        net_id = self._make_network(qwk_username=None)

        with self.app.app_context():
            net = EchomailNetwork.query.get(net_id)
            captured = {}

            class _FakeQWKClient:
                def __init__(self, **kwargs):
                    captured.update(kwargs)

                def poll(self, *a, **kw):
                    return {'received': [], 'sent': 0}

            with patch('anetbbs.echomail.qwk.QWKClient', _FakeQWKClient):
                _run_client(net, [], self.app)

        self.assertEqual(captured.get('username'), 'TESTPI')

    def test_explicit_username_is_not_overridden(self):
        from anetbbs.models import EchomailNetwork
        from anetbbs.echomail.poller import _run_client

        net_id = self._make_network(qwk_username='REALUSER')

        with self.app.app_context():
            net = EchomailNetwork.query.get(net_id)
            captured = {}

            class _FakeQWKClient:
                def __init__(self, **kwargs):
                    captured.update(kwargs)

                def poll(self, *a, **kw):
                    return {'received': [], 'sent': 0}

            with patch('anetbbs.echomail.qwk.QWKClient', _FakeQWKClient):
                _run_client(net, [], self.app)

        self.assertEqual(captured.get('username'), 'REALUSER')


if __name__ == '__main__':
    unittest.main()
