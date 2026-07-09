"""Regression test: InterBBS Wall / Last Callers admin settings must
refuse a QWK-type network, at both the UI level (dropdown never offers
one) and the route level (a crafted POST with a QWK network_id is
rejected, not silently accepted).

Live-caught: a sysop pointed InterBBS Wall at a QWK-type network and
the ANET_WALL area never worked -- QWK areas are identified by numeric
conference number end to end (anetbbs/echomail/qwk.py,
anetbbs/echomail/qwk_hub_ftp.py), so a symbolic tag like ANET_WALL can
never receive real QWK traffic no matter how the EchoArea row gets
created.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class InterbbsBinkpOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

        # wall_admin.py/lastcallers_admin.py's _env_path() always resolves
        # to the REAL repo .env file, by design (that's genuinely what it
        # writes to in production) -- but a test that reaches the
        # "settings saved" success path calls _write_env_keys() against
        # it too. Confirmed the hard way: an earlier version of this test
        # file left a real .env behind in the repo with test data in it,
        # which then got picked up by config.py's load_dotenv() on every
        # later pytest run in the same session and poisoned
        # TestingConfig's class-level defaults process-wide. Redirect
        # both to a scratch path for the duration of every test here.
        scratch_env = str(Path(self._tmp.name) / 'scratch.env')
        self._env_patches = [
            patch('anetbbs.web.wall_admin._env_path', return_value=scratch_env),
            patch('anetbbs.web.lastcallers_admin._env_path', return_value=scratch_env),
            patch('anetbbs.web.games_interbbs_admin._env_path', return_value=scratch_env),
            patch('anetbbs.web.admin._env_path', return_value=scratch_env),
        ]
        for p in self._env_patches:
            p.start()
            self.addCleanup(p.stop)

    def _login_as_admin(self, client):
        from anetbbs.models import db, User
        with self.app.app_context():
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

    def _make_networks(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            binkp = EchomailNetwork(name='ANotherNetwork', network_type='binkp')
            qwk = EchomailNetwork(name='ANotherNetwork (QWK)', network_type='qwk')
            db.session.add_all([binkp, qwk])
            db.session.commit()
            return binkp.id, qwk.id

    def test_wall_network_dropdown_excludes_qwk(self):
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        resp = client.get('/admin/wall/')
        body = resp.get_data(as_text=True)
        self.assertIn('ANotherNetwork<', body)
        self.assertNotIn('ANotherNetwork (QWK)', body)

    def test_wall_settings_rejects_qwk_network_id(self):
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        resp = client.post('/admin/wall/settings', data={
            'color_scheme': 'cyan',
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(qwk_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'BinkP network', resp.data)
        self.assertFalse(self.app.config.get('WALL_INTERBBS_ENABLED'))

    def test_wall_settings_accepts_binkp_network_id(self):
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        resp = client.post('/admin/wall/settings', data={
            'color_scheme': 'cyan',
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(binkp_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.app.config.get('WALL_INTERBBS_ENABLED'))
        self.assertEqual(self.app.config.get('WALL_INTERBBS_NETWORK_ID'), str(binkp_id))

    def test_wall_settings_creates_area_immediately_not_lazily(self):
        """Live-caught: enabling the feature alone created nothing at
        all -- the ANET_WALL area only got created the first time
        someone posted a new local wall message. A sysop enabling this
        should see the area right away, not have to post something
        first just to bootstrap it."""
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        client.post('/admin/wall/settings', data={
            'color_scheme': 'cyan',
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(binkp_id),
        }, follow_redirects=True)

        from anetbbs.echomail.interbbs_sync import WALL_AREA_TAG
        from anetbbs.models import EchoArea
        with self.app.app_context():
            area = EchoArea.query.filter_by(network_id=binkp_id, tag=WALL_AREA_TAG).first()
            self.assertIsNotNone(area, 'ANET_WALL area must be created immediately on enable')
            self.assertTrue(area.is_sysop_only)
            self.assertTrue(area.is_subscribed)

    def test_lastcallers_network_dropdown_excludes_qwk(self):
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        resp = client.get('/admin/lastcallers/')
        body = resp.get_data(as_text=True)
        self.assertIn('ANotherNetwork<', body)
        self.assertNotIn('ANotherNetwork (QWK)', body)

    def test_lastcallers_settings_rejects_qwk_network_id(self):
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        resp = client.post('/admin/lastcallers/settings', data={
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(qwk_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'BinkP network', resp.data)
        self.assertFalse(self.app.config.get('LASTCALLERS_INTERBBS_ENABLED'))

    def test_lastcallers_settings_creates_area_immediately_not_lazily(self):
        """Same fix as Wall's -- see
        test_wall_settings_creates_area_immediately_not_lazily."""
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        client.post('/admin/lastcallers/settings', data={
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(binkp_id),
        }, follow_redirects=True)

        from anetbbs.echomail.interbbs_sync import LASTCALLERS_AREA_TAG
        from anetbbs.models import EchoArea
        with self.app.app_context():
            area = EchoArea.query.filter_by(network_id=binkp_id, tag=LASTCALLERS_AREA_TAG).first()
            self.assertIsNotNone(area, 'ANET_LASTCALLERS area must be created immediately on enable')
            self.assertTrue(area.is_sysop_only)
            self.assertTrue(area.is_subscribed)

    # ------------------------------------------------------------------
    # InterBBS Game Scores -- same BinkP-only / eager-area-creation shape
    # as Wall/Last Callers above, plus the casino starting-balance
    # fairness lock unique to this feature.
    # ------------------------------------------------------------------

    def test_games_network_dropdown_excludes_qwk(self):
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        resp = client.get('/admin/games-interbbs/')
        body = resp.get_data(as_text=True)
        self.assertIn('ANotherNetwork<', body)
        self.assertNotIn('ANotherNetwork (QWK)', body)

    def test_games_settings_rejects_qwk_network_id(self):
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        resp = client.post('/admin/games-interbbs/settings', data={
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(qwk_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'BinkP network', resp.data)
        self.assertFalse(self.app.config.get('GAMES_INTERBBS_ENABLED'))

    def test_games_settings_creates_area_immediately_not_lazily(self):
        """Same fix as Wall's/Last Callers' -- see
        test_wall_settings_creates_area_immediately_not_lazily. Built
        correctly the first time here rather than shipping the same
        lazy-creation bug a third time."""
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        client.post('/admin/games-interbbs/settings', data={
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(binkp_id),
        }, follow_redirects=True)

        from anetbbs.echomail.interbbs_sync import GAMES_AREA_TAG
        from anetbbs.models import EchoArea
        with self.app.app_context():
            area = EchoArea.query.filter_by(network_id=binkp_id, tag=GAMES_AREA_TAG).first()
            self.assertIsNotNone(area, 'ANET_GAMESCORES area must be created immediately on enable')
            self.assertTrue(area.is_sysop_only)
            self.assertTrue(area.is_subscribed)

    def test_games_settings_enable_forces_casino_starts_to_standard(self):
        """Enabling score sharing must reset the four casino starting
        balances to the shared standard, regardless of what the sysop
        had them set to before -- a "$50,000 peak" isn't earned under
        identical odds on two installs with different starting
        balances."""
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)
        self.app.config['CASINO_BLACKJACK_START'] = '99999'

        client.post('/admin/games-interbbs/settings', data={
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(binkp_id),
        }, follow_redirects=True)

        from anetbbs.echomail.interbbs_sync import CASINO_INTERBBS_STANDARD_STARTS
        for key, val in CASINO_INTERBBS_STANDARD_STARTS.items():
            self.assertEqual(str(self.app.config.get(key)), val,
                             f'{key} was not reset to the standard value on enable')

    def test_casino_start_change_auto_disables_games_interbbs(self):
        """The fairness lock: changing a CASINO_*_START value away from
        the standard while score sharing is on must turn score sharing
        back off in the same write, not leave the numbers to silently
        drift out of sync with other installs."""
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        client.post('/admin/games-interbbs/settings', data={
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(binkp_id),
        }, follow_redirects=True)
        self.assertTrue(self.app.config.get('GAMES_INTERBBS_ENABLED'))

        resp = client.post('/admin/settings', data={
            'CASINO_BLACKJACK_START': '750',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'InterBBS score sharing was turned off', resp.data)
        self.assertFalse(self.app.config.get('GAMES_INTERBBS_ENABLED'))

    def test_casino_start_resubmit_same_value_does_not_disable(self):
        """Submitting the settings form re-posts every field, including
        ones the sysop didn't touch -- resubmitting the SAME value must
        not be mistaken for a real change and trip the fairness lock."""
        binkp_id, qwk_id = self._make_networks()
        client = self.app.test_client()
        self._login_as_admin(client)

        client.post('/admin/games-interbbs/settings', data={
            'interbbs_enabled': 'on',
            'interbbs_network_id': str(binkp_id),
        }, follow_redirects=True)
        self.assertTrue(self.app.config.get('GAMES_INTERBBS_ENABLED'))

        from anetbbs.echomail.interbbs_sync import CASINO_INTERBBS_STANDARD_STARTS
        resp = client.post('/admin/settings', data={
            'CASINO_BLACKJACK_START': CASINO_INTERBBS_STANDARD_STARTS['CASINO_BLACKJACK_START'],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'InterBBS score sharing was turned off', resp.data)
        self.assertTrue(self.app.config.get('GAMES_INTERBBS_ENABLED'))


if __name__ == '__main__':
    unittest.main()
