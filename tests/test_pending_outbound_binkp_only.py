"""Test for the "Pending Outbound" / "Echo out (queue)" counter fix.

Live-caught: this counter (EchomailMessage.direction='outbound',
sent_at=None) climbed forever on a real hub regardless of whether
anything was actually stuck, because QWK delivery is tracked via a
per-node high-water mark (QWKNodeLastSent) instead of sent_at, and
QWK's outbound path never sets sent_at at all -- every QWK message
ever sent stays sent_at=None permanently. Scoped to BinkP-only, since
that's the only transport where sent_at actually means "not yet
delivered."
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PendingOutboundBinkpOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.pending_outbound_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        from anetbbs.models import User, db
        with self.app.app_context():
            admin = User.query.filter_by(username='pendingoutboundtest').first()
            if not admin:
                admin = User(username='pendingoutboundtest', is_admin=True, access_level=255,
                            email='pendingoutboundtest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_dashboard_pending_outbound_excludes_qwk(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage
        import re

        client = self._client()

        # Baseline: whatever the dashboard already reports before we add
        # anything (other tests / seed data may have left rows behind).
        resp = client.get('/admin/echomail/')
        self.assertEqual(resp.status_code, 200)
        before = int(re.search(
            r'([\d,]+)\s*</div>\s*<div>Pending Outbound',
            resp.get_data(as_text=True)).group(1).replace(',', ''))

        with self.app.app_context():
            binkp_net = EchomailNetwork(name='PendingOutboundBinkpNet', network_type='binkp')
            qwk_net = EchomailNetwork(name='PendingOutboundQWKNet', network_type='qwk')
            db.session.add_all([binkp_net, qwk_net])
            db.session.flush()
            binkp_area = EchoArea(network_id=binkp_net.id, tag='FIDO.TEST', name='B', is_active=True)
            qwk_area = EchoArea(network_id=qwk_net.id, tag='500', name='Q', is_active=True)
            db.session.add_all([binkp_area, qwk_area])
            db.session.flush()

            db.session.add(EchomailMessage(
                area_id=binkp_area.id, network_id=binkp_net.id,
                from_name='Test', to_name='All', subject='Binkp pending',
                body='x', direction='outbound', sent_at=None))
            # QWK messages never get sent_at set at all -- this row
            # mirrors the real shape of every QWK outbound message ever
            # sent, which is exactly what made this counter climb
            # forever on the live hub.
            db.session.add(EchomailMessage(
                area_id=qwk_area.id, network_id=qwk_net.id,
                from_name='Test', to_name='All', subject='QWK never-sent',
                body='x', direction='outbound', sent_at=None))
            db.session.commit()

        resp = client.get('/admin/echomail/')
        self.assertEqual(resp.status_code, 200)
        after = int(re.search(
            r'([\d,]+)\s*</div>\s*<div>Pending Outbound',
            resp.get_data(as_text=True)).group(1).replace(',', ''))

        # Only the BinkP message should have moved the needle -- the
        # QWK one must not be counted at all.
        self.assertEqual(after, before + 1)


if __name__ == '__main__':
    unittest.main()
