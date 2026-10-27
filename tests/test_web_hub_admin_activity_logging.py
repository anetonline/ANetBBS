"""Regression test for a gap found in a broader web-blueprint logging
audit (2026-10-26): web/hub_admin.py (the sysop-only hub-peer/config
admin area under /admin/echomail/hub/) never wrote any UserActivity
row for any of its write actions, so an admin's "what did I do this
session" caller-log drill-down showed nothing for hub peer/config
changes even on a long admin session.

Covers every write route this audit added logging to: adding/editing/
deleting a BinkP node, polling a BinkP node, adding/editing/deleting a
QWK node, adding/editing/setting-default/deleting a hub identity,
approving/denying a QWK node request, saving the network-join config,
and manually triggering nodelist generation. Pure read-only admin page
views (index, binkp_nodes, qwk_nodes, hold_queue, previews, etc.) are
deliberately NOT logged -- see the audit's own granularity guidance.

Each test checks the row's activity_type, a recognizable detail, and
(since all these routes go through a real flask_login admin session)
that caller_log_id correlates to the admin's own login session --
the same field web/files.py and web/file_areas.py forgot in an
earlier, already-fixed bug (see
test_web_file_download_activity_correlation.py).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebHubAdminActivityLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                         '.web_hub_admin_activity_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, CallerLog, HubIdentity
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()

            admin = User(username='hubadmin', email='hubadmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

            cl = CallerLog(user_id=admin.id, username='hubadmin', service='web')
            db.session.add(cl)
            db.session.commit()
            cls.caller_log_id = cl.id

            # create_app's startup migration seeds exactly one default
            # HubIdentity (_seed_default_hub_identity in web_app.py) --
            # grab its id, every form here needs a valid hub_identity_id.
            cls.identity_id = HubIdentity.query.filter_by(is_default=True).first().id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
            sess['caller_log_id'] = self.caller_log_id
        return client

    def _latest_activity(self, activity_type):
        from anetbbs.models import UserActivity
        with self.app.app_context():
            return (UserActivity.query
                   .filter_by(user_id=self.admin_id, activity_type=activity_type)
                   .order_by(UserActivity.id.desc()).first())

    def _assert_logged(self, activity_type, expect_substr=None):
        activity = self._latest_activity(activity_type)
        self.assertIsNotNone(activity, f'expected a {activity_type!r} '
                             f'UserActivity row to have been logged')
        self.assertEqual(activity.caller_log_id, self.caller_log_id)
        self.assertEqual(activity.service, 'web')
        if expect_substr:
            self.assertIn(expect_substr, activity.details)
        return activity

    # -- BinkP node management ------------------------------------------

    def test_new_binkp_node_logs_activity(self):
        # hub_identity_id deliberately omitted: BinkPNodeForm's
        # SelectField choices only include ACTIVE HubIdentity rows, and
        # the seeded default identity starts is_active=False (see
        # _seed_default_hub_identity's own comment) -- posting its id
        # explicitly fails "Not a valid choice", while leaving the field
        # out entirely lets _populate_identity_choices()'s own fallback
        # default take over, same as test_hub_node_edit_password.py's
        # existing binkp/new coverage does.
        client = self._admin_client()
        resp = client.post('/admin/echomail/hub/binkp/new', data={
            'name': 'Test BinkP Node', 'ftn_address': '1200:1/99',
            'password': 'secretpw', 'sysop': '', 'system_name': '',
            'location': '', 'email': '', 'phone': '', 'baud': '115200',
            'binkp_port': '24554', 'binkp_host': '', 'notes': '',
            'network_id': '0',
            'is_active': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_binkp_node_added', '1200:1/99')

    def test_edit_binkp_node_logs_activity(self):
        from anetbbs.models import db, BinkPNode
        with self.app.app_context():
            node = BinkPNode(name='EditMe', ftn_address='1200:1/50',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._admin_client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/edit', data={
            'name': 'Edited Name', 'ftn_address': '1200:1/50',
            'password': '', 'sysop': '', 'system_name': '', 'location': '',
            'email': '', 'phone': '', 'baud': '115200',
            'binkp_port': '24554', 'binkp_host': '', 'notes': '',
            'network_id': '0',
            'is_active': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_binkp_node_edited', 'Edited Name')

    def test_delete_binkp_node_logs_activity(self):
        from anetbbs.models import db, BinkPNode
        with self.app.app_context():
            node = BinkPNode(name='DeleteMe', ftn_address='1200:1/51',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._admin_client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_binkp_node_deleted', '1200:1/51')

    def test_poll_binkp_node_logs_activity(self):
        from anetbbs.models import db, BinkPNode
        with self.app.app_context():
            node = BinkPNode(name='PollMe', ftn_address='1200:1/52',
                             password='x', is_active=True,
                             binkp_host='example.test')
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._admin_client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/poll',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_binkp_node_polled', '1200:1/52')

    # -- QWK node management ---------------------------------------------

    def test_new_qwk_node_logs_activity(self):
        # hub_identity_id omitted -- same reasoning as test_new_binkp_node_
        # logs_activity above (QWKNodeForm shares the same SelectField/
        # active-identities-only choices pattern).
        client = self._admin_client()
        resp = client.post('/admin/echomail/hub/qwk/new', data={
            'packet_id': 'NEWQWK', 'name': 'New QWK Node',
            'sysop': '', 'email': '', 'password': 'pw12345', 'notes': '',
            'is_active': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_qwk_node_added', 'NEWQWK')

    def test_edit_qwk_node_logs_activity(self):
        from anetbbs.models import db, QWKNode
        with self.app.app_context():
            node = QWKNode(packet_id='EDITQWK', name='EditMe QWK',
                           password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._admin_client()
        resp = client.post(f'/admin/echomail/hub/qwk/{node_id}/edit', data={
            'packet_id': 'EDITQWK', 'name': 'Edited QWK Name',
            'sysop': '', 'email': '', 'password': '', 'notes': '',
            'is_active': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_qwk_node_edited', 'Edited QWK Name')

    def test_delete_qwk_node_logs_activity(self):
        from anetbbs.models import db, QWKNode
        with self.app.app_context():
            node = QWKNode(packet_id='DELQWK', name='DeleteMe QWK',
                           password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._admin_client()
        resp = client.post(f'/admin/echomail/hub/qwk/{node_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_qwk_node_deleted', 'DELQWK')

    # -- Hub identity management ------------------------------------------

    def test_new_hub_identity_logs_activity(self):
        client = self._admin_client()
        resp = client.post('/admin/echomail/hub/identities/new', data={
            'name': 'Second Identity', 'slug': 'second-identity',
            'binkp_hub_node': '1', 'nodelist_speed': '115200',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_identity_added', 'second-identity')

    def test_edit_hub_identity_logs_activity(self):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity(name='EditIdentity', slug='edit-identity',
                                   binkp_hub_node=1, nodelist_speed=115200,
                                   is_active=True, is_default=False)
            db.session.add(identity)
            db.session.commit()
            identity_id = identity.id

        client = self._admin_client()
        resp = client.post(f'/admin/echomail/hub/identities/{identity_id}/edit', data={
            'name': 'Edited Identity Name', 'slug': 'edit-identity',
            'binkp_hub_node': '1', 'nodelist_speed': '115200',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_identity_edited', 'Edited Identity Name')

    def test_set_default_hub_identity_logs_activity(self):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity(name='SetDefaultMe', slug='set-default-me',
                                   binkp_hub_node=1, nodelist_speed=115200,
                                   is_active=True, is_default=False)
            db.session.add(identity)
            db.session.commit()
            identity_id = identity.id

        client = self._admin_client()
        resp = client.post(
            f'/admin/echomail/hub/identities/{identity_id}/set-default',
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_identity_set_default', 'set-default-me')

    def test_delete_hub_identity_logs_activity(self):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity(name='DeleteIdentity', slug='delete-identity',
                                   binkp_hub_node=1, nodelist_speed=115200,
                                   is_active=True, is_default=False)
            db.session.add(identity)
            db.session.commit()
            identity_id = identity.id

        client = self._admin_client()
        resp = client.post(
            f'/admin/echomail/hub/identities/{identity_id}/delete',
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_identity_deleted', 'delete-identity')

    # -- QWK node self-registration requests ------------------------------

    def test_approve_qwk_request_logs_activity(self):
        from anetbbs.models import db, QWKNodeRequest
        with self.app.app_context():
            req = QWKNodeRequest(bbs_name='Applicant BBS', packet_id='APPR1',
                                 status='pending', applied_via='terminal')
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client()
        resp = client.post(
            f'/admin/echomail/hub/qwk/requests/{req_id}/approve',
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_qwk_request_approved', 'APPR1')

    def test_deny_qwk_request_logs_activity(self):
        from anetbbs.models import db, QWKNodeRequest
        with self.app.app_context():
            req = QWKNodeRequest(bbs_name='Denied BBS', packet_id='DENY1',
                                 status='pending', applied_via='terminal')
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client()
        resp = client.post(
            f'/admin/echomail/hub/qwk/requests/{req_id}/deny',
            data={'reason': 'not needed'}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_qwk_request_denied', 'DENY1')

    # -- Network join config ----------------------------------------------

    def test_join_config_save_logs_activity(self):
        client = self._admin_client()
        resp = client.post('/admin/echomail/hub/join/config', data={
            'hub_identity_id': str(self.identity_id),
            'enabled': 'y', 'network_name': 'Test Join Network',
            'intro_text': 'Welcome', 'binkp_zone': '1200', 'binkp_net': '1',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_join_config_changed')

    # -- Nodelist generation ------------------------------------------------

    def test_nodelist_generate_now_logs_activity(self):
        from anetbbs.models import db, ScheduledEvent
        with self.app.app_context():
            if not ScheduledEvent.query.filter_by(
                    handler_key='hub_generate_nodelist').first():
                db.session.add(ScheduledEvent(
                    name='Generate nodelist', handler_key='hub_generate_nodelist',
                    params_json='{}', is_enabled=True))
                db.session.commit()

        client = self._admin_client()
        resp = client.post('/admin/echomail/hub/nodelist/generate-now',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_logged('hub_nodelist_generated')


if __name__ == '__main__':
    unittest.main()
