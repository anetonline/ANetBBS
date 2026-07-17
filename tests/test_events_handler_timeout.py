"""Regression test for a real live bug: a peer sysop's scheduled-events
admin panel showed events configured correctly but NEVER firing --
confirmed via a 4-day, 73000-line log with the scheduler's own startup
line appearing exactly once and zero activity after. Root cause:
events/runner.py's fire() ran the handler call directly on the
scheduler thread with no timeout at all (a deliberate prior design
choice -- "a runaway is a sysop bug, not a security threat") -- but the
actual consequence is worse than that framing suggests: ONE hung
handler (a `shell` command waiting on input, a network call with no
effective timeout, anything) freezes the scheduler thread FOREVER,
silently, taking down every OTHER scheduled event with it too,
including the harmless stock defaults that have nothing to do with
whatever hung.

Fixed by bounding every handler call to a generous but hard ceiling
(_HANDLER_TIMEOUT_SEC, 300s) via eventlet.tpool.execute() + eventlet
.Timeout() when eventlet is active, with a plain-threading fallback
otherwise -- a hung handler's own underlying call may keep running in
the background past the deadline (no safe way to force-kill a stuck
native thread), but it can no longer block any OTHER event from firing.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.events import runner


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


class HandlerTimeoutTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._db_path = str(Path(__file__).resolve().parent / '.events_timeout_test.db')
        self.addCleanup(lambda: os.path.exists(self._db_path) and os.remove(self._db_path))
        self.app = _fresh_app(self._db_path)

    def test_hung_handler_times_out_instead_of_blocking_forever(self):
        """A handler that never returns must not hang fire() forever --
        this is the exact bug: one such handler previously froze the
        entire scheduler thread, silently, for the rest of the
        process's life."""
        def _hangs_forever(app, params):
            # Bounded, not truly infinite: eventlet.tpool's worker
            # threads aren't daemonized, so an ACTUALLY-unbounded sleep
            # here would leak a real, permanently-blocked native thread
            # for the rest of THIS TEST PROCESS's life once abandoned --
            # a real concern for the production scheduler too (see
            # _HANDLER_TIMEOUT_SEC's own docstring), but not something
            # a test should also inflict on its own process. A few
            # seconds is plenty to prove fire() returns well before
            # that, without leaking forever.
            time.sleep(3)
            return True, 'should never get here'

        from anetbbs.events.handlers import REGISTRY
        with patch.object(runner, '_HANDLER_TIMEOUT_SEC', 0.3), \
             patch.dict(REGISTRY, {'hangs': _hangs_forever}):
            from anetbbs.models import db, ScheduledEvent
            with self.app.app_context():
                ev = ScheduledEvent(
                    name='hangs', handler_key='hangs', params_json='{}',
                    schedule_json='{"kind": "noop"}', is_enabled=True)
                db.session.add(ev)
                db.session.commit()

                start = time.monotonic()
                ok, out = runner.fire(self.app, ev.id)
                elapsed = time.monotonic() - start

        self.assertFalse(ok)
        self.assertIn('timed out', out)
        self.assertLess(elapsed, 5.0,
                        'fire() must return promptly once the handler '
                        'timeout elapses, not block indefinitely')

    def test_normal_handler_still_returns_its_real_result(self):
        """A well-behaved handler must not be affected by the timeout
        wrapper -- its real (ok, out) must come through unchanged."""
        def _quick(app, params):
            return True, 'all good'

        from anetbbs.events.handlers import REGISTRY
        with patch.dict(REGISTRY, {'quick': _quick}):
            from anetbbs.models import db, ScheduledEvent
            with self.app.app_context():
                ev = ScheduledEvent(
                    name='quick', handler_key='quick', params_json='{}',
                    schedule_json='{"kind": "noop"}', is_enabled=True)
                db.session.add(ev)
                db.session.commit()

                ok, out = runner.fire(self.app, ev.id)

        self.assertTrue(ok)
        self.assertEqual(out, 'all good')

    def test_handler_has_real_app_context_when_run_off_thread(self):
        """The handler must be able to touch the DB -- proves the
        context-repush wrapper actually works, not just that the
        timeout mechanism runs SOMETHING."""
        from anetbbs.models import db, ScheduledEvent

        def _touches_db(app, params):
            # Would raise "working outside of application context"
            # if the timeout wrapper didn't re-push app.app_context()
            # on whatever thread actually runs this.
            count = ScheduledEvent.query.count()
            return True, f'saw {count} event(s)'

        from anetbbs.events.handlers import REGISTRY
        with patch.dict(REGISTRY, {'touches_db': _touches_db}):
            with self.app.app_context():
                ev = ScheduledEvent(
                    name='touches_db', handler_key='touches_db',
                    params_json='{}', schedule_json='{"kind": "noop"}',
                    is_enabled=True)
                db.session.add(ev)
                db.session.commit()

                ok, out = runner.fire(self.app, ev.id)

        self.assertTrue(ok, out)
        self.assertIn('saw', out)

    def test_one_hung_event_does_not_block_a_later_due_event_in_the_same_tick(self):
        """The real-world consequence this bug caused: ONE hung event
        freezing the scheduler thread meant every OTHER event, including
        harmless stock defaults, silently never fired again either."""
        from datetime import datetime, timedelta
        from anetbbs.models import db, ScheduledEvent
        import json

        def _hangs_forever(app, params):
            # Bounded, not truly infinite: eventlet.tpool's worker
            # threads aren't daemonized, so an ACTUALLY-unbounded sleep
            # here would leak a real, permanently-blocked native thread
            # for the rest of THIS TEST PROCESS's life once abandoned --
            # a real concern for the production scheduler too (see
            # _HANDLER_TIMEOUT_SEC's own docstring), but not something
            # a test should also inflict on its own process. A few
            # seconds is plenty to prove fire() returns well before
            # that, without leaking forever.
            time.sleep(3)
            return True, 'should never get here'

        def _quick(app, params):
            return True, 'fine'

        from anetbbs.events.handlers import REGISTRY
        with patch.object(runner, '_HANDLER_TIMEOUT_SEC', 0.3), \
             patch.dict(REGISTRY, {'hangs': _hangs_forever, 'quick': _quick}):
            with self.app.app_context():
                ScheduledEvent.query.delete()
                db.session.commit()
                hung = ScheduledEvent(
                    name='hangs-first', handler_key='hangs', params_json='{}',
                    schedule_json=json.dumps({'kind': 'interval', 'minutes': 1}),
                    is_enabled=True, last_run_at=None)
                fine = ScheduledEvent(
                    name='fine-second', handler_key='quick', params_json='{}',
                    schedule_json=json.dumps({'kind': 'interval', 'minutes': 1}),
                    is_enabled=True, last_run_at=None)
                db.session.add_all([hung, fine])
                db.session.commit()

                start = time.monotonic()
                runner._tick(self.app)
                elapsed = time.monotonic() - start

                refreshed_fine = ScheduledEvent.query.filter_by(
                    name='fine-second').first()

        self.assertLess(elapsed, 5.0,
                        'one hung event must not block the whole tick')
        self.assertIsNotNone(refreshed_fine.last_run_at,
                             'a later event in the same tick must still '
                             'fire even though an earlier one hung')
        self.assertEqual(refreshed_fine.last_status, 'ok')


if __name__ == '__main__':
    unittest.main()
