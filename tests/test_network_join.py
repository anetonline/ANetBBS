"""Tests for the public "apply to join this network" feature
(anetbbs/web/network_join.py, anetbbs/echomail/network_join.py, and the
Join Form tab + review queue in anetbbs/web/hub_admin.py).

Built after Jerry asked for a public web form where anyone can apply to
join ANotherNetwork -- read the rules, download the infopack, check a
box confirming they read the rules, submit an application generic
across both BinkP and QWK transports (a real applicant might want
either or both), configurable per-hub-install so any sysop running
their own ANetBBS hub could enable it and upload their own infopack.
"""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

REAL_INFOPACK = Path('/media/jerry/EXT HDD/AIANETBBS/annetnet.zip')


def _make_test_zip(path, members):
    """members: dict of {filename: content_str}"""
    with zipfile.ZipFile(path, 'w') as zf:
        for name, content in members.items():
            zf.writestr(name, content)


class NetworkJoinZipHelperTests(unittest.TestCase):
    """Unit tests for anetbbs/echomail/network_join.py -- no Flask app
    needed, pure zipfile logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_list_txt_members_sorted_largest_first(self):
        from anetbbs.echomail.network_join import list_txt_members
        zpath = os.path.join(self.tmpdir, 'test.zip')
        _make_test_zip(zpath, {
            'small.txt': 'x' * 10,
            'big.txt': 'x' * 1000,
            'medium.txt': 'x' * 100,
            'art.ans': 'not a txt file',
        })
        members = list_txt_members(zpath)
        self.assertEqual([m[0] for m in members], ['big.txt', 'medium.txt', 'small.txt'])

    def test_list_txt_members_empty_for_non_zip(self):
        from anetbbs.echomail.network_join import list_txt_members
        bad_path = os.path.join(self.tmpdir, 'not_a_zip.txt')
        with open(bad_path, 'w') as f:
            f.write('this is not a zip file')
        self.assertEqual(list_txt_members(bad_path), [])

    def test_auto_pick_largest(self):
        from anetbbs.echomail.network_join import auto_pick_rules_member
        zpath = os.path.join(self.tmpdir, 'test.zip')
        _make_test_zip(zpath, {'small.txt': 'x' * 10, 'big.txt': 'x' * 1000})
        self.assertEqual(auto_pick_rules_member(zpath), 'big.txt')

    def test_auto_pick_none_when_no_txt_files(self):
        from anetbbs.echomail.network_join import auto_pick_rules_member
        zpath = os.path.join(self.tmpdir, 'test.zip')
        _make_test_zip(zpath, {'art.ans': 'no text files here'})
        self.assertIsNone(auto_pick_rules_member(zpath))

    def test_extract_member_roundtrip(self):
        from anetbbs.echomail.network_join import extract_member_text
        zpath = os.path.join(self.tmpdir, 'test.zip')
        _make_test_zip(zpath, {'rules.txt': 'Be nice to each other.'})
        self.assertEqual(extract_member_text(zpath, 'rules.txt'),
                         'Be nice to each other.')

    def test_extract_missing_member_returns_none(self):
        from anetbbs.echomail.network_join import extract_member_text
        zpath = os.path.join(self.tmpdir, 'test.zip')
        _make_test_zip(zpath, {'rules.txt': 'content'})
        self.assertIsNone(extract_member_text(zpath, 'does_not_exist.txt'))

    def test_extract_non_zip_does_not_raise(self):
        from anetbbs.echomail.network_join import extract_member_text
        bad_path = os.path.join(self.tmpdir, 'not_a_zip.txt')
        with open(bad_path, 'w') as f:
            f.write('not a zip')
        self.assertIsNone(extract_member_text(bad_path, 'anything.txt'))

    def test_extract_cp437_fallback_decodes_non_utf8(self):
        from anetbbs.echomail.network_join import extract_member_text
        zpath = os.path.join(self.tmpdir, 'test.zip')
        # A byte sequence that is invalid UTF-8 but valid CP437 (0x80-0xFF
        # box-drawing / extended chars).
        cp437_bytes = bytes([0xC9, 0xCD, 0xCD, 0xBB])  # box-drawing corner chars
        with zipfile.ZipFile(zpath, 'w') as zf:
            zf.writestr('art.txt', cp437_bytes)
        text = extract_member_text(zpath, 'art.txt')
        self.assertIsNotNone(text)
        self.assertEqual(len(text), 4)

    @unittest.skipUnless(REAL_INFOPACK.is_file(), 'real annetnet.zip not present')
    def test_real_infopack_picks_annet_txt(self):
        from anetbbs.echomail.network_join import auto_pick_rules_member, extract_member_text
        picked = auto_pick_rules_member(str(REAL_INFOPACK))
        self.assertEqual(picked, 'annet.txt')
        text = extract_member_text(str(REAL_INFOPACK), picked)
        self.assertIn('ANotherNetwork', text)


class NetworkJoinModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.network_join_model_test.db')
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

    def test_config_singleton_creates_once_reuses_after(self):
        from anetbbs.models import NetworkJoinConfig
        with self.app.app_context():
            c1 = NetworkJoinConfig.get()
            c1.network_name = 'FirstCall'
            from anetbbs.models import db
            db.session.commit()
            c2 = NetworkJoinConfig.get()
            self.assertEqual(c1.id, c2.id)
            self.assertEqual(c2.network_name, 'FirstCall')

    def test_request_partial_fields_binkp_only(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='TestBBS', email='test@example.com',
                binkp_ftn_address='1:1/999', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            self.assertIsNone(req.qwk_packet_id)
            self.assertEqual(req.binkp_ftn_address, '1:1/999')

    def test_request_partial_fields_qwk_only(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='TestBBS2', email='test2@example.com',
                qwk_packet_id='TESTQWK', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            self.assertIsNone(req.binkp_ftn_address)
            self.assertEqual(req.qwk_packet_id, 'TESTQWK')


class NetworkJoinRouteTests(unittest.TestCase):
    """Full route tests -- public form, admin config/upload, approval flow."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.network_join_route_test.db')
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

    def _admin_client(self, username='route_admin'):
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

    def _enable_join_form(self):
        from anetbbs.models import db, NetworkJoinConfig
        with self.app.app_context():
            cfg = NetworkJoinConfig.get()
            cfg.enabled = True
            cfg.network_name = 'TestNet'
            cfg.rules_text = 'Rules: be excellent to each other.'
            db.session.commit()

    def test_join_form_404_without_hub_mode(self):
        self._enable_join_form()
        self.app.config['REGISTRY_MODE_ENABLED'] = False
        try:
            client = self.app.test_client()
            resp = client.get('/join/')
            self.assertEqual(resp.status_code, 404)
        finally:
            self.app.config['REGISTRY_MODE_ENABLED'] = True

    def test_join_form_404_when_disabled(self):
        from anetbbs.models import db, NetworkJoinConfig
        with self.app.app_context():
            cfg = NetworkJoinConfig.get()
            cfg.enabled = False
            db.session.commit()
        client = self.app.test_client()
        resp = client.get('/join/')
        self.assertEqual(resp.status_code, 404)

    def test_join_form_get_shows_rules_and_download_link(self):
        self._enable_join_form()
        client = self.app.test_client()
        resp = client.get('/join/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('be excellent to each other', body)

    def test_post_missing_rules_ack_rejected(self):
        self._enable_join_form()
        client = self.app.test_client()
        get_resp = client.get('/join/')
        import re
        token = re.search(r'name="csrf_token" value="([^"]+)"', get_resp.get_data(as_text=True))
        from anetbbs.models import NetworkJoinRequest
        before = NetworkJoinRequest.query.count() if False else None
        with self.app.app_context():
            before = NetworkJoinRequest.query.count()
        resp = client.post('/join/', data={
            'csrf_token': token.group(1) if token else '',
            'name': 'Someone', 'bbs_name': 'SomeBBS', 'email': 'x@example.com',
            'binkp_ftn_address': '1:1/111',
            # rules_ack intentionally omitted
        })
        self.assertEqual(resp.status_code, 200)  # re-renders form with errors
        with self.app.app_context():
            after = NetworkJoinRequest.query.count()
        self.assertEqual(before, after, 'request should not be created without rules_ack')

    def test_post_no_transport_selected_rejected(self):
        self._enable_join_form()
        client = self.app.test_client()
        get_resp = client.get('/join/')
        import re
        token = re.search(r'name="csrf_token" value="([^"]+)"', get_resp.get_data(as_text=True))
        from anetbbs.models import NetworkJoinRequest
        with self.app.app_context():
            before = NetworkJoinRequest.query.count()
        resp = client.post('/join/', data={
            'csrf_token': token.group(1) if token else '',
            'name': 'Someone', 'bbs_name': 'SomeBBS', 'email': 'x@example.com',
            'rules_ack': 'y',
            # neither binkp_ftn_address nor qwk_packet_id filled in
        })
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            after = NetworkJoinRequest.query.count()
        self.assertEqual(before, after, 'request should not be created with no transport')

    def test_post_valid_binkp_only_creates_pending_request(self):
        self._enable_join_form()
        client = self.app.test_client()
        get_resp = client.get('/join/')
        import re
        token = re.search(r'name="csrf_token" value="([^"]+)"', get_resp.get_data(as_text=True))
        resp = client.post('/join/', data={
            'csrf_token': token.group(1) if token else '',
            'name': 'ValidApplicant', 'bbs_name': 'ValidBBS',
            'email': 'valid@example.com',
            'binkp_ftn_address': '1:1/222',
            'rules_ack': 'y',
        })
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import NetworkJoinRequest
            req = NetworkJoinRequest.query.filter_by(bbs_name='ValidBBS').first()
            self.assertIsNotNone(req)
            self.assertEqual(req.status, 'pending')
            self.assertTrue(req.rules_ack)

    def test_post_without_csrf_token_rejected(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        try:
            self._enable_join_form()
            client = self.app.test_client()
            with self.app.app_context():
                from anetbbs.models import NetworkJoinRequest
                before = NetworkJoinRequest.query.filter_by(bbs_name='NoCsrfBBS').count()
            resp = client.post('/join/', data={
                'name': 'NoCsrf', 'bbs_name': 'NoCsrfBBS',
                'email': 'nocsrf@example.com', 'binkp_ftn_address': '1:1/333',
                'rules_ack': 'y',
            })
            with self.app.app_context():
                after = NetworkJoinRequest.query.filter_by(bbs_name='NoCsrfBBS').count()
            self.assertEqual(before, after, 'request should not be created without CSRF token')
        finally:
            self.app.config['WTF_CSRF_ENABLED'] = False

    def test_infopack_download_no_login_required(self):
        from anetbbs.models import db, NetworkJoinConfig
        zpath = os.path.join(self.join_dir, 'dl_test.zip')
        _make_test_zip(zpath, {'rules.txt': 'test rules'})
        with self.app.app_context():
            cfg = NetworkJoinConfig.get()
            cfg.enabled = True
            cfg.infopack_filename = 'dl_test.zip'
            cfg.infopack_original_filename = 'infopack.zip'
            db.session.commit()
        client = self.app.test_client()  # no login
        resp = client.get('/join/infopack.zip')
        self.assertEqual(resp.status_code, 200)

    def test_infopack_404_when_disabled(self):
        from anetbbs.models import db, NetworkJoinConfig
        with self.app.app_context():
            cfg = NetworkJoinConfig.get()
            cfg.enabled = False
            db.session.commit()
        client = self.app.test_client()
        resp = client.get('/join/infopack.zip')
        self.assertEqual(resp.status_code, 404)

    def test_admin_routes_require_admin_login(self):
        client = self.app.test_client()  # not logged in at all
        resp = client.get('/admin/echomail/hub/join/requests')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_nav_link_only_shown_when_logged_in_and_enabled(self):
        """Jerry asked how anyone would discover /join/ -- it wasn't
        linked from anywhere. Fixed by adding a Tools-dropdown nav link,
        deliberately logged-in-only (Jerry's explicit choice, not shown
        to anonymous visitors) and only rendered when the feature is
        actually reachable (hub mode + enabled), via a context processor
        (_inject_network_join_enabled in anetbbs/web_app.py).

        The Tools dropdown was later reorganized into category landing
        pages (main.py's TOOLS_HUB_SECTIONS / tools_hub()) -- same real
        problem as the Admin dropdown had, a flat list that grew too
        long. "Join Our Network" now lives under Tools > Community
        (/tools/community) instead of directly in the top navbar, but
        the same three gating rules must still hold at its new home."""
        from anetbbs.models import db, User, NetworkJoinConfig
        with self.app.app_context():
            u = User.query.filter_by(username='navlinktest').first()
            if not u:
                u = User(username='navlinktest', is_admin=False,
                        email='navlinktest@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id

        self._enable_join_form()
        client = self.app.test_client()

        # Logged out -- must not show even though the feature is enabled
        # (community hub page requires login, same as before).
        resp = client.get('/tools/community')
        self.assertNotIn('Join Our Network', resp.get_data(as_text=True))

        # Logged in, enabled -- must show.
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        resp = client.get('/tools/community')
        self.assertIn('Join Our Network', resp.get_data(as_text=True))

        # Logged in, disabled -- must not show.
        with self.app.app_context():
            cfg = NetworkJoinConfig.get()
            cfg.enabled = False
            db.session.commit()
        resp = client.get('/tools/community')
        self.assertNotIn('Join Our Network', resp.get_data(as_text=True))

    def test_approve_binkp_only_creates_one_node(self):
        from anetbbs.models import db, NetworkJoinRequest, BinkPNode
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='ApproveBinkpBBS', name='Approver', email='a@example.com',
                binkp_ftn_address='1:1/444', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('approver1')
        resp = client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'approved')
            self.assertIsNotNone(req.binkp_node_id)
            self.assertIsNone(req.qwk_node_id)
            self.assertIsNotNone(req.generated_binkp_password)
            node = BinkPNode.query.get(req.binkp_node_id)
            self.assertEqual(node.ftn_address, '1:1/444')

    def test_approve_qwk_only_creates_one_node(self):
        from anetbbs.models import db, NetworkJoinRequest, QWKNode
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='ApproveQwkBBS', name='Approver', email='a2@example.com',
                qwk_packet_id='APRVQWK', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('approver2')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)
        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'approved')
            self.assertIsNone(req.binkp_node_id)
            self.assertIsNotNone(req.qwk_node_id)
            node = QWKNode.query.get(req.qwk_node_id)
            self.assertEqual(node.packet_id, 'APRVQWK')

    def test_approve_both_creates_two_nodes(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='ApproveBothBBS', name='Approver', email='a3@example.com',
                binkp_ftn_address='1:1/555', qwk_packet_id='BOTHQWK', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('approver3')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)
        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'approved')
            self.assertIsNotNone(req.binkp_node_id)
            self.assertIsNotNone(req.qwk_node_id)
            self.assertIsNotNone(req.generated_binkp_password)
            self.assertIsNotNone(req.generated_qwk_password)
            self.assertNotEqual(req.generated_binkp_password, req.generated_qwk_password)

    def test_approve_collision_rolls_back_partial_creation(self):
        from anetbbs.models import db, NetworkJoinRequest, BinkPNode
        with self.app.app_context():
            # Pre-existing node that collides with the QWK packet_id below.
            from anetbbs.models import QWKNode
            db.session.add(QWKNode(packet_id='TAKEN123', name='Existing',
                                   password='x', is_active=True))
            db.session.commit()

            req = NetworkJoinRequest(
                bbs_name='CollisionBBS', name='Approver', email='a4@example.com',
                binkp_ftn_address='1:1/666', qwk_packet_id='TAKEN123', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('approver4')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)
        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            # Should still be pending -- the whole approval was rejected
            # up front since QWK packet_id collides, before any node
            # was created.
            self.assertEqual(req.status, 'pending')
            self.assertIsNone(req.binkp_node_id)
            binkp_node = BinkPNode.query.filter_by(ftn_address='1:1/666').first()
            self.assertIsNone(binkp_node, 'BinkP node should not exist -- '
                              'whole approval was rejected due to QWK collision')

    def test_next_binkp_node_address_starts_at_2_when_none_exist(self):
        from anetbbs.web.hub_admin import _next_binkp_node_address
        with self.app.app_context():
            self.assertEqual(_next_binkp_node_address(1200, 42), '1200:42/2')

    def test_next_binkp_node_address_takes_max_plus_one(self):
        from anetbbs.models import db, BinkPNode
        from anetbbs.web.hub_admin import _next_binkp_node_address
        with self.app.app_context():
            db.session.add(BinkPNode(name='A', ftn_address='1200:43/2',
                                     password='x', is_active=True))
            db.session.add(BinkPNode(name='B', ftn_address='1200:43/5',
                                     password='x', is_active=True))
            db.session.commit()
            self.assertEqual(_next_binkp_node_address(1200, 43), '1200:43/6')

    def test_next_binkp_node_address_ignores_other_zone_net_and_nonnumeric(self):
        from anetbbs.models import db, BinkPNode
        from anetbbs.web.hub_admin import _next_binkp_node_address
        with self.app.app_context():
            db.session.add(BinkPNode(name='OtherZone', ftn_address='2:44/99',
                                     password='x', is_active=True))
            db.session.add(BinkPNode(name='Domain', ftn_address='some.domain.bbs',
                                     password='x', is_active=True))
            db.session.commit()
            # Neither the other zone:net node nor the non-numeric address
            # should influence numbering for zone 1200 net 44 -- still
            # starts fresh at 2.
            self.assertEqual(_next_binkp_node_address(1200, 44), '1200:44/2')

    def test_approve_with_zone_net_configured_auto_assigns_address(self):
        from anetbbs.models import db, NetworkJoinRequest, NetworkJoinConfig, BinkPNode
        with self.app.app_context():
            cfg = NetworkJoinConfig.get()
            cfg.binkp_zone = 1200
            cfg.binkp_net = 50
            db.session.commit()

            # Applicant proposes an address that should be IGNORED in
            # favor of the auto-assigned one.
            req = NetworkJoinRequest(
                bbs_name='AutoNumberedBBS', name='Auto', email='auto@example.com',
                binkp_ftn_address='9:9/9999', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('approver_autonum')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)
        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'approved')
            node = BinkPNode.query.get(req.binkp_node_id)
            self.assertEqual(node.ftn_address, '1200:50/2')
            self.assertNotEqual(node.ftn_address, req.binkp_ftn_address)
            # Reset for other tests in this class that assume unset zone/net.
            cfg = NetworkJoinConfig.get()
            cfg.binkp_zone = None
            cfg.binkp_net = None
            db.session.commit()

    def test_join_request_detail_shows_full_fields(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='DetailViewBBS', name='Detail Person',
                email='detail@example.com', bbs_software='Synchronet',
                telnet_address='detail.example.com:23',
                notes='Some notes only visible on the detail page.',
                binkp_ftn_address='1:1/888', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('detail_viewer')
        resp = client.get(f'/admin/echomail/hub/join/requests/{req_id}')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('Synchronet', body)
        self.assertIn('detail.example.com:23', body)
        self.assertIn('Some notes only visible on the detail page.', body)

    def test_join_requests_list_links_to_detail_view(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='LinkedFromListBBS', name='Linker',
                email='linker@example.com', binkp_ftn_address='1:1/889',
                rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('list_linker')
        resp = client.get('/admin/echomail/hub/join/requests')
        body = resp.get_data(as_text=True)
        self.assertIn(f'/admin/echomail/hub/join/requests/{req_id}', body)

    def test_reviewed_table_shows_generated_password(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='PasswordShownBBS', name='Pwd', email='pwd@example.com',
                binkp_ftn_address='1:1/890', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('pwd_shower')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)
        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            pwd = req.generated_binkp_password
        resp = client.get('/admin/echomail/hub/join/requests')
        self.assertIn(pwd, resp.get_data(as_text=True))

    def test_archive_writes_json_file_not_web_reachable(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='ArchiveMeBBS', name='Archiver',
                email='archiver@example.com', binkp_ftn_address='1:1/891',
                notes='Archive this note.', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('archiver1')
        resp = client.post(f'/admin/echomail/hub/join/requests/{req_id}/archive',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            from anetbbs.web.hub_admin import _join_archive_dir
            archive_dir = _join_archive_dir()
        files = [f for f in os.listdir(archive_dir) if f.startswith(f'{req_id:05d}_')]
        self.assertEqual(len(files), 1)
        import json
        with open(os.path.join(archive_dir, files[0])) as f:
            payload = json.load(f)
        self.assertEqual(payload['applicant']['bbs_name'], 'ArchiveMeBBS')
        self.assertEqual(payload['notes'], 'Archive this note.')

        # Not reachable from the web -- no route serves this directory.
        anon_resp = self.app.test_client().get(
            f'/network_join_archive/{files[0]}')
        self.assertEqual(anon_resp.status_code, 404)

    def test_deny_sets_reason_no_nodes_created(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                bbs_name='DenyBBS', name='Denier', email='d@example.com',
                binkp_ftn_address='1:1/777', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('denier1')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/deny',
                    data={'reason': 'Address already in use elsewhere'},
                    follow_redirects=True)
        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'denied')
            self.assertEqual(req.deny_reason, 'Address already in use elsewhere')
            self.assertIsNone(req.binkp_node_id)


if __name__ == '__main__':
    unittest.main()
