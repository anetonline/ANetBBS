"""Regression test for two live-caught bugs in process_rep_upload()
(anetbbs/echomail/qwk_hub_ftp.py) -- both found chasing the same real
incident: a message replied to from a QWK-connected node ("shows sent"
on the sending side) never appeared on the hub.

1. **The real root cause**: `_parse_messages_dat()` (qwk.py) returns
   parsed message dicts keyed 'conf_num'/'from_name'/'to_name', but
   this function read 'conference'/'from'/'to' -- keys that never
   existed in that dict, always silently falling back to their .get()
   defaults. `conference` defaulting to 0 meant every uploaded reply,
   from every QWK node, always resolved to conference 0 (personal
   mail) -- which no node ever has a legitimate subscription to -- so
   the `if sub is None: continue` guard silently dropped every single
   message. This was never specific to one node or one test message;
   it broke inbound REP processing entirely, for as long as this
   function has existed.

2. **A second, unrelated bug found in the same investigation**: a
   trailing `logger.info(...)` call sat OUTSIDE the function's
   `with app.app_context():` block but still referenced
   `node.packet_id` -- an ORM attribute SQLAlchemy expires by default
   after a commit. By the time that line ran, the app context (and
   its session) had already torn down, raising DetachedInstanceError
   and making the caller (anetbbs/ftp/server.py's on_file_received)
   log "REP processing failed" -- misleading, since on a real
   (non-broken) upload the import itself would already have succeeded
   by that point.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeMsg:
    """Minimal stand-in for an EchomailMessage row, matching what
    _build_rep_packet() reads off a real one."""
    def __init__(self, id_, subject, body, to_name, from_name, conf_num):
        self.id = id_
        self.subject = subject
        self.body = body
        self.to_name = to_name
        self.from_name = from_name
        self.direction = 'outbound'
        self._qwk_conf_num = conf_num
        self.msg_id = None


class QwkRepUploadDetachedInstanceFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_rep_upload_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_rep_upload_imports_message_without_raising(self):
        from anetbbs.models import (db, EchomailNetwork, EchoArea, QWKNode,
                                    QWKNodeLastSent, EchomailMessage)
        from anetbbs.echomail.qwk import _build_rep_packet
        from anetbbs.echomail.qwk_hub_ftp import process_rep_upload
        import tempfile

        with self.app.app_context():
            net = EchomailNetwork(name='RepUploadTestNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='99', name='Test Area', is_active=True)
            db.session.add(area)
            node = QWKNode(packet_id='REPTEST', name='Rep Test Node',
                           password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            sub = QWKNodeLastSent(node_id=node.id, echo_area_id=area.id, conf_number=99)
            db.session.add(sub)
            db.session.commit()
            node_id = node.id
            area_id = area.id

            fake_msg = _FakeMsg(1, 'Re: test', 'Hello from the test suite', 'All',
                                'StingRay', conf_num=99)
            rep_bytes = _build_rep_packet([fake_msg], 'REPTEST', hub_id='ANET')

        with tempfile.TemporaryDirectory() as tmpdir:
            rep_path = os.path.join(tmpdir, 'REPTEST.rep')
            with open(rep_path, 'wb') as f:
                f.write(rep_bytes)

            # This must not raise -- the live bug raised DetachedInstanceError
            # here even though the import itself had already succeeded.
            count = process_rep_upload(node_id, rep_path, self.app)

        self.assertEqual(count, 1)

        with self.app.app_context():
            msgs = EchomailMessage.query.filter_by(area_id=area_id).all()
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].subject, 'Re: test')
            self.assertEqual(msgs[0].from_name, 'StingRay')

            refreshed_node = QWKNode.query.get(node_id)
            self.assertIsNotNone(refreshed_node.last_poll_at)


if __name__ == '__main__':
    unittest.main()
