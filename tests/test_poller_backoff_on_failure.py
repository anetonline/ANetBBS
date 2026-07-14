"""Regression test for a real bug reported via external GitHub issue
#8: a failed echomail poll left EchomailNetwork.last_poll_at
untouched, so _is_poll_due() saw the network as still "due" on the
very next scheduler tick (60 seconds later -- see poller.py's main
loop) and retried in a tight loop regardless of the configured poll
interval. The reporter noted some upstream hubs started blocking the
repeated attempts, making the original failure worse.

Reporter's own patch (accepted as-is after review): move the
`network.last_poll_at = datetime.utcnow()` stamp out of the
success-only path and into _do_poll()'s `finally` block, so it's set
unconditionally -- one poll attempt, one stamp, success or failure.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PollerBackoffOnFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poller_backoff_test.db')
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

    def test_last_poll_at_set_after_failed_poll(self):
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.poller import _do_poll

        with self.app.app_context():
            net = EchomailNetwork(
                name='BackoffFailTestNet', network_type='binkp',
                binkp_host='127.0.0.1', binkp_port=1,  # nothing listening
                our_address='1:1/10', hub_address='1:1/11',  # not self-referential
                is_active=True, poll_interval_minutes=30)
            db.session.add(net)
            db.session.commit()

            self.assertIsNone(net.last_poll_at)

            before = datetime.utcnow()
            with self.assertRaises(Exception):
                _do_poll(self.app, net)
            after = datetime.utcnow()

            self.assertIsNotNone(net.last_poll_at,
                                 'a failed poll must still stamp last_poll_at, '
                                 'or the network retries on every scheduler tick')
            self.assertGreaterEqual(net.last_poll_at, before)
            self.assertLessEqual(net.last_poll_at, after)

    def test_is_poll_due_false_immediately_after_failed_poll(self):
        """The actual reported symptom: without the fix, a failed poll's
        network looks "due" again on the very next 60-second tick,
        regardless of poll_interval_minutes."""
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.poller import _do_poll, _is_poll_due

        with self.app.app_context():
            net = EchomailNetwork(
                name='BackoffDueTestNet', network_type='binkp',
                binkp_host='127.0.0.1', binkp_port=1,
                our_address='1:1/20', hub_address='1:1/21',
                is_active=True, poll_interval_minutes=30)
            db.session.add(net)
            db.session.commit()

            with self.assertRaises(Exception):
                _do_poll(self.app, net)

            # Immediately after the failed poll -- must NOT be due again
            # (that's exactly the retry-storm bug).
            self.assertFalse(_is_poll_due(net, datetime.utcnow()))
            # But once the real interval has elapsed, it must be due
            # again -- confirms this isn't a permanent lockout either.
            self.assertTrue(_is_poll_due(
                net, net.last_poll_at + timedelta(minutes=31)))

    def test_success_path_still_sets_last_poll_at(self):
        """Regression guard: the reporter's patch also removed a
        redundant early commit from the success path (last_poll_at was
        previously stamped once right after a successful send/receive,
        then committed again a few lines later in `finally`) -- confirm
        a poll that completes without raising at all still ends up
        with last_poll_at set, not just the failure path above.
        _run_client() is mocked to skip real network I/O entirely and
        return a successful, empty result."""
        from unittest.mock import patch
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail import poller as poller_mod

        with self.app.app_context():
            net = EchomailNetwork(
                name='BackoffSuccessTestNet', network_type='binkp',
                binkp_host='127.0.0.1', binkp_port=1,
                our_address='1:1/30', hub_address='1:1/31',
                is_active=True, poll_interval_minutes=30)
            db.session.add(net)
            db.session.commit()
            self.assertIsNone(net.last_poll_at)

            with patch.object(poller_mod, '_run_client',
                              return_value={'sent': 0, 'received': []}):
                poller_mod._do_poll(self.app, net)  # must not raise

            self.assertIsNotNone(net.last_poll_at)


if __name__ == '__main__':
    unittest.main()
