"""Regression test for a real bug found in a full echomail-subsystem
audit: anetbbs/web/qwk_user.py's upload() route (the per-user QWK REP
uploader, distinct from qwk_hub_ftp.py's hub-side REP importer and
from qwk_hub.py's) used a bare db.session.rollback() on a per-message
exception -- the exact same anti-pattern already found and fixed once
in qwk_hub_ftp.py's process_rep_upload() (see
test_qwk_rep_upload_partial_rollback.py). A bare rollback() discards
the ENTIRE open transaction, not just the failing message, so any
message already db.session.add()ed earlier in the SAME upload loop
gets silently dropped the moment a LATER message in the same packet
raises, while `imported` still reports success for all of them.

Fixed with the same db.session.begin_nested() SAVEPOINT isolation
pattern used everywhere else this bug class has been found.
"""
import io
import os
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class QwkUserRepUploadPartialRollbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_user_rep_partial_rollback_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchoArea, EchomailNetwork
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            user = User(username='reptest', email='reptest@example.com',
                       password_hash='x', access_level=100)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id
            net = EchomailNetwork(name='RepUploadTestNet', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            # conf_num in the legacy positional scheme this route uses is
            # a 1-based index into EchoArea.query.filter_by(is_active=True)
            # -- find our own test area's real position rather than
            # assuming an empty DB (create_app() auto-seeds default areas).
            area = EchoArea(network_id=net.id, tag='REPUP', name='Rep Upload Test',
                            is_active=True, min_access_level=0)
            db.session.add(area)
            db.session.commit()
            areas = EchoArea.query.filter_by(is_active=True).all()
            cls.conf_num = next(i for i, a in enumerate(areas, 1) if a.id == area.id)
            cls.area_id = area.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _build_rep_bytes(self, messages):
        """Build a minimal REP-format MESSAGES.DAT + zip wrapper matching
        what qwk_user.py's upload() parses: 128-byte welcome block, then
        one 128-byte header + body-blocks per message, header bytes
        123:125 = struct '<H' conf_num, 116:122 = ASCII block count."""
        import zipfile
        BLOCK = 128
        buf = io.BytesIO()
        buf.write(b' ' * BLOCK)  # welcome block
        for to_name, subject, body, conf_num in messages:
            body_bytes = body.encode('latin-1', errors='replace')
            remainder = len(body_bytes) % BLOCK
            if remainder:
                body_bytes += b' ' * (BLOCK - remainder)
            num_blocks = 1 + len(body_bytes) // BLOCK
            header = bytearray(b' ' * BLOCK)
            header[21:46] = to_name.encode('cp437')[:25].ljust(25)
            header[71:96] = subject.encode('cp437')[:25].ljust(25)
            header[116:122] = f'{num_blocks:<6d}'.encode()[:6]
            struct.pack_into('<H', header, 123, conf_num)
            buf.write(bytes(header))
            buf.write(body_bytes)
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('REPTEST.MSG', buf.getvalue())
        return zbuf.getvalue()

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_earlier_message_survives_a_later_messages_failure(self):
        from anetbbs.models import EchomailMessage

        rep_bytes = self._build_rep_bytes([
            ('All', 'First message survives', 'Body one', self.conf_num),
            ('All', 'Second message fails', 'Body two', self.conf_num),
        ])

        import anetbbs.web.qwk_user as qwk_user_mod
        real_cls = qwk_user_mod.EchomailMessage
        call_count = {'n': 0}

        def _flaky_echomail_message(*args, **kwargs):
            call_count['n'] += 1
            if call_count['n'] == 2:
                raise ValueError('simulated per-message import failure')
            return real_cls(*args, **kwargs)

        client = self._client_as(self.user_id)
        with patch.object(qwk_user_mod, 'EchomailMessage', _flaky_echomail_message):
            resp = client.post('/qwk/upload', data={
                'rep': (io.BytesIO(rep_bytes), 'REPTEST.rep'),
            }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            msgs = EchomailMessage.query.filter_by(area_id=self.area_id).all()
            self.assertEqual(len(msgs), 1,
                             'the first message must survive the second '
                             'message\'s failure, not be rolled back with it')
            self.assertEqual(msgs[0].subject, 'First message survives')


if __name__ == '__main__':
    unittest.main()
