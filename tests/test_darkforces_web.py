"""Regression tests for the ANetDarkForces web-game play page
(anetbbs/web/games.py's play() route + anetbbs/games/web_games.py's
'fullscreen' flag + anetbbs/templates/games/play_web_fullscreen.html).

Covers a real bug report: playing the canvas raycaster inside the
normal site chrome (navbar/search bar/footer, base.html's bounded
.container) clipped the play area and threw off centering, with no way
to scroll down to see the rest of it. Dark Forces needs mouse-look and
the full viewport the way the standalone build had it, so it now opts
into a separate chrome-free template via a per-registry-entry flag --
this guards that the flag routes to the right template and that the
chrome-free page still carries what the game actually needs (CSRF meta
for the save API, a way back to the Game Center) while NOT dragging in
base.html's navbar/container.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DarkForcesWebPlayPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._dbfile = str(Path(__file__).resolve().parent / '.darkforces_web_test.db')
        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._dbfile}'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User

        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

        with cls.app.app_context():
            db.create_all()
            u = User(username='df_alice', email='df_alice@example.com', is_admin=False)
            u.set_password('password123')
            db.session.add(u)
            db.session.commit()

        cls.alice = cls.app.test_client()
        resp = cls.alice.post('/auth/login',
                               data={'username': 'df_alice', 'password': 'password123'},
                               follow_redirects=True)
        assert resp.status_code == 200, 'login failed for df_alice'

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_registry_entry_flags_fullscreen(self):
        from anetbbs.games.web_games import get_web_game_info
        info = get_web_game_info('darkforces')
        self.assertIsNotNone(info)
        self.assertTrue(info.get('fullscreen'))

    def test_play_page_uses_chromeless_fullscreen_template(self):
        resp = self.alice.get('/games/darkforces/play')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)

        # The game itself still renders.
        self.assertIn('id="gameRoot"', body)
        self.assertIn('id="gameCanvas"', body)
        self.assertIn('js/darkforces/main.js', body)

        # A way back to the Game Center, since base.html's navbar isn't here.
        self.assertIn('id="fsBackLink"', body)
        self.assertIn('/games/', body)

        # CSRF meta must still be present -- the save API is a fetch() POST.
        self.assertIn('name="csrf-token"', body)

        # NOT the normal chromed layout: no navbar, no bounded .container,
        # no footer -- this is what was clipping/off-centering the canvas.
        self.assertNotIn('navbar-brand', body)
        self.assertNotIn('class="footer"', body)
        self.assertNotIn('Back to Game Center', body)  # play_web.html's header link

    def test_other_web_games_keep_normal_chrome(self):
        # Sanity check the flag is opt-in, not a global behavior change --
        # an ordinary boxed web game must still get the chromed template.
        resp = self.alice.get('/games/snake/play')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('navbar-brand', body)
        self.assertIn('Back to Game Center', body)


if __name__ == '__main__':
    unittest.main()
