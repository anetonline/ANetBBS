"""Regression test for a gap found in a broader web-blueprint logging
audit (2026-10-26): web/qwk_hub.py's two machine-facing hub routes
(GET /qwkhub/<id>.qwk and POST /qwkhub/<id>.rep) never wrote any
UserActivity row at all, so a sysop investigating a downstream node's
traffic through the admin activity feed found nothing.

These routes authenticate via HTTP Basic Auth against QWKNode (a
downstream BBS, machine-to-machine), not via a logged-in web User --
there is no flask_login session and no CallerLog row for this traffic.
So the logged UserActivity rows use user_id=None, the documented "anon
event" case (see UserActivity.user_id's own comment in models.py), and
naturally get caller_log_id=None too (web_log_activity() still fills
in ip_address/user_agent/service='web' from the real request).
"""
import base64
import io
import os
import struct
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _basic_auth_header(username, password):
    token = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return {'Authorization': f'Basic {token}'}


def _build_rep_zip(hub_id, to_name, subject, body, conf_num):
    """Minimal single-message REP zip -- same shape as
    test_qwk_rep_msgid_dedup.py's _build_rep_zip()."""
    BLOCK = 128
    buf = io.BytesIO()
    buf.write(b' ' * BLOCK)
    body_bytes = body.encode('latin-1', errors='replace')
    remainder = len(body_bytes) % BLOCK
    if remainder:
        body_bytes += b' ' * (BLOCK - remainder)
    num_blocks = 1 + len(body_bytes) // BLOCK
    header = bytearray(b' ' * BLOCK)
    header[8:21] = b'01-01-2026'.ljust(13)[:13]
    header[21:46] = to_name.encode('cp437')[:25].ljust(25)
    header[46:71] = b'Tester'.ljust(25)[:25]
    header[71:96] = subject.encode('cp437')[:25].ljust(25)
    header[116:122] = f'{num_blocks:<6d}'.encode()[:6]
    struct.pack_into('<H', header, 123, conf_num)
    buf.write(bytes(header))
    buf.write(body_bytes)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'{hub_id}.MSG', buf.getvalue())
    return zbuf.getvalue()


class WebQwkHubActivityLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                         '.web_qwk_hub_activity_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, QWKNode, EchomailNetwork, EchoArea, QWKNodeLastSent
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            db.session.add(QWKNode(packet_id='ACTNODE', name='Activity Test Node',
                                   password='nodepw', is_active=True))
            db.session.commit()

            net = EchomailNetwork(name='QwkActivityNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='1', name='ActivityArea',
                            is_active=True)
            db.session.add(area)
            db.session.flush()
            node = QWKNode.query.filter_by(packet_id='ACTNODE').first()
            db.session.add(QWKNodeLastSent(node_id=node.id, echo_area_id=area.id,
                                           conf_number=1))
            db.session.commit()

            from anetbbs.echomail.qwk_hub_ftp import resolve_hub_id
            cls.hub_id = resolve_hub_id(node)

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_download_qwk_logs_qwk_pack_downloaded_activity(self):
        from anetbbs.models import UserActivity
        client = self.app.test_client()
        resp = client.get('/qwkhub/ACTNODE.qwk',
                          headers=_basic_auth_header('ACTNODE', 'nodepw'))
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(activity_type='qwk_pack_downloaded')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'a QWK packet download must log a '
                'qwk_pack_downloaded UserActivity row')
            self.assertIsNone(activity.user_id,
                              'machine-to-machine QWK hub traffic has no '
                              'logged-in web user -- user_id must be None')
            self.assertIsNone(activity.caller_log_id,
                              'no CallerLog session exists for this '
                              'traffic -- caller_log_id must be None')
            self.assertIn('ACTNODE', activity.details)
            self.assertEqual(activity.service, 'web')

    def test_upload_rep_logs_qwk_rep_uploaded_activity(self):
        from anetbbs.models import UserActivity
        client = self.app.test_client()
        rep_bytes = _build_rep_zip(self.hub_id, 'All', 'Activity log test',
                                   'Body text for the activity log test.', 1)
        resp = client.post('/qwkhub/ACTNODE.rep',
                           data=rep_bytes,
                           content_type='application/octet-stream',
                           headers=_basic_auth_header('ACTNODE', 'nodepw'))
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(activity_type='qwk_rep_uploaded')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'a REP upload must log a qwk_rep_uploaded '
                'UserActivity row')
            self.assertIsNone(activity.user_id)
            self.assertIsNone(activity.caller_log_id)
            self.assertIn('ACTNODE', activity.details)
            self.assertIn('1', activity.details)


if __name__ == '__main__':
    unittest.main()
