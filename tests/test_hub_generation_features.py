"""Tests for the hub "Generation & Distribution" admin UI feature:

- nodelist generation -> published into ANN.FILES.NODELIST, replacing
  the previous copy
- ScheduledEvent for nodelist generation only seeds on hub installs
  (REGISTRY_MODE_ENABLED)
- files uploaded to a network-attached FileArea auto-queue for TIC
  distribution to subscribed peers; purely local areas don't
- QWK packet preview doesn't consume the node's real high-water mark
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class HubGenerationFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.hub_gen_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app, _create_default_data
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()
            _create_default_data()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_nodelist_event_seeded_on_hub_install(self):
        from anetbbs.models import ScheduledEvent
        with self.app.app_context():
            ev = ScheduledEvent.query.filter_by(
                handler_key='hub_generate_nodelist').first()
            self.assertIsNotNone(ev)
            self.assertTrue(ev.is_enabled)

    def test_write_nodelist_replaces_prior_file(self):
        from anetbbs.models import FileArea
        from anetbbs.echomail.nodelist import write_nodelist_to_area

        with self.app.app_context():
            area = FileArea.query.filter_by(tag='ANN.FILES.NODELIST').first()
            os.makedirs(area.storage_path, exist_ok=True)
            stale_path = os.path.join(area.storage_path, 'NODELIST.001')
            with open(stale_path, 'w') as f:
                f.write('stale')

            write_nodelist_to_area()

            files = os.listdir(area.storage_path)
            nodelist_files = [f for f in files if f.upper().startswith('NODELIST.')]
            self.assertEqual(len(nodelist_files), 1)
            self.assertFalse(os.path.exists(stale_path))

    def test_hatch_local_file_fans_out_to_subscribed_peers(self):
        from anetbbs.models import (db, FileArea, FileEchoSubscription,
                                    HatchQueue)
        from anetbbs.echomail.tic import hatch_local_file

        with self.app.app_context():
            area = FileArea.query.filter_by(tag='ANN.FILES.BBSSOFT').first()
            db.session.add(FileEchoSubscription(
                file_area_id=area.id, peer_address='1200:9/1', is_active=True))
            db.session.add(FileEchoSubscription(
                file_area_id=area.id, peer_address='1200:9/2', is_active=True))
            db.session.commit()

            os.makedirs(area.storage_path, exist_ok=True)
            test_file = os.path.join(area.storage_path, 'unit_test_file.zip')
            with open(test_file, 'wb') as f:
                f.write(b'test binary content')

            queued = hatch_local_file(area, test_file, 'unit_test_file.zip',
                                      'a test file')
            self.assertEqual(queued, 2)

            rows = HatchQueue.query.filter_by(
                file_area_id=area.id, filename='unit_test_file.zip').all()
            self.assertEqual(len(rows), 2)
            peers = {r.peer_address for r in rows}
            self.assertEqual(peers, {'1200:9/1', '1200:9/2'})
            for r in rows:
                self.assertEqual(r.status, 'pending')
                self.assertTrue(r.crc32)
                self.assertEqual(r.size_bytes, len(b'test binary content'))

    def test_upload_to_local_only_area_does_not_hatch(self):
        """A purely local FileArea (network_id is None) has no peers to
        hatch to -- _hatch_if_network_area should be a no-op there."""
        from anetbbs.models import db, FileArea, HatchQueue
        from anetbbs.web.file_areas import _hatch_if_network_area

        with self.app.app_context():
            local_area = FileArea(
                tag='TEST.LOCAL.ONLY', name='Local Only', is_active=True,
                storage_path='/tmp/does-not-matter', network_id=None)
            db.session.add(local_area)
            db.session.commit()

            before = HatchQueue.query.count()
            _hatch_if_network_area(local_area, '/tmp/does-not-matter/x.zip',
                                   'x.zip', 'desc')
            after = HatchQueue.query.count()
            self.assertEqual(before, after)

    def test_upload_route_to_network_area_hatches_automatically(self):
        """Full route-level test: POSTing an upload to an ANN.FILES.* area
        (network-attached) via the actual upload() view creates HatchQueue
        rows for subscribed peers, with zero manual step."""
        from anetbbs.models import (db, FileArea, FileEchoSubscription,
                                    HatchQueue, User)
        import io

        with self.app.app_context():
            area = FileArea.query.filter_by(tag='ANN.FILES.DOORS').first()
            db.session.add(FileEchoSubscription(
                file_area_id=area.id, peer_address='1200:8/1', is_active=True))
            db.session.commit()
            area_id = area.id
            admin = User.query.filter_by(username='admin').first()
            admin_id = admin.id

        self.app.config['WTF_CSRF_ENABLED'] = False
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

        resp = client.post(
            f'/file-areas/{area_id}/upload',
            data={'file': (io.BytesIO(b'route test content'), 'route_test.zip')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            rows = HatchQueue.query.filter_by(
                file_area_id=area_id, filename='route_test.zip').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].peer_address, '1200:8/1')

    def test_qwk_preview_does_not_mark_sent(self):
        """Preview should build a packet without advancing the node's
        high-water mark -- a real subsequent download must still include
        everything the preview showed."""
        from anetbbs.models import (db, QWKNode, QWKNodeLastSent, EchoArea,
                                    EchomailMessage, User)

        with self.app.app_context():
            area = EchoArea.query.filter_by(tag='ANN.GENERAL').first()
            node = QWKNode(packet_id='TESTQWK', name='Test Node',
                          password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(QWKNodeLastSent(
                node_id=node.id, echo_area_id=area.id, conf_number=1,
                last_message_id=None))
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=area.network_id,
                from_name='Tester', to_name='All', subject='Test msg',
                body='hello', direction='inbound'))
            db.session.commit()
            node_id = node.id
            admin = User.query.filter_by(username='admin').first()
            admin_id = admin.id
            hwm_before = QWKNodeLastSent.query.filter_by(
                node_id=node_id, echo_area_id=area.id).first().last_message_id

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

        resp = client.get(f'/admin/echomail/hub/qwk/{node_id}/preview')
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.data), 0)

        with self.app.app_context():
            hwm_after = QWKNodeLastSent.query.filter_by(
                node_id=node_id, echo_area_id=area.id).first().last_message_id
            self.assertEqual(hwm_before, hwm_after)


if __name__ == '__main__':
    unittest.main()
