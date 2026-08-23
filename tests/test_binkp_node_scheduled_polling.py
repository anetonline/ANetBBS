"""Tests for scheduled hub-initiated polling of downstream BinkP nodes.

Real gap: BinkPNode had dial-out capability (binkp_host/port/tls) and a
working poll function (_do_poll_node, reachable via the manual "Poll Now"
button), but _poller_loop() -- the only place that runs on a timer --
only ever iterated EchomailNetwork rows. A node never got polled unless
a sysop clicked the button by hand, no matter how reachable it was.

poll_interval_minutes on BinkPNode is nullable (unlike EchomailNetwork's,
which always has a real default of 60) -- NULL means "don't auto-poll
this node," since most real downstream nodes are poll-in only and
shouldn't suddenly start getting dialed just because a sysop filled in
binkp_host for some other reason (e.g. to make manual Poll Now
available).

Uses the same real-Flask-app-plus-sqlite pattern as
test_poller_backoff_on_failure.py / test_poller_node_poll_now.py.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BinkPNodeScheduledPollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.node_sched_poll_test.db')
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

    def _make_network(self, name):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(
            name=name, network_type='binkp',
            our_address='1200:1/1', hub_address='1200:1/1', is_active=True)
        db.session.add(net)
        db.session.commit()
        return net

    # -- _is_poll_due_node -----------------------------------------------

    def test_node_with_no_interval_is_never_due(self):
        from anetbbs.echomail.poller import _is_poll_due_node
        from anetbbs.models import db, BinkPNode

        with self.app.app_context():
            net = self._make_network('NoIntervalNet')
            node = BinkPNode(name='N1', ftn_address='1200:1/50', password='x',
                             network_id=net.id, binkp_host='node1.example.test',
                             poll_interval_minutes=None)
            db.session.add(node)
            db.session.commit()

            self.assertFalse(_is_poll_due_node(node, datetime.utcnow()),
                             'a node with poll_interval_minutes=None must '
                             'never be scheduled, only manually pollable')

    def test_node_never_polled_before_is_due_immediately(self):
        from anetbbs.echomail.poller import _is_poll_due_node
        from anetbbs.models import db, BinkPNode

        with self.app.app_context():
            net = self._make_network('FirstPollNet')
            node = BinkPNode(name='N2', ftn_address='1200:1/51', password='x',
                             network_id=net.id, binkp_host='node2.example.test',
                             poll_interval_minutes=30)
            db.session.add(node)
            db.session.commit()

            self.assertTrue(_is_poll_due_node(node, datetime.utcnow()))

    def test_node_respects_its_own_interval(self):
        from anetbbs.echomail.poller import _is_poll_due_node
        from anetbbs.models import db, BinkPNode

        with self.app.app_context():
            net = self._make_network('IntervalNet')
            node = BinkPNode(name='N3', ftn_address='1200:1/52', password='x',
                             network_id=net.id, binkp_host='node3.example.test',
                             poll_interval_minutes=30,
                             last_poll_at=datetime.utcnow())
            db.session.add(node)
            db.session.commit()

            self.assertFalse(_is_poll_due_node(node, datetime.utcnow()))
            self.assertTrue(_is_poll_due_node(
                node, node.last_poll_at + timedelta(minutes=31)))

    def test_floor_protects_against_sub_5_minute_intervals(self):
        from anetbbs.echomail.poller import _is_poll_due_node
        from anetbbs.models import db, BinkPNode

        with self.app.app_context():
            net = self._make_network('FloorNet')
            node = BinkPNode(name='N4', ftn_address='1200:1/53', password='x',
                             network_id=net.id, binkp_host='node4.example.test',
                             poll_interval_minutes=1,  # below the 5-min floor
                             last_poll_at=datetime.utcnow())
            db.session.add(node)
            db.session.commit()

            self.assertFalse(_is_poll_due_node(
                node, node.last_poll_at + timedelta(minutes=2)))
            self.assertTrue(_is_poll_due_node(
                node, node.last_poll_at + timedelta(minutes=6)))

    # -- _do_poll_node stamps last_poll_at unconditionally ----------------

    def test_last_poll_at_stamped_on_success(self):
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, BinkPNode

        with self.app.app_context():
            net = self._make_network('StampSuccessNet')
            node = BinkPNode(name='N5', ftn_address='1200:1/54', password='x',
                             network_id=net.id, binkp_host='node5.example.test')
            db.session.add(node)
            db.session.commit()
            self.assertIsNone(node.last_poll_at)

            with patch.object(poller_mod, '_run_node_client',
                              return_value={'sent': 0, 'received': []}):
                poller_mod._do_poll_node(self.app, node)

            self.assertIsNotNone(node.last_poll_at)

    def test_last_poll_at_stamped_on_failure(self):
        """Same backoff bug class already fixed for EchomailNetwork
        (test_poller_backoff_on_failure.py) -- without an unconditional
        stamp in `finally`, a failed dial-out would look "due" again on
        the very next 60s scheduler tick regardless of interval."""
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, BinkPNode

        with self.app.app_context():
            net = self._make_network('StampFailNet')
            node = BinkPNode(name='N6', ftn_address='1200:1/55', password='x',
                             network_id=net.id, binkp_host='node6.example.test',
                             poll_interval_minutes=30)
            db.session.add(node)
            db.session.commit()

            before = datetime.utcnow()
            with patch.object(poller_mod, '_run_node_client',
                              side_effect=ConnectionRefusedError('boom')):
                with self.assertRaises(Exception):
                    poller_mod._do_poll_node(self.app, node)
            after = datetime.utcnow()

            self.assertIsNotNone(node.last_poll_at)
            self.assertGreaterEqual(node.last_poll_at, before)
            self.assertLessEqual(node.last_poll_at, after)

            from anetbbs.echomail.poller import _is_poll_due_node
            self.assertFalse(_is_poll_due_node(node, datetime.utcnow()))

    # -- _poller_loop's tick dispatches due nodes, skips others ----------

    def test_poller_tick_polls_due_dialable_node_and_skips_others(self):
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, BinkPNode

        with self.app.app_context():
            net = self._make_network('TickNet')

            due_dialable = BinkPNode(
                name='Due', ftn_address='1200:1/60', password='x',
                network_id=net.id, binkp_host='due.example.test',
                poll_interval_minutes=30, is_active=True)
            not_due = BinkPNode(
                name='NotDue', ftn_address='1200:1/61', password='x',
                network_id=net.id, binkp_host='notdue.example.test',
                poll_interval_minutes=30, last_poll_at=datetime.utcnow(),
                is_active=True)
            no_interval = BinkPNode(
                name='NoInterval', ftn_address='1200:1/62', password='x',
                network_id=net.id, binkp_host='nointerval.example.test',
                poll_interval_minutes=None, is_active=True)
            poll_in_only = BinkPNode(
                name='PollInOnly', ftn_address='1200:1/63', password='x',
                network_id=net.id, binkp_host=None,
                poll_interval_minutes=30, is_active=True)
            db.session.add_all([due_dialable, not_due, no_interval, poll_in_only])
            db.session.commit()

            polled_names = []

            def _fake_do_poll_node(app, node):
                polled_names.append(node.name)

            with patch.object(poller_mod, '_do_poll', lambda app, n: None), \
                 patch.object(poller_mod, '_do_poll_node', _fake_do_poll_node), \
                 patch.object(poller_mod, '_stop_event') as mock_event:
                # Run exactly one tick: first .wait() returns False (run
                # the body once), second returns True (stop the loop).
                mock_event.wait.side_effect = [False, True]
                poller_mod._poller_loop(self.app)

            # Assert containment/exclusion rather than exact list equality
            # -- this class shares one sqlite DB across all its tests
            # (setUpClass, not setUp), so earlier tests' own BinkPNode
            # rows are still real, active rows a full-table query like
            # _poller_loop's legitimately sees too.
            self.assertIn('Due', polled_names)
            self.assertNotIn('NotDue', polled_names)
            self.assertNotIn('NoInterval', polled_names)
            self.assertNotIn('PollInOnly', polled_names)


if __name__ == '__main__':
    unittest.main()
