"""Regression tests for anetbbs.core.session.BBSSession._enforce_time_budget.

Real gap found in a security/performance audit: this method -- the
per-session/per-day UserTimeBudget cutoff a sysop can configure per
user in /admin/users/<id> -- was fully implemented (immediate cutoff
when the daily allowance is already exhausted, a 5-minute warning plus
a hard disconnect watchdog otherwise) but nothing in the real login
flow ever called it, so a configured limit had zero effect on a real
session no matter what a sysop set it to. Called unbound against a
lightweight fake session object, same technique already used for
tests/test_notification_login_popup.py.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class _FakeWriter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, user_id, is_admin=False):
        self.user = {'id': user_id, 'is_admin': is_admin}
        self.writer = _FakeWriter()
        self.written = []
        self._budget_task = None

    async def write(self, text):
        self.written.append(text)


class TimeBudgetEnforcementTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'time_budget.db'))
        with self.app.app_context():
            from anetbbs.models import db, User
            u = User(username='budgetuser', email='budget@example.com', password_hash='x')
            admin = User(username='budgetadmin', email='budgetadmin@example.com',
                        password_hash='x', is_admin=True)
            db.session.add_all([u, admin])
            db.session.commit()
            self.user_id = u.id
            self.admin_id = admin.id

    def _run(self, session):
        from anetbbs.core.session import BBSSession
        return asyncio.run(BBSSession._enforce_time_budget(session))

    def _run_and_check_watchdog_scheduled(self, session):
        """asyncio.run() tears down its loop (cancelling any still-
        pending child tasks) the instant the awaited coroutine
        returns -- checking .done() on the watchdog task AFTER
        asyncio.run() has already returned would always see it
        cancelled regardless of whether scheduling actually worked.
        This drives everything (the call, a single scheduling
        opportunity via asyncio.sleep(0), the pending check, and a
        clean cancel+await) inside one still-running loop instead."""
        from anetbbs.core.session import BBSSession
        async def _inner():
            await BBSSession._enforce_time_budget(session)
            if session._budget_task is None:
                return None
            await asyncio.sleep(0)  # let create_task's watchdog actually start
            still_pending = not session._budget_task.done()
            session._budget_task.cancel()
            try:
                await session._budget_task
            except asyncio.CancelledError:
                pass
            return still_pending
        return asyncio.run(_inner())

    def test_no_op_when_no_budget_row_exists(self):
        session = _FakeSession(self.user_id)
        self._run(session)
        self.assertEqual(session.written, [])
        self.assertFalse(session.writer.closed)
        self.assertIsNone(session._budget_task)

    def test_no_op_for_admin_even_with_an_exhausted_budget_row(self):
        from anetbbs.models import db, UserTimeBudget
        with self.app.app_context():
            db.session.add(UserTimeBudget(
                user_id=self.admin_id, time_limit_min=0,
                daily_limit_min=60, used_today_min=60, bank_minutes=0))
            db.session.commit()
        session = _FakeSession(self.admin_id, is_admin=True)
        self._run(session)
        self.assertEqual(session.written, [])
        self.assertFalse(session.writer.closed)

    def test_exhausted_daily_budget_writes_message_and_closes_the_writer(self):
        from anetbbs.models import db, UserTimeBudget
        with self.app.app_context():
            db.session.add(UserTimeBudget(
                user_id=self.user_id, time_limit_min=0,
                daily_limit_min=60, used_today_min=60, bank_minutes=0))
            db.session.commit()
        session = _FakeSession(self.user_id)
        self._run(session)
        joined = ''.join(session.written)
        self.assertIn('time budget is exhausted', joined)
        self.assertTrue(session.writer.closed,
                        'an exhausted budget must hard-disconnect the session')

    def test_banked_minutes_extend_an_otherwise_exhausted_daily_budget(self):
        from anetbbs.models import db, UserTimeBudget
        with self.app.app_context():
            db.session.add(UserTimeBudget(
                user_id=self.user_id, time_limit_min=0,
                daily_limit_min=60, used_today_min=60, bank_minutes=30))
            db.session.commit()
        session = _FakeSession(self.user_id)
        still_pending = self._run_and_check_watchdog_scheduled(session)
        self.assertEqual(session.written, [])
        self.assertFalse(session.writer.closed)
        self.assertIsNotNone(session._budget_task,
                             'remaining bank minutes must schedule a watchdog, not disconnect')
        self.assertTrue(still_pending)

    def test_remaining_time_schedules_a_watchdog_task_without_disconnecting(self):
        from anetbbs.models import db, UserTimeBudget
        with self.app.app_context():
            db.session.add(UserTimeBudget(
                user_id=self.user_id, time_limit_min=30,
                daily_limit_min=0, used_today_min=0, bank_minutes=0))
            db.session.commit()
        session = _FakeSession(self.user_id)
        still_pending = self._run_and_check_watchdog_scheduled(session)
        self.assertEqual(session.written, [])
        self.assertFalse(session.writer.closed)
        self.assertIsNotNone(session._budget_task)
        self.assertTrue(still_pending)

    def test_unlimited_budget_row_is_a_no_op(self):
        """time_limit_min=0 and daily_limit_min=0 together mean
        unlimited -- must not schedule a watchdog or disconnect."""
        from anetbbs.models import db, UserTimeBudget
        with self.app.app_context():
            db.session.add(UserTimeBudget(
                user_id=self.user_id, time_limit_min=0,
                daily_limit_min=0, used_today_min=999, bank_minutes=0))
            db.session.commit()
        session = _FakeSession(self.user_id)
        self._run(session)
        self.assertEqual(session.written, [])
        self.assertFalse(session.writer.closed)
        self.assertIsNone(session._budget_task)

    def test_is_actually_invoked_from_the_real_login_flow(self):
        """Direct guard against the exact regression this audit found:
        confirms the call site exists in source, not just that the
        method works when called directly (every test above already
        proved that in isolation -- the real bug was that nothing
        called it at all)."""
        import inspect
        from anetbbs.core.session import BBSSession
        source = inspect.getsource(BBSSession.start)
        self.assertIn('_enforce_time_budget', source)

    def test_budget_task_is_cancelled_on_session_teardown(self):
        """Real gap found in a security/performance audit: unlike the
        presence-heartbeat (_hb_task) and kick-watchdog (_kick_task)
        tasks right next to it, _budget_task was created by
        _enforce_time_budget() but never cancelled anywhere -- a
        normal logout left it alive, sleeping for however much of the
        budget window remained, holding a reference to the dead
        session (including its reader/writer) until it finally woke up
        and tried to close an already-torn-down connection. Same
        source-inspection technique as the test above, since start()'s
        finally: block isn't independently callable -- confirms the
        cancellation line actually exists, in the same shape/pattern
        as the neighboring _kick_task cancellation it was modeled on."""
        import inspect
        from anetbbs.core.session import BBSSession
        source = inspect.getsource(BBSSession.start)
        self.assertIn("getattr(self, '_budget_task', None)", source)
        # Must actually be cancelled, not just looked up.
        idx = source.index("getattr(self, '_budget_task', None)")
        nearby = source[idx:idx + 200]
        self.assertIn('.cancel()', nearby)


if __name__ == '__main__':
    unittest.main()
