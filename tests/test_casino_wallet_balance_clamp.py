"""Regression test for a CRITICAL currency-integrity bug found in a full
audit: anetbbs/web/games.py's update_wallet() took the client's claimed
final casino chip balance verbatim with no validation at all -- any
authenticated user could POST an arbitrary balance (e.g. 999999999) and
have it persisted directly, also forging #1 leaderboard peak_balance
entries in the process.

The real fix is moving bet/payout math server-side (all 4 casino games
run entirely client-side JS today) -- out of scope for this pass. This
test locks in the proportionate mitigation that IS in place: a hard
per-request delta cap and an absolute ceiling, so the trivial
"set balance to a huge number in one request" exploit is blocked.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CasinoWalletBalanceClampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.casino_wallet_clamp_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Game
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            user = User(username='casinoclamptest', email='cct@example.com',
                       password_hash='x', is_active=True, access_level=100)
            db.session.add(user)
            if Game.query.filter_by(slug='blackjack').first() is None:
                db.session.add(Game(slug='blackjack', name='Blackjack', is_active=True,
                                    web_enabled=True, game_type='builtin_web',
                                    min_access_level=0))
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_absurd_balance_is_clamped_not_persisted_verbatim(self):
        client = self._client()
        resp = client.post('/games/blackjack/wallet', json={'balance': 999999999})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertLess(body['balance'], 999999999)

    def test_repeated_large_jumps_stay_bounded_by_absolute_ceiling(self):
        client = self._client()
        last_balance = None
        for _ in range(5):
            resp = client.post('/games/blackjack/wallet', json={'balance': 999999999})
            last_balance = resp.get_json()['balance']
        # 500x the blackjack starting balance (500) = 250000
        self.assertLessEqual(last_balance, 500 * 500)

    def test_modest_realistic_win_still_applies_normally(self):
        client = self._client()
        resp = client.get('/games/blackjack/wallet')
        start_balance = resp.get_json()['balance']
        resp = client.post('/games/blackjack/wallet',
                           json={'balance': start_balance + 50})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['balance'], start_balance + 50)


if __name__ == '__main__':
    unittest.main()
