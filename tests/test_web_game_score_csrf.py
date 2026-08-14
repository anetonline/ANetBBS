"""Regression test: every web game's score-submission fetch() call
(anetbbs/templates/games/web/*.html) POSTed to /games/<slug>/score
with a JSON body and NO X-CSRFToken header at all (hangman.html's own
attempt read a "csrf_token" cookie that nothing in this app ever
sets, so it was just as broken via a different path). CSRFProtect is
initialized app-wide in web_app.py with no exemption for the games
blueprint, so with CSRF protection actually enabled (the real
production posture), EVERY score submission from EVERY web game was
silently rejected -- the JS's own .catch(function(){}) swallowed the
failure with zero user-visible error. A real, live functional bug,
not just a theoretical one. Found in a security/performance audit.

Fixed by reading the token from the same <meta name="csrf-token">
tag base.html already renders on every page and sending it as
X-CSRFToken -- the same working pattern already used by this app's
own blackjack/holdem/slots/videopoker templates and several admin
pages.

Verifies with REAL Flask-WTF CSRF protection enabled (most of this
suite disables it for convenience) that: (1) a submission with no
token is rejected and no GameScore row is created (proves the
endpoint is genuinely protected, not accidentally exempted), and (2)
a submission using the real token from the rendered page -- exactly
how the fixed JS now behaves -- succeeds and a GameScore row IS
created.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class WebGameScoreCsrfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.web_game_score_csrf_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Game
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='csrfscoretester',
                    email='csrfscoretester@example.com',
                    password_hash='x', access_level=0)
            db.session.add(u)
            g = Game(name='CSRF Test Game', slug='csrf-score-test-game',
                     game_type='web', is_active=True, min_access_level=0)
            db.session.add(g)
            db.session.commit()
            cls.user_id = u.id
            cls.game_slug = g.slug

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _score_count(self):
        from anetbbs.models import GameScore
        with self.app.app_context():
            return GameScore.query.filter_by(user_id=self.user_id).count()

    def test_submission_with_no_csrf_token_is_rejected(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        try:
            client = self._client_as(self.user_id)
            before = self._score_count()
            resp = client.post(f'/games/{self.game_slug}/score',
                               json={'score': 12345})
            after = self._score_count()
            self.assertNotEqual(resp.status_code, 200,
                                'a request with no CSRF token must not succeed')
            self.assertEqual(before, after,
                             'no GameScore row must be created without a valid CSRF token')
        finally:
            self.app.config['WTF_CSRF_ENABLED'] = False

    def test_submission_with_the_real_page_token_succeeds(self):
        """Mirrors exactly what the fixed JS now does: GET a page that
        renders the <meta name="csrf-token"> tag, extract the token,
        send it back as X-CSRFToken on the score POST."""
        self.app.config['WTF_CSRF_ENABLED'] = True
        try:
            client = self._client_as(self.user_id)
            page = client.get(f'/games/{self.game_slug}')
            self.assertEqual(page.status_code, 200)
            m = re.search(r'<meta name="csrf-token" content="([^"]+)">',
                          page.get_data(as_text=True))
            self.assertIsNotNone(m, 'the game detail page must render the '
                                    'csrf-token meta tag (base.html)')
            token = m.group(1)

            before = self._score_count()
            resp = client.post(f'/games/{self.game_slug}/score',
                               json={'score': 12345},
                               headers={'X-CSRFToken': token})
            after = self._score_count()
            self.assertEqual(resp.status_code, 200,
                             'a request with the real page CSRF token must succeed')
            self.assertEqual(after, before + 1,
                             'a GameScore row must be created for a '
                             'correctly-authenticated submission')
        finally:
            self.app.config['WTF_CSRF_ENABLED'] = False


if __name__ == '__main__':
    unittest.main()
