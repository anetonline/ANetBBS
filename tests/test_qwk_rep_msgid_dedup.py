"""Regression test: neither QWK hub REP importer (web/qwk_hub.py's
import_rep_packet, echomail/qwk_hub_ftp.py's process_rep_upload) ever
deduplicated by msg_id before inserting, unlike the BinkP import paths
(poller.py's _import_message(), binkp_server.py's _import_pkt_payload()),
which both check for an existing (msg_id, area_id) row first. A node
re-uploading the same REP (a retried request after a dropped
connection, a client resubmission after a timed-out ack) duplicated
every message in it.

_parse_messages_dat() (echomail/qwk.py) already synthesizes a
deterministic content-hash msg_id when no @MSGID: kludge is present in
the body -- the same message content always hashes the same -- so
uploading byte-identical REP content twice is exactly the scenario this
covers.
"""
import io
import os
import struct
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _build_rep_zip(hub_id, messages):
    """messages: list of (to_name, subject, body, conf_num)."""
    BLOCK = 128
    buf = io.BytesIO()
    buf.write(b' ' * BLOCK)
    for to_name, subject, body, conf_num in messages:
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


class QwkRepMsgidDedupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_rep_msgid_dedup_test.db')
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

    def test_http_hub_rep_reupload_does_not_duplicate(self):
        from anetbbs.models import (db, QWKNode, EchomailNetwork, EchoArea,
                                    QWKNodeLastSent, EchomailMessage)
        from anetbbs.web.qwk_hub import import_rep_packet, resolve_hub_id

        with self.app.app_context():
            net = EchomailNetwork(name='RepDedupHttpNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='DEDUP1', name='Dedup1',
                            is_active=True)
            db.session.add(area)
            db.session.flush()
            node = QWKNode(packet_id='DEDUPND', name='DedupNode', password='x',
                           is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(QWKNodeLastSent(node_id=node.id, echo_area_id=area.id,
                                           conf_number=1))
            db.session.commit()

            hub_id = resolve_hub_id(node)
            rep_bytes = _build_rep_zip(hub_id, [
                ('All', 'Reupload test', 'Same content every time', 1)])

            first = import_rep_packet(node, rep_bytes)
            second = import_rep_packet(node, rep_bytes)

            self.assertEqual(first, 1, 'first upload must import the message')
            self.assertEqual(second, 0,
                             'identical REP content re-uploaded must not '
                             'duplicate the message')
            self.assertEqual(
                EchomailMessage.query.filter_by(area_id=area.id).count(), 1)

    def test_ftp_hub_rep_reupload_does_not_duplicate(self):
        import tempfile
        from anetbbs.models import (db, QWKNode, EchomailNetwork, EchoArea,
                                    QWKNodeLastSent, EchomailMessage)
        from anetbbs.echomail.qwk_hub_ftp import process_rep_upload

        with self.app.app_context():
            net = EchomailNetwork(name='RepDedupFtpNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='DEDUP2', name='Dedup2',
                            is_active=True)
            db.session.add(area)
            db.session.flush()
            node = QWKNode(packet_id='DEDUPFT', name='DedupFtpNode', password='x',
                           is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(QWKNodeLastSent(node_id=node.id, echo_area_id=area.id,
                                           conf_number=1))
            db.session.commit()
            node_id = node.id
            area_id = area.id

        rep_bytes = _build_rep_zip('ANET', [
            ('All', 'FTP reupload test', 'Same FTP content every time', 1)])

        with tempfile.TemporaryDirectory() as tmpdir:
            rep_path = os.path.join(tmpdir, 'test.rep')
            with open(rep_path, 'wb') as f:
                f.write(rep_bytes)

            first = process_rep_upload(node_id, rep_path, self.app)
            second = process_rep_upload(node_id, rep_path, self.app)

        with self.app.app_context():
            self.assertEqual(first, 1)
            self.assertEqual(second, 0,
                             'identical REP content re-uploaded via FTP must '
                             'not duplicate the message')
            self.assertEqual(
                EchomailMessage.query.filter_by(area_id=area_id).count(), 1)


if __name__ == '__main__':
    unittest.main()
