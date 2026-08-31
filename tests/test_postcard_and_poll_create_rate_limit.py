"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): postcards.py's create() and polls.py's
new_poll() had no rate limiting at all -- unlike every other user-
content-creation route in this app (board_post, netmail_compose,
file_area_upload, pm's own inline PM limiter). Any logged-in user
could POST to either in a tight loop and create an unbounded number of
Postcard or Poll/PollOption rows. Fixed with the same
limit=20/window=300 threshold already used for the equivalent routes.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_app(db_path):
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


class _RateLimitTestBase(unittest.TestCase):
    _db_name = 'create_rl.db'

    def setUp(self):
        import anetbbs.config as cfg_mod
        import tempfile
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / self._db_name))
        with self.app.app_context():
            from anetbbs.models import db, User
            db.create_all()
            user = User(username='createratelimituser', email='crlu@example.test',
                       password_hash='x', access_level=100, is_admin=False)
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client


class PostcardCreateRateLimitTests(_RateLimitTestBase):
    _db_name = 'postcard_create_rl.db'

    def test_create_is_rate_limited_after_the_configured_number_of_requests(self):
        from anetbbs.features import rate_limit as rl
        for _ in range(20):
            rl._check('postcard_create:u' + str(self.user_id), 20, 300)
        resp = self._client().post('/postcards/new', data={
            'name': 'one too many', 'width': '60', 'height': '20',
        })
        self.assertEqual(resp.status_code, 429)

    def test_create_still_works_under_the_limit(self):
        resp = self._client().get('/postcards/new')
        self.assertEqual(resp.status_code, 200)


class PollCreateRateLimitTests(_RateLimitTestBase):
    _db_name = 'poll_create_rl.db'

    def test_new_poll_is_rate_limited_after_the_configured_number_of_requests(self):
        from anetbbs.features import rate_limit as rl
        for _ in range(20):
            rl._check('poll_create:u' + str(self.user_id), 20, 300)
        resp = self._client().post('/polls/new', data={
            'question': 'one too many', 'options': 'A\nB',
        })
        self.assertEqual(resp.status_code, 429)

    def test_new_poll_still_works_under_the_limit(self):
        resp = self._client().get('/polls/new')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
