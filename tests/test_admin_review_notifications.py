"""Regression tests for admin-review notifications, added after Jerry
asked: "on the hub, for any incoming request, I should have a
notification ... I don't know unless I go looking for it." A research
pass confirmed the in-app Notification system (anetbbs/features/notify.py)
already existed with per-kind user preferences, but had zero call sites
for any of: MSP federation registry join requests, QWK node
applications, new users pending NUV approval, or unknown/bad echomail
areas -- a sysop had to manually visit each admin page to discover any
of these.

Fixed by adding a notify_admins(kind, ...) helper (loops every
is_admin=True user through the existing notify()) and wiring it into
all four gaps at their actual creation site.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AdminReviewNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_notify_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_admin(self, username):
        from anetbbs.models import db, User
        admin = User(username=username, is_admin=True,
                    email=f'{username}@example.com')
        admin.set_password('x')
        db.session.add(admin)
        db.session.commit()
        return admin.id

    # --- notify_admins() itself -------------------------------------

    def test_notify_admins_reaches_every_admin_not_just_one(self):
        from anetbbs.models import Notification
        from anetbbs.features.notify import notify_admins

        with self.app.app_context():
            a1 = self._make_admin('adm_one')
            a2 = self._make_admin('adm_two')

            notify_admins('qwk_node_app', title='test', body='b')

            self.assertTrue(Notification.query.filter_by(
                user_id=a1, kind='qwk_node_app').first() is not None)
            self.assertTrue(Notification.query.filter_by(
                user_id=a2, kind='qwk_node_app').first() is not None)

    def test_notify_admins_skips_non_admins(self):
        from anetbbs.models import db, User, Notification
        from anetbbs.features.notify import notify_admins

        with self.app.app_context():
            regular = User(username='regular_user', is_admin=False,
                           email='regular@example.com')
            regular.set_password('x')
            db.session.add(regular)
            db.session.commit()
            regular_id = regular.id

            notify_admins('bad_area', title='t', body='b')

            self.assertIsNone(Notification.query.filter_by(
                user_id=regular_id, kind='bad_area').first())

    # --- QWK node application (API route) -----------------------------

    def test_qwk_apply_api_notifies_admins(self):
        from anetbbs.models import Notification

        with self.app.app_context():
            admin_id = self._make_admin('adm_qwkapi')

        client = self.app.test_client()
        resp = client.post('/qwkhub/apply', json={
            'bbs_name': 'Test Peer BBS', 'packet_id': 'TESTPEER',
            'sysop_name': 'Someone', 'email': 'peer@example.com',
        })
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            n = Notification.query.filter_by(
                user_id=admin_id, kind='qwk_node_app').first()
            self.assertIsNotNone(n)
            self.assertIn('TESTPEER', n.title)

    # --- MSP registry join request (verify step) -----------------------

    def test_msp_registry_verify_notifies_admins(self):
        from anetbbs.models import db, RegistryEntry, Notification
        import secrets

        with self.app.app_context():
            admin_id = self._make_admin('adm_msp')
            token = secrets.token_urlsafe(24)
            entry = RegistryEntry(
                host='peer.example.com', msp_port=18, systat_port=11,
                name='Peer BBS', sysop='Someone',
                contact_email='peer@example.com',
                registration_token=token,
                is_verified=False, is_approved=False, is_listed=False,
                source_ip='127.0.0.1',
            )
            db.session.add(entry)
            db.session.commit()

        client = self.app.test_client()
        resp = client.get(f'/registry/verify/{token}')
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            n = Notification.query.filter_by(
                user_id=admin_id, kind='msp_join_request').first()
            self.assertIsNotNone(n)
            self.assertIn('peer.example.com', n.title)

    def test_msp_registry_initial_unverified_register_does_not_notify(self):
        """The /register step alone (before the registrant proves they
        control the contact email) is not yet actionable for the sysop
        -- notifying here would fire for every drive-by/bogus submission,
        not just real ones. Confirmed no notification at this stage."""
        from anetbbs.models import Notification

        with self.app.app_context():
            admin_id = self._make_admin('adm_msp_early')

        client = self.app.test_client()
        client.post('/registry/api/v1/register', json={
            'host': 'unverified-peer.example.com', 'name': 'Peer',
            'contact_email': 'peer2@example.com',
        })

        with self.app.app_context():
            n = Notification.query.filter_by(
                user_id=admin_id, kind='msp_join_request').first()
            self.assertIsNone(n)

    # --- NUV pending user ------------------------------------------------

    def test_nuv_registration_notifies_admins(self):
        from anetbbs.models import Notification

        with self.app.app_context():
            admin_id = self._make_admin('adm_nuv')
            self.app.config['NUV_ENABLED'] = True

        try:
            from anetbbs.models import SECURITY_QUESTIONS

            client = self.app.test_client()
            client.get('/auth/register')  # populate CSRF-exempt session state if needed
            resp = client.post('/auth/register', data={
                'username': 'newbie_user',
                'email': 'newbie@example.com',
                'password': 'CorrectHorseBattery9!',
                'password2': 'CorrectHorseBattery9!',
                'question_1': SECURITY_QUESTIONS[0], 'answer_1': 'Rex',
                'question_2': SECURITY_QUESTIONS[1], 'answer_2': 'Springfield',
                'question_3': SECURITY_QUESTIONS[2], 'answer_3': 'Smith',
            }, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)

            with self.app.app_context():
                n = Notification.query.filter_by(
                    user_id=admin_id, kind='nuv_pending').first()
                self.assertIsNotNone(n)
                self.assertIn('newbie_user', n.title)
        finally:
            self.app.config['NUV_ENABLED'] = False

    # --- Bad area log ------------------------------------------------

    def test_bad_area_notifies_admins_once_not_per_message(self):
        from anetbbs.models import db, EchomailNetwork, Notification
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            admin_id = self._make_admin('adm_badarea')
            net = EchomailNetwork(name='TestBadAreaNet', network_type='binkp')
            db.session.add(net)
            db.session.commit()

            for i in range(3):
                _import_message(net, {
                    'area_tag': 'UNKNOWN.AREA',
                    'from_name': f'Someone{i}',
                    'subject': f'Test {i}',
                    'body': 'body text',
                    'msg_id': f'<msg{i}@test>',
                })

            notes = Notification.query.filter_by(
                user_id=admin_id, kind='bad_area').all()
            self.assertEqual(len(notes), 1,
                             'bad_area notification should fire once per '
                             'newly-discovered area, not once per message')

    # --- Network join application (5th kind, added with the public
    # "apply to join this network" feature) --------------------------

    def test_network_join_app_notifies_admins(self):
        from anetbbs.models import db, NetworkJoinConfig, Notification

        with self.app.app_context():
            admin_id = self._make_admin('adm_netjoin')
            cfg = NetworkJoinConfig.get()
            cfg.enabled = True
            cfg.network_name = 'TestJoinNet'
            db.session.commit()

        client = self.app.test_client()
        get_resp = client.get('/join/')
        import re
        token = re.search(r'name="csrf_token" value="([^"]+)"',
                          get_resp.get_data(as_text=True))
        resp = client.post('/join/', data={
            'csrf_token': token.group(1) if token else '',
            'name': 'NotifyTester', 'bbs_name': 'NotifyTestBBS',
            'email': 'notify@example.com', 'binkp_ftn_address': '1:1/888',
            'rules_ack': 'y',
        }, headers={'X-Forwarded-For': '203.0.113.10'})
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            n = Notification.query.filter_by(
                user_id=admin_id, kind='network_join_app').first()
            self.assertIsNotNone(n)
            self.assertIn('NotifyTestBBS', n.title)

    def test_network_join_app_honors_admin_opt_out(self):
        from anetbbs.models import db, User, NetworkJoinConfig, Notification
        import json as _json

        with self.app.app_context():
            admin = User(username='adm_netjoin_optout', is_admin=True,
                        email='adm_netjoin_optout@example.com')
            admin.set_password('x')
            admin.notify_prefs = _json.dumps({'network_join_app': False})
            db.session.add(admin)
            db.session.commit()
            admin_id = admin.id
            cfg = NetworkJoinConfig.get()
            cfg.enabled = True
            db.session.commit()

        client = self.app.test_client()
        get_resp = client.get('/join/')
        import re
        token = re.search(r'name="csrf_token" value="([^"]+)"',
                          get_resp.get_data(as_text=True))
        client.post('/join/', data={
            'csrf_token': token.group(1) if token else '',
            'name': 'OptOutTester', 'bbs_name': 'OptOutTestBBS',
            'email': 'optout@example.com', 'binkp_ftn_address': '1:1/889',
            'rules_ack': 'y',
        }, headers={'X-Forwarded-For': '203.0.113.20'})

        with self.app.app_context():
            n = Notification.query.filter_by(
                user_id=admin_id, kind='network_join_app').first()
            self.assertIsNone(n, 'admin opted out of this kind -- should not notify')


if __name__ == '__main__':
    unittest.main()
