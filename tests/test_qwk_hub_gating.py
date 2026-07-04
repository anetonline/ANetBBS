"""Regression tests for the ANotherNetwork QWK hub gating fix, added
2026-07-04.

Jerry reported: "we added ANotherNetwork QWK and echomail. evidentally
it did not get wired into my BBS being the hub -- bbs.a-net.fyi ...
all the sysops try to put in for a node and it goes to their system."

Root cause: the terminal "Apply for ANotherNetwork QWK node" wizard
(anetbbs/features/bbs_ui.py's _apply_qwk_node) wrote a QWKNodeRequest
straight into whichever BBS install happened to run it -- there was no
network call to the real hub at all. Worse, the admin review UI
(anetbbs/web/hub_admin.py's hub_admin_bp) was registered on every
install with no gate, so every sysop's own admin panel showed the same
"Node Requests" queue as if THEY were the hub.

Fixed by:
  1. Gating the entire hub_admin_bp blueprint behind
     REGISTRY_MODE_ENABLED (the existing "this install IS the hub"
     flag already used by the MSP federation registry) via a
     blueprint-wide before_request hook.
  2. Adding a real hub-side API (anetbbs/web/qwk_hub.py:
     POST /qwkhub/apply, GET /qwkhub/status/<token>), gated the same
     way, so the terminal wizard on a PEER install can actually POST
     its application to the real hub instead of writing locally.

These tests cover the hub-side API and the blueprint gating with a
Flask test client. The terminal wizard's client-side HTTP-calling
logic (bbs_ui.py) was verified by manual code review; it isn't covered
here since it requires mocking BBSSession I/O together with requests,
which isn't a good fit for this test file's scope.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _make_app(db_path, registry_mode_enabled):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'

    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['REGISTRY_MODE_ENABLED'] = registry_mode_enabled
    return app


class HubAdminBlueprintGatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()

    @classmethod
    def tearDownClass(cls):
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_node_requests_page_404s_when_not_the_hub(self):
        app = _make_app(str(Path(self._tmp.name) / 'a.db'), registry_mode_enabled=False)
        client = app.test_client()
        resp = client.get('/admin/echomail/hub/qwk/requests')
        self.assertEqual(resp.status_code, 404)

    def test_nodelist_404s_when_not_the_hub(self):
        # The whole blueprint is gated, not just the review-queue routes.
        app = _make_app(str(Path(self._tmp.name) / 'b.db'), registry_mode_enabled=False)
        client = app.test_client()
        resp = client.get('/admin/echomail/hub/nodelist')
        self.assertEqual(resp.status_code, 404)

    def test_node_requests_page_does_not_404_when_hub_mode_enabled(self):
        # Should hit the @login_required check instead (302 redirect to
        # login, or 401/403) -- specifically NOT 404, since 404 would
        # mean the hub-mode gate is (wrongly) still blocking it.
        app = _make_app(str(Path(self._tmp.name) / 'c.db'), registry_mode_enabled=True)
        client = app.test_client()
        resp = client.get('/admin/echomail/hub/qwk/requests')
        self.assertNotEqual(resp.status_code, 404)

    def _login_as_admin(self, app, client):
        """Bypass the real login form (rate limiting, CSRF) by injecting
        Flask-Login's session keys directly -- a standard technique for
        testing @login_required routes."""
        from anetbbs.models import db, User
        with app.app_context():
            admin = User.query.filter_by(username='admin').first()
            if admin is None:
                admin = User(username='admin', email='admin@example.com', is_admin=True)
                admin.set_password('password123')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

    def test_hub_management_card_hidden_on_non_hub_install(self):
        # Since the entire hub_admin_bp blueprint 404s on non-hub
        # installs, a nav card linking to it would be a dead link --
        # confirm hub() filters it out of the Network section.
        app = _make_app(str(Path(self._tmp.name) / 'k.db'), registry_mode_enabled=False)
        client = app.test_client()
        self._login_as_admin(app, client)
        resp = client.get('/admin/hub/network')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'Hub Management', resp.data)
        self.assertIn(b'Register with Hub', resp.data)

    def test_hub_management_card_shown_on_hub_install(self):
        app = _make_app(str(Path(self._tmp.name) / 'l.db'), registry_mode_enabled=True)
        client = app.test_client()
        self._login_as_admin(app, client)
        resp = client.get('/admin/hub/network')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Hub Management', resp.data)
        self.assertIn(b'Register with Hub', resp.data)


class QwkHubApplyApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()

    @classmethod
    def tearDownClass(cls):
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_apply_404s_on_a_non_hub_install(self):
        app = _make_app(str(Path(self._tmp.name) / 'd.db'), registry_mode_enabled=False)
        client = app.test_client()
        resp = client.post('/qwkhub/apply', json={
            'bbs_name': 'Some Other BBS', 'packet_id': 'OTHER',
        })
        self.assertEqual(resp.status_code, 404)

    def test_apply_succeeds_on_the_hub_and_returns_a_token(self):
        app = _make_app(str(Path(self._tmp.name) / 'e.db'), registry_mode_enabled=True)
        client = app.test_client()
        resp = client.post('/qwkhub/apply', json={
            'bbs_name': 'Some Other BBS', 'packet_id': 'OTHER',
            'sysop_name': 'Some Sysop', 'email': 'sysop@example.com',
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['ok'])
        self.assertTrue(body['request_token'])

        from anetbbs.models import QWKNodeRequest
        with app.app_context():
            req = QWKNodeRequest.query.filter_by(packet_id='OTHER').first()
            self.assertIsNotNone(req)
            self.assertEqual(req.request_token, body['request_token'])
            self.assertEqual(req.applied_via, 'api')
            self.assertEqual(req.status, 'pending')

    def test_apply_rejects_a_taken_packet_id(self):
        app = _make_app(str(Path(self._tmp.name) / 'f.db'), registry_mode_enabled=True)
        client = app.test_client()
        client.post('/qwkhub/apply', json={
            'bbs_name': 'First BBS', 'packet_id': 'DUPE'})
        resp = client.post('/qwkhub/apply', json={
            'bbs_name': 'Second BBS', 'packet_id': 'DUPE'})
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(resp.get_json()['ok'])

    def test_apply_rejects_malformed_packet_id(self):
        app = _make_app(str(Path(self._tmp.name) / 'g.db'), registry_mode_enabled=True)
        client = app.test_client()
        resp = client.post('/qwkhub/apply', json={
            'bbs_name': 'Some BBS', 'packet_id': 'a very long invalid id!!'})
        self.assertEqual(resp.status_code, 400)

    def test_status_404s_on_a_non_hub_install(self):
        app = _make_app(str(Path(self._tmp.name) / 'h.db'), registry_mode_enabled=False)
        client = app.test_client()
        resp = client.get('/qwkhub/status/some-token')
        self.assertEqual(resp.status_code, 404)

    def test_status_returns_pending_then_reflects_approval(self):
        app = _make_app(str(Path(self._tmp.name) / 'i.db'), registry_mode_enabled=True)
        client = app.test_client()
        apply_resp = client.post('/qwkhub/apply', json={
            'bbs_name': 'Some BBS', 'packet_id': 'STATUS1'})
        token = apply_resp.get_json()['request_token']

        status_resp = client.get(f'/qwkhub/status/{token}')
        self.assertEqual(status_resp.status_code, 200)
        body = status_resp.get_json()
        self.assertEqual(body['status'], 'pending')
        self.assertNotIn('generated_password', body)

        # Simulate the hub admin approving it directly in the DB (the
        # real approve_qwk_request route is covered by manual testing
        # against the actual admin UI, not re-tested here).
        from anetbbs.models import db, QWKNodeRequest
        with app.app_context():
            req = QWKNodeRequest.query.filter_by(packet_id='STATUS1').first()
            req.status = 'approved'
            req.generated_password = 'testpass123'
            db.session.commit()

        status_resp2 = client.get(f'/qwkhub/status/{token}')
        body2 = status_resp2.get_json()
        self.assertEqual(body2['status'], 'approved')
        self.assertEqual(body2['generated_password'], 'testpass123')

    def test_status_404s_for_an_unknown_token(self):
        app = _make_app(str(Path(self._tmp.name) / 'j.db'), registry_mode_enabled=True)
        client = app.test_client()
        resp = client.get('/qwkhub/status/nonexistent-token')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
