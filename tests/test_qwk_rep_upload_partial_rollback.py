"""Regression test for a real bug found in a full echomail-subsystem
audit: process_rep_upload()'s per-message exception handler used to
call a bare db.session.rollback() on failure -- which rolls back the
ENTIRE open transaction, not just the one failing message. Any messages
already add()+flush()ed earlier in the SAME upload (their ids already
captured, count already incremented) were silently discarded the
moment ANY later message in the same batch hit an exception, while the
final "imported %d messages" log line kept reporting the pre-rollback
count -- a sysop had no way to know some messages from a multi-message
REP upload had vanished.

Fixed by wrapping each message's insert in its own SAVEPOINT
(db.session.begin_nested()), the same pattern poller.py's own
_import_message() already uses for the identical class of bug -- a
rollback now only undoes the ONE failing message, not the whole batch.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeMsg:
    def __init__(self, id_, subject, body, to_name, from_name, conf_num):
        self.id = id_
        self.subject = subject
        self.body = body
        self.to_name = to_name
        self.from_name = from_name
        self.direction = 'outbound'
        self._qwk_conf_num = conf_num
        self.msg_id = None


class QwkRepUploadPartialRollbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_rep_partial_rollback_test.db')
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

    def test_earlier_message_survives_a_later_messages_failure(self):
        from anetbbs.models import (db, EchomailNetwork, EchoArea, QWKNode,
                                    QWKNodeLastSent, EchomailMessage)
        from anetbbs.echomail.qwk import _build_rep_packet
        from anetbbs.echomail import qwk_hub_ftp
        import tempfile

        with self.app.app_context():
            net = EchomailNetwork(name='RepPartialRollbackNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='77', name='Test Area', is_active=True)
            db.session.add(area)
            node = QWKNode(packet_id='PARTFAIL', name='Partial Fail Node',
                           password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            sub = QWKNodeLastSent(node_id=node.id, echo_area_id=area.id, conf_number=77)
            db.session.add(sub)
            db.session.commit()
            node_id = node.id
            area_id = area.id

            # Two messages in ONE upload: the first should succeed, the
            # second is engineered to fail during import.
            fake_msgs = [
                _FakeMsg(1, 'First message survives', 'Body one', 'All',
                        'StingRay', conf_num=77),
                _FakeMsg(2, 'Second message fails', 'Body two', 'All',
                        'StingRay', conf_num=77),
            ]
            rep_bytes = _build_rep_packet(fake_msgs, 'PARTFAIL', hub_id='ANET')

        # Make the SECOND EchomailMessage construction raise, simulating
        # a real per-row failure (constraint violation, encoding issue,
        # etc.) -- the first message's own insert must be unaffected.
        # process_rep_upload() does `from ..models import ... EchomailMessage`
        # freshly inside the function on every call, so the patch target
        # is the real anetbbs.models.EchomailMessage (what that local
        # import binds to), not qwk_hub_ftp's own namespace. A plain
        # stand-in function won't do any more -- process_rep_upload() now
        # also does a msg_id-dedup EchomailMessage.query.filter_by(...)
        # lookup BEFORE construction (see test_qwk_rep_msgid_dedup.py),
        # so the replacement must still expose a real `.query`. __new__
        # returning an already-fully-built real instance means Python
        # won't re-run __init__ on it (the returned object isn't an
        # instance of this stand-in class).
        import anetbbs.models as models_mod
        real_cls = models_mod.EchomailMessage
        call_count = {'n': 0}

        class _QueryProxy:
            # `.query` must resolve LAZILY -- real_cls.query itself needs
            # an active app_context (Flask-SQLAlchemy's scoped session),
            # which doesn't exist yet at this class body's definition
            # time; process_rep_upload() opens its own app_context
            # internally, by which point this descriptor's __get__ runs.
            def __get__(self, obj, owner):
                return real_cls.query

        class _FlakyEchomailMessage:
            query = _QueryProxy()

            def __new__(cls, *args, **kwargs):
                call_count['n'] += 1
                if call_count['n'] == 2:
                    raise ValueError('simulated per-message import failure')
                return real_cls(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            rep_path = os.path.join(tmpdir, 'PARTFAIL.rep')
            with open(rep_path, 'wb') as f:
                f.write(rep_bytes)

            with patch.object(models_mod, 'EchomailMessage', _FlakyEchomailMessage):
                count = qwk_hub_ftp.process_rep_upload(node_id, rep_path, self.app)

        # Only the first message actually persisted -- count must
        # reflect reality, not overstate what's in the database.
        self.assertEqual(count, 1)

        with self.app.app_context():
            msgs = EchomailMessage.query.filter_by(area_id=area_id).all()
            self.assertEqual(len(msgs), 1,
                             'the first message must survive the second '
                             'message\'s failure, not be rolled back with it')
            self.assertEqual(msgs[0].subject, 'First message survives')


if __name__ == '__main__':
    unittest.main()
