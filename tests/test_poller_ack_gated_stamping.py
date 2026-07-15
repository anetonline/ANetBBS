"""Regression test for the other half of a real mail-loss bug found in
a full-subsystem BinkP audit: even after binkp.py's _send_messages()
was fixed to correctly return 0 when the hub doesn't acknowledge our
outbound packet (see test_binkp_send_ack_gating.py), poller.py's
_do_poll() still unconditionally stamped every queued outbound message
as sent -- with no check of result['sent'] at all. Fixing only one of
the two files would NOT have actually fixed the end-to-end behavior:
a busy/unstable hub replying M_SKIP or M_ERR still caused every queued
message to be marked delivered here and never retried, even though the
BinkP layer correctly reported failure.

Fix: the "stamp outbound messages as sent" block in _do_poll() is now
gated on `result.get('sent', 0)` being truthy, matching how
_send_messages() itself gates its return value on the hub's actual
M_GOT/M_SKIP/M_ERR reply.

Uses the same real-Flask-app-plus-sqlite pattern as
test_poller_self_referential.py, patching poller._run_client directly
so no real network I/O happens.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AckGatedStampingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poller_ackgate_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

        from anetbbs.models import db
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_network_with_queued_message(self, name):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage

        net = EchomailNetwork(
            name=name, network_type='binkp',
            binkp_host='peer.example.test', binkp_port=24554,
            our_address='1:114/30', hub_address='1:114/0',
            is_active=True,
        )
        db.session.add(net)
        db.session.commit()

        area = EchoArea(network_id=net.id, tag='TEST.ECHO', name='Test Echo')
        db.session.add(area)
        db.session.commit()

        msg = EchomailMessage(
            area_id=area.id, network_id=net.id,
            from_name='Sysop', to_name='All', subject='queued outbound',
            body='hello', direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()
        return net, msg

    def test_hub_skip_leaves_message_queued_for_retry(self):
        """result['sent'] == 0 (hub SKIP/ERR/no-ack) must NOT stamp
        sent_at -- the message should still be queued next poll."""
        from anetbbs.models import db, EchomailMessage
        from anetbbs.echomail import poller

        with self.app.app_context():
            net, msg = self._make_network_with_queued_message('SkipTestNet')
            msg_id = msg.id

            with patch.object(poller, '_run_client',
                              return_value={'sent': 0, 'received': [],
                                            'hatched_ids': []}):
                poller._do_poll(self.app, net)

            refreshed = db.session.get(EchomailMessage, msg_id)
            self.assertIsNone(
                refreshed.sent_at,
                'message must stay queued (sent_at NULL) when the hub '
                'did not acknowledge the batch')

    def test_hub_got_ack_stamps_message_sent(self):
        """result['sent'] nonzero (hub GOT) DOES stamp sent_at -- the
        success path must keep working."""
        from anetbbs.models import db, EchomailMessage
        from anetbbs.echomail import poller

        with self.app.app_context():
            net, msg = self._make_network_with_queued_message('GotTestNet')
            msg_id = msg.id

            with patch.object(poller, '_run_client',
                              return_value={'sent': 1, 'received': [],
                                            'hatched_ids': []}):
                poller._do_poll(self.app, net)

            refreshed = db.session.get(EchomailMessage, msg_id)
            self.assertIsNotNone(
                refreshed.sent_at,
                'message must be stamped sent when the hub acknowledged '
                'the batch')


if __name__ == '__main__':
    unittest.main()
