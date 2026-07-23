"""Regression test for a real gap found in a full echomail-subsystem
audit: nothing guarded against two overlapping poll attempts for the
SAME network -- a sysop double-clicking "Poll Now"
(echomail_admin.py's poll_now(), which spawns a bare daemon thread with
no dedup check at all) or a manual poll landing while the scheduled
_poller_loop's own tick for that network is already mid-flight could
run two concurrent BinkP sessions to the same peer, risking duplicate
sends and interleaved hold-queue/ack-gated-stamping writes.

Fixed by reusing the existing status='running' EchomailPollLog row
(already written for poll-in-progress visibility) as a dedup signal:
_do_poll() now checks for an existing running row for the same
network before starting a second one.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PollerConcurrentPollGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poller_concurrent_guard_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_second_poll_is_skipped_while_first_is_still_running(self):
        from anetbbs.models import db, EchomailNetwork, EchomailPollLog
        from anetbbs.echomail import poller

        with self.app.app_context():
            net = EchomailNetwork(
                name='ConcurrentGuardNet', network_type='binkp',
                binkp_host='127.0.0.1', binkp_port=24554,
                our_address='1:1/20', hub_address='1:1/21',
                is_active=True, poll_interval_minutes=30)
            db.session.add(net)
            db.session.commit()

            # Simulate a poll already in progress (exactly the row
            # _do_poll() itself writes at the top of a real attempt).
            existing = EchomailPollLog(
                network_id=net.id, poll_type='both', status='running')
            db.session.add(existing)
            db.session.commit()

            called = {'n': 0}

            def _fake_run_client(*a, **kw):
                called['n'] += 1
                return {'received': [], 'sent': 0}

            with patch.object(poller, '_run_client', _fake_run_client):
                poller._do_poll(self.app, net)

            self.assertEqual(called['n'], 0,
                             'a second poll must not even attempt a connection '
                             'while one is already in progress for this network')
            # Still exactly one row -- no new EchomailPollLog created for
            # the skipped attempt.
            self.assertEqual(
                EchomailPollLog.query.filter_by(network_id=net.id).count(), 1)

    def test_poll_proceeds_normally_when_nothing_is_running(self):
        """Sanity check: the guard must not block the normal case."""
        from anetbbs.models import db, EchomailNetwork, EchomailPollLog
        from anetbbs.echomail import poller

        with self.app.app_context():
            net = EchomailNetwork(
                name='ConcurrentGuardNormalNet', network_type='binkp',
                binkp_host='127.0.0.1', binkp_port=24554,
                our_address='1:1/22', hub_address='1:1/23',
                is_active=True, poll_interval_minutes=30)
            db.session.add(net)
            db.session.commit()

            called = {'n': 0}

            def _fake_run_client(*a, **kw):
                called['n'] += 1
                return {'received': [], 'sent': 0}

            with patch.object(poller, '_run_client', _fake_run_client):
                poller._do_poll(self.app, net)

            self.assertEqual(called['n'], 1)
            self.assertEqual(
                EchomailPollLog.query.filter_by(network_id=net.id).count(), 1)
            row = EchomailPollLog.query.filter_by(network_id=net.id).first()
            self.assertEqual(row.status, 'success')

    def test_completed_prior_poll_does_not_block_a_new_one(self):
        """A row that's already moved past 'running' (success/error/
        partial) is old history, not a signal to skip."""
        from anetbbs.models import db, EchomailNetwork, EchomailPollLog
        from anetbbs.echomail import poller

        with self.app.app_context():
            net = EchomailNetwork(
                name='ConcurrentGuardCompletedNet', network_type='binkp',
                binkp_host='127.0.0.1', binkp_port=24554,
                our_address='1:1/24', hub_address='1:1/25',
                is_active=True, poll_interval_minutes=30)
            db.session.add(net)
            db.session.commit()
            db.session.add(EchomailPollLog(
                network_id=net.id, poll_type='both', status='success'))
            db.session.commit()

            called = {'n': 0}

            def _fake_run_client(*a, **kw):
                called['n'] += 1
                return {'received': [], 'sent': 0}

            with patch.object(poller, '_run_client', _fake_run_client):
                poller._do_poll(self.app, net)

            self.assertEqual(called['n'], 1)


if __name__ == '__main__':
    unittest.main()
