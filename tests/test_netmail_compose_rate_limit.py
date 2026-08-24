"""Regression test for a real gap found in a security audit:
anetbbs/web/netmail.py's compose() route can spawn an immediate
outbound BinkP dial-out thread on submit (a "Crash"-flagged netmail,
or a direct-crash reply -- see send_netmail_direct_now()/
trigger_immediate_delivery() in the route body), bypassing the normal
poll schedule entirely -- but unlike every other route in this app
that can trigger repeated outbound network activity (e.g. file_areas.py's
upload(), which carries @rate_limit('file_area_upload', ...)), compose()
had no rate limiting at all. Any authenticated user could spam
compose+crash-flag to spawn unbounded daemon threads and force repeated
unsolicited outbound BinkP connection attempts against real third-party
FTN nodes/hubs.
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


class NetmailComposeRateLimitTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        import tempfile
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'netmail_compose_rl.db'))
        with self.app.app_context():
            from anetbbs.models import db, User
            db.create_all()
            user = User(username='netmailrltest', email='nmrl@example.test',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_compose_is_rate_limited_after_the_configured_number_of_requests(self):
        # The real limit is 20/300s -- drive it past that directly via
        # the rate-limit internals (same established pattern as
        # test_file_areas_upload_size_and_rate_limit.py) rather than
        # firing 21 real crash-mail sends.
        from anetbbs.features import rate_limit as rl
        for _ in range(20):
            rl._check('netmail_compose:u' + str(self.user_id), 20, 300)
        resp = self._client().post('/netmail/compose', data={
            'to_address': '1:1/1', 'to_name': 'Someone',
            'subject': 'one too many', 'body': 'x',
        })
        self.assertEqual(resp.status_code, 429)

    def test_compose_still_works_under_the_limit(self):
        resp = self._client().get('/netmail/compose')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
