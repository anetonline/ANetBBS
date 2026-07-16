"""Regression tests for a real bug reported by a peer sysop ("his
[scheduled] events aren't running automatically"): a ScheduledEvent
row with an out-of-range 'daily'/'weekly' time (the only schedule
kind never range-validated at save time -- hourly/weekly-day/interval
all were) reached `datetime.replace(hour=..)` uncaught inside
`compute_next_run()`. events/runner.py's `_tick()` had no per-row
exception isolation around that call, so the single bad row aborted
the ENTIRE scheduler sweep for that tick -- silently starving every
other event ordered after it, forever, with nothing but a generic
"event scheduler crashed; backing off" line to go on.

Three independent fixes, three test groups:
  1. `_parse_hhmm()` now rejects out-of-range values instead of letting
     them reach `datetime.replace()`.
  2. `_tick()` isolates each row so one bad schedule can't block others.
  3. `events_admin.py`'s `_parse_form_schedule()` now validates daily/
     weekly `time` the same way hourly/weekly-day/interval already
     were, so a bad value can't be saved in the first place; and
     `_row_view()` can't 500 the admin list page on a pre-existing
     bad row.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.events import runner


class ParseHhmmRangeTests(unittest.TestCase):
    def test_valid_time_parses_normally(self):
        self.assertEqual(runner._parse_hhmm('14:30'), (14, 30))

    def test_out_of_range_hour_falls_back_instead_of_raising(self):
        self.assertEqual(runner._parse_hhmm('25:00'), (3, 0))

    def test_out_of_range_minute_falls_back_instead_of_raising(self):
        self.assertEqual(runner._parse_hhmm('10:75'), (3, 0))

    def test_negative_hour_falls_back(self):
        self.assertEqual(runner._parse_hhmm('-1:00'), (3, 0))

    def test_compute_next_run_never_raises_for_out_of_range_time(self):
        # This is the exact call chain that used to crash: a schedule
        # dict with an unvalidated bad hour reaching datetime.replace().
        schedule = {'kind': 'daily', 'time': '25:00'}
        # Must not raise.
        result = runner.compute_next_run(schedule, None)
        self.assertIsInstance(result, datetime)


class TickPerRowIsolationTests(unittest.TestCase):
    """DB-backed: confirms one row's unevaluable schedule doesn't stop
    _tick() from still firing a separate, healthy, due event."""

    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.events_isolation_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_bad_schedule_row_does_not_block_a_later_due_event(self):
        from anetbbs.models import db, ScheduledEvent

        with self.app.app_context():
            ScheduledEvent.query.delete()
            db.session.commit()

            bad = ScheduledEvent(
                name='zzz-bad-row-sorts-last', handler_key='noop',
                params_json='{}',
                schedule_json=json.dumps({'kind': 'daily', 'time': '99:99'}),
                is_enabled=True, last_run_at=None)
            good = ScheduledEvent(
                name='healthy-noop', handler_key='noop', params_json='{}',
                schedule_json=json.dumps({'kind': 'interval', 'minutes': 1}),
                is_enabled=True,
                last_run_at=datetime.utcnow() - timedelta(minutes=5))
            db.session.add_all([bad, good])
            db.session.commit()

            # Sanity: bad's name sorts after good's in default (id) order,
            # so if isolation were missing, bad's exception (were
            # _parse_hhmm not already fixed above) would have prevented
            # good from ever being reached in the same sweep.
            runner._tick(self.app)

            refreshed_good = ScheduledEvent.query.filter_by(
                name='healthy-noop').first()
            self.assertIsNotNone(refreshed_good.last_run_at)
            self.assertEqual(refreshed_good.last_status, 'ok')

    def test_is_due_raising_for_one_row_still_lets_tick_process_others(self):
        """Belt-and-suspenders: even if some future schedule shape
        raises for a reason _parse_hhmm's range guard doesn't cover,
        _tick()'s own per-row try/except must still contain it."""
        from anetbbs.models import db, ScheduledEvent

        with self.app.app_context():
            ScheduledEvent.query.delete()
            db.session.commit()

            boom = ScheduledEvent(
                name='boom', handler_key='noop', params_json='{}',
                schedule_json=json.dumps({'kind': 'daily', 'time': '01:00'}),
                is_enabled=True, last_run_at=None)
            good = ScheduledEvent(
                name='fine', handler_key='noop', params_json='{}',
                schedule_json=json.dumps({'kind': 'daily', 'time': '02:00'}),
                is_enabled=True,
                last_run_at=datetime.utcnow() - timedelta(days=2))
            db.session.add_all([boom, good])
            db.session.commit()

            real_is_due = runner.is_due

            def _flaky_is_due(schedule, last_run_at, **kw):
                if schedule.get('time') == '01:00':
                    raise RuntimeError('simulated unexpected failure')
                return real_is_due(schedule, last_run_at, **kw)

            with patch.object(runner, 'is_due', side_effect=_flaky_is_due):
                runner._tick(self.app)  # must not raise

            refreshed_good = ScheduledEvent.query.filter_by(name='fine').first()
            self.assertIsNotNone(refreshed_good.last_run_at)


class EventsAdminValidationTests(unittest.TestCase):
    """Pure-function tests -- no Flask/DB needed for _parse_form_schedule
    itself, which only reads .get() off whatever's passed in."""

    def test_daily_rejects_out_of_range_hour(self):
        from anetbbs.web.events_admin import _parse_form_schedule
        sched, err = _parse_form_schedule(
            {'schedule_kind': 'daily', 'schedule_time': '25:00'})
        self.assertIsNotNone(err)
        self.assertEqual(sched, {})

    def test_daily_accepts_valid_time(self):
        from anetbbs.web.events_admin import _parse_form_schedule
        sched, err = _parse_form_schedule(
            {'schedule_kind': 'daily', 'schedule_time': '14:30'})
        self.assertIsNone(err)
        self.assertEqual(sched, {'kind': 'daily', 'time': '14:30'})

    def test_weekly_rejects_out_of_range_time(self):
        from anetbbs.web.events_admin import _parse_form_schedule
        sched, err = _parse_form_schedule({
            'schedule_kind': 'weekly', 'schedule_day': '2',
            'schedule_time': '99:99'})
        self.assertIsNotNone(err)
        self.assertEqual(sched, {})

    def test_weekly_accepts_valid_time(self):
        from anetbbs.web.events_admin import _parse_form_schedule
        sched, err = _parse_form_schedule({
            'schedule_kind': 'weekly', 'schedule_day': '2',
            'schedule_time': '04:15'})
        self.assertIsNone(err)
        self.assertEqual(sched, {'kind': 'weekly', 'day': 2, 'time': '04:15'})


if __name__ == '__main__':
    unittest.main()
