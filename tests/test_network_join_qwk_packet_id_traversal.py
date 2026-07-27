"""Regression test for a CRITICAL finding from a full FTP-server
security audit: the public, unauthenticated network-join application
form's qwk_packet_id field (anetbbs/web/network_join.py's
JoinApplicationForm) only validated Length(max=8) -- no charset check --
unlike QWKNodeForm (anetbbs/web/hub_admin.py, admin-only) and the
self-service QWK apply API (web/qwk_hub.py), both of which already
regex-validate against ^[A-Za-z0-9]+$ specifically because packet_id
flows straight into a filesystem path: AnetbbsAuthorizer.
validate_authentication() (anetbbs/ftp/server.py) builds the FTP
session's per-node home dir as
os.path.join(self.qwk_root, node.packet_id.upper()).

"../../.." is exactly 8 characters and passed the old Length(max=8)-only
check cleanly. Once a sysop approved such a request (hub_admin.py's
approve_join_request()), that string became a real QWKNode.packet_id and,
on the node's next FTP login, its session root -- escaping
data/qwk-hub/ outward with full elradfmw (read/write/delete/rename/
mkdir) permission.

Two independent fixes, both exercised here:
1. JoinApplicationForm.qwk_packet_id now has the same Regexp validator
   as QWKNodeForm -- rejects the submission outright.
2. approve_join_request() now re-validates server-side too (defense in
   depth, matching approve_qwk_request()'s existing precedent for the
   sibling QWKNodeRequest flow), in case a bad value ever reaches a
   pending row by some other path (e.g. direct DB manipulation, a future
   API).
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class QwkPacketIdTraversalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_pid_traversal_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        cls.join_dir = tempfile.mkdtemp()
        cfg_mod.TestingConfig.NETWORK_JOIN_DIR = cls.join_dir
        cls.join_archive_dir = tempfile.mkdtemp()
        cfg_mod.TestingConfig.NETWORK_JOIN_ARCHIVE_DIR = cls.join_archive_dir

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        cls.app.config['NETWORK_JOIN_DIR'] = cls.join_dir
        cls.app.config['NETWORK_JOIN_ARCHIVE_DIR'] = cls.join_archive_dir
        with cls.app.app_context():
            db.create_all()
            from anetbbs.models import NetworkJoinConfig
            cfg = NetworkJoinConfig.get()
            cfg.enabled = True
            cfg.network_name = 'TraversalTestNet'
            cfg.rules_text = 'Be excellent.'
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)
        import shutil
        shutil.rmtree(cls.join_dir, ignore_errors=True)
        shutil.rmtree(cls.join_archive_dir, ignore_errors=True)

    def _admin_client(self, username):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=True,
                        email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def _csrf_token(self, client):
        get_resp = client.get('/join/')
        m = re.search(r'name="csrf_token" value="([^"]+)"',
                      get_resp.get_data(as_text=True))
        return m.group(1) if m else ''

    # -- fix 1: form-level rejection ---------------------------------------

    def test_path_traversal_packet_id_rejected_at_form_level(self):
        from anetbbs.models import NetworkJoinRequest
        client = self.app.test_client()
        token = self._csrf_token(client)
        with self.app.app_context():
            before = NetworkJoinRequest.query.count()
        resp = client.post('/join/', data={
            'csrf_token': token,
            'name': 'Attacker', 'bbs_name': 'EvilBBS',
            'email': 'evil@example.com',
            'qwk_packet_id': '../../..',
            'rules_ack': 'y',
        })
        self.assertEqual(resp.status_code, 200)  # re-renders with errors
        with self.app.app_context():
            after = NetworkJoinRequest.query.count()
        self.assertEqual(before, after,
                         'a packet_id with path-traversal chars must be '
                         'rejected before a request row is even created')

    def test_ordinary_alnum_packet_id_still_accepted(self):
        from anetbbs.models import NetworkJoinRequest
        client = self.app.test_client()
        token = self._csrf_token(client)
        resp = client.post('/join/', data={
            'csrf_token': token,
            'name': 'Normal', 'bbs_name': 'NormalBBS',
            'email': 'normal@example.com',
            'qwk_packet_id': 'NORMAL1',
            'rules_ack': 'y',
        })
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            req = NetworkJoinRequest.query.filter_by(bbs_name='NormalBBS').first()
            self.assertIsNotNone(req)
            self.assertEqual(req.qwk_packet_id, 'NORMAL1')

    # -- fix 2: server-side re-check in approve_join_request() -------------

    def test_approve_rejects_traversal_packet_id_even_if_it_reached_a_row(self):
        """Simulates a bad value reaching a pending row by some other
        path than the (now-fixed) public form -- the approval handler
        itself must still refuse to ever create a QWKNode from it."""
        from anetbbs.models import db, NetworkJoinRequest, QWKNode
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='SneakBBS', name='Sneak', email='sneak@example.com',
                qwk_packet_id='../../..', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('traversal_admin')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'pending',
                             'approval must be refused, not silently applied')
            self.assertIsNone(req.qwk_node_id)
            self.assertIsNone(
                QWKNode.query.filter(
                    QWKNode.packet_id.in_(['../../..', '..', '.'])).first(),
                'no QWKNode must ever be created from an invalid packet_id')

    def test_approve_normalizes_case_before_creating_node(self):
        """Defense-in-depth check uppercases before validating/creating,
        matching approve_qwk_request()'s existing behavior."""
        from anetbbs.models import db, NetworkJoinRequest, QWKNode
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='LowerBBS', name='Lower', email='lower@example.com',
                qwk_packet_id='lowerid', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('traversal_admin2')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'approved')
            node = QWKNode.query.get(req.qwk_node_id)
            self.assertEqual(node.packet_id, 'LOWERID')


if __name__ == '__main__':
    unittest.main()
