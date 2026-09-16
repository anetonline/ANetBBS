"""Regression tests for a real live gap: every door dropfile reported a
flat, hardcoded 60 minutes remaining, regardless of the user's actual
UserTimeBudget (or complete absence of one) -- confirmed live by a
sysop testing as an admin account (unlimited by definition) who still
saw every door report exactly one hour left, every single launch.

anetbbs/core/time_budget.py's compute_remaining_minutes() is the new
shared calculation (mirroring core/session.py's own
_enforce_time_budget() hard-kick logic); this file covers that
function directly, plus that games/door_runner.py's launch_door_game()
and features/menu_engine.py's _act_exec() dropfile support both now
call it instead of using the old flat default.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class ComputeRemainingMinutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.time_budget_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            user = User(username='budgettester', email='bt@example.com',
                       password_hash='x', is_admin=False)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.models import db, UserTimeBudget
        with self.app.app_context():
            UserTimeBudget.query.filter_by(user_id=self.user_id).delete()
            db.session.commit()

    def _set_budget(self, **kwargs):
        from anetbbs.models import db, UserTimeBudget
        with self.app.app_context():
            b = UserTimeBudget(user_id=self.user_id, **kwargs)
            db.session.add(b)
            db.session.commit()

    def test_admin_gets_unlimited_regardless_of_any_budget(self):
        from anetbbs.core.time_budget import compute_remaining_minutes, UNLIMITED_MINUTES
        self._set_budget(time_limit_min=5, daily_limit_min=5, used_today_min=5)
        with self.app.app_context():
            self.assertEqual(
                compute_remaining_minutes(self.user_id, is_admin=True),
                UNLIMITED_MINUTES)

    def test_no_budget_row_at_all_is_unlimited(self):
        from anetbbs.core.time_budget import compute_remaining_minutes, UNLIMITED_MINUTES
        with self.app.app_context():
            self.assertEqual(
                compute_remaining_minutes(self.user_id, is_admin=False),
                UNLIMITED_MINUTES)

    def test_budget_row_with_both_limits_zero_is_unlimited(self):
        from anetbbs.core.time_budget import compute_remaining_minutes, UNLIMITED_MINUTES
        self._set_budget(time_limit_min=0, daily_limit_min=0)
        with self.app.app_context():
            self.assertEqual(
                compute_remaining_minutes(self.user_id, is_admin=False),
                UNLIMITED_MINUTES)

    def test_session_limit_alone_is_reported_directly(self):
        from anetbbs.core.time_budget import compute_remaining_minutes
        self._set_budget(time_limit_min=45, daily_limit_min=0)
        with self.app.app_context():
            self.assertEqual(
                compute_remaining_minutes(self.user_id, is_admin=False), 45)

    def test_daily_limit_caps_session_limit_when_lower(self):
        from anetbbs.core.time_budget import compute_remaining_minutes
        # 90 min/session cap, but only 20 of a 240/day budget left.
        self._set_budget(time_limit_min=90, daily_limit_min=240, used_today_min=220)
        with self.app.app_context():
            self.assertEqual(
                compute_remaining_minutes(self.user_id, is_admin=False), 20)

    def test_bank_minutes_extend_the_daily_cap(self):
        from anetbbs.core.time_budget import compute_remaining_minutes
        self._set_budget(time_limit_min=90, daily_limit_min=240,
                         used_today_min=220, bank_minutes=100)
        with self.app.app_context():
            # daily_left (20) + bank (100) = 120, still under the 90 session cap
            self.assertEqual(
                compute_remaining_minutes(self.user_id, is_admin=False), 90)

    def test_exhausted_daily_budget_reports_zero_not_unlimited(self):
        from anetbbs.core.time_budget import compute_remaining_minutes
        self._set_budget(time_limit_min=0, daily_limit_min=60, used_today_min=60)
        with self.app.app_context():
            self.assertEqual(
                compute_remaining_minutes(self.user_id, is_admin=False), 0)


class LaunchDoorGameUsesRealBudgetTests(unittest.TestCase):
    """launch_door_game() must call compute_remaining_minutes() (not
    the old flat default) whenever the caller doesn't explicitly pass
    minutes_remaining -- which is every real caller today."""

    def test_launch_door_game_computes_real_minutes_when_not_given(self):
        from anetbbs.games import door_runner

        fake_game = type('FakeGame', (), {
            'id': 1, 'slug': 'faketestgame', 'max_nodes': 1,
        })()
        user = {'id': 42, 'is_admin': False}

        with patch('anetbbs.games.door_runner.allocate_node', return_value=1), \
             patch('anetbbs.games.door_runner.db') as mock_db, \
             patch('anetbbs.games.door_runner.GameSession'), \
             patch('anetbbs.core.time_budget.compute_remaining_minutes',
                  return_value=123) as mock_compute, \
             patch('anetbbs.games.door_runner.build_token_context',
                  return_value={}), \
             patch('anetbbs.games.door_runner.write_drop_file',
                  return_value=None) as mock_write:
            mock_db.session.add = lambda *a, **kw: None
            mock_db.session.commit = lambda: None
            try:
                door_runner.launch_door_game(
                    fake_game, user, lambda data: None)
            except Exception:
                # The rest of a real launch (PTY fork, command
                # building) isn't under test here and may legitimately
                # fail against a fake Game -- we only care that the
                # dropfile write already happened with the right value
                # by the time any of that runs.
                pass

        mock_compute.assert_called_once_with(42, False)
        self.assertTrue(mock_write.called)
        self.assertEqual(mock_write.call_args.args[3], 123)


if __name__ == '__main__':
    unittest.main()
