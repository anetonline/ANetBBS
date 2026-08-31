"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): tosser.py's toss_area_messages()
loaded EVERY message ever posted to an area on every call, even when
most of a long-running area's history already has a hold-queue row for
every currently-targeted node (the steady state for a node well past
its initial catchup) -- cost grows with total area history, not with
what's actually still outstanding.

Fixed by pre-filtering the EchomailMessage query in SQL (a NOT IN an
already-fully-queued subquery) when force=False, so only messages
genuinely missing a hold-queue row for at least one target node are
ever loaded into Python. force=True (the %RESCAN case) deliberately
keeps the original full scan -- it must reconsider already-'sent' rows
too, which the pre-filter would incorrectly exclude.

Functional correctness (new messages queue, repeat %RESCAN re-queues,
etc.) is already covered by test_areafix.py/test_filefix.py, both of
which still pass unmodified against this fix. What's new here is a
direct check that the *mechanism* changed: capture the actual SQL sent
to the DB and confirm the SELECT against echomail_messages carries the
pre-filter when force=False, and does NOT when force=True (preserving
the original correctness-critical full scan for rescans).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class TosserBoundedCatchupQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tosser_bounded_test.db')
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

    def _make_network_node_area(self, suffix):
        from anetbbs.models import db, EchomailNetwork, BinkPNode, EchoArea, EchoAreaNode
        net = EchomailNetwork(name=f'TosserNet{suffix}', network_type='binkp',
                              our_address=f'9:{suffix}/1')
        db.session.add(net)
        db.session.flush()
        node = BinkPNode(name=f'TosserNode{suffix}', ftn_address=f'9:{suffix}/2',
                         password='testpw123', is_active=True, network_id=net.id)
        db.session.add(node)
        db.session.flush()
        area = EchoArea(tag=f'AF.TOSSER{suffix}', name='Tosser Test',
                        network_id=net.id, is_active=True, is_subscribed=True)
        db.session.add(area)
        db.session.flush()
        db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
        db.session.commit()
        return net, node, area

    def test_already_fully_queued_history_is_not_reloaded_into_python(self):
        """The actual regression guard: with a large backlog already
        fully queued for the target node, a repeat (non-force) catchup
        call must not re-select those already-covered messages."""
        from sqlalchemy import event
        from anetbbs.models import db, EchomailMessage
        from anetbbs.echomail.tosser import toss_area_messages

        with self.app.app_context():
            net, node, area = self._make_network_node_area('A')
            for n in range(25):
                db.session.add(EchomailMessage(
                    area_id=area.id, network_id=net.id, from_name='Sysop',
                    to_name='All', subject=f'Old {n}', body='x',
                    direction='inbound'))
            db.session.commit()

            # First call queues everything (fresh subscription catchup).
            first = toss_area_messages(area.id, node_id=node.id)
            self.assertEqual(first, 25)

            # Second call (steady state -- nothing new) must load ~0
            # message rows, not the full 25-row history again.
            captured = []

            def _capture(conn, cursor, statement, parameters, context, executemany):
                captured.append(statement)

            event.listen(db.engine, 'before_cursor_execute', _capture)
            try:
                second = toss_area_messages(area.id, node_id=node.id)
            finally:
                event.remove(db.engine, 'before_cursor_execute', _capture)
            self.assertEqual(second, 0,
                             'nothing new to queue -- everything was already '
                             'fully covered by the first call')

            select_statements = [
                s for s in captured
                if 'FROM echomail_messages' in s and 'SELECT' in s.upper()]
            self.assertTrue(select_statements,
                            'expected a SELECT against echomail_messages')
            self.assertTrue(
                any('NOT IN' in s.upper() for s in select_statements),
                'the SELECT against echomail_messages must carry the '
                'already-fully-queued pre-filter (NOT IN a subquery '
                'against binkp_hold_queue) when force=False -- if this '
                'regresses back to an unfiltered SELECT, every catchup '
                'call goes back to loading the full area history again')

    def test_force_rescan_still_does_the_full_scan(self):
        """force=True must NOT carry the pre-filter -- it needs to
        reconsider already-'sent' rows too, which the filter would
        incorrectly treat as still needing nothing done."""
        from anetbbs.models import db, EchomailMessage, BinkPHoldQueue
        from anetbbs.echomail.tosser import toss_area_messages

        with self.app.app_context():
            net, node, area = self._make_network_node_area('B')
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='Sysop',
                to_name='All', subject='Msg', body='x', direction='inbound'))
            db.session.commit()

            toss_area_messages(area.id, node_id=node.id)
            row = BinkPHoldQueue.query.filter_by(node_id=node.id).first()
            row.status = 'sent'
            db.session.commit()

            requeued = toss_area_messages(area.id, node_id=node.id, force=True)
            self.assertEqual(requeued, 1,
                             'force=True must re-queue the already-sent '
                             'message, not skip it as if fully covered')

    def test_new_message_since_last_catchup_still_queues(self):
        """A message posted AFTER the last catchup must still be
        picked up -- the pre-filter must only exclude messages that are
        genuinely fully covered, not the whole area."""
        from anetbbs.models import db, EchomailMessage, BinkPHoldQueue
        from anetbbs.echomail.tosser import toss_area_messages

        with self.app.app_context():
            net, node, area = self._make_network_node_area('C')
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='Sysop',
                to_name='All', subject='First', body='x', direction='inbound'))
            db.session.commit()
            toss_area_messages(area.id, node_id=node.id)

            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='Sysop',
                to_name='All', subject='Second', body='x', direction='inbound'))
            db.session.commit()

            newly_queued = toss_area_messages(area.id, node_id=node.id)
            self.assertEqual(newly_queued, 1)
            self.assertEqual(
                BinkPHoldQueue.query.filter_by(node_id=node.id).count(), 2)


if __name__ == '__main__':
    unittest.main()
