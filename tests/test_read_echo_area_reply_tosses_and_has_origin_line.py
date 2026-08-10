"""Regression test for a real bug Jerry found live: he replied to a
test echomail message on a real FTN network from the terminal, and the
posted message had no "* Origin:" line -- and, worse, may never have
reached the network at all.

Root cause: read_echo_area()'s inline reply composer
(anetbbs/features/bbs_ui.py) is a FOURTH local-compose write path into
EchomailMessage, separate from _compose_echomail() (the dedicated
"Compose Echomail" menu item), web/echomail.py's compose() route, and
petscii_ui.py's _echo_compose(). The other three were already fixed
for the "never calls toss_message()" gap (see
test_local_compose_tosses_to_downstream.py) and already set
tear_line/origin_line from ECHOMAIL_TEAR_LINE/ECHOMAIL_ORIGIN_LINE --
read_echo_area()'s reply branch got neither fix, so a terminal reply
sat in the local DB (visible on read-back, hence "no origin line")
but was never queued into BinkPHoldQueue for any subscribed downstream
node at all.

This test drives the real reply flow (read an existing message, press
R) with a real downstream BinkPNode subscribed to the area, and
asserts both effects: a BinkPHoldQueue row appears, and the new
message's tear_line/origin_line match the configured values.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, keys):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.written = []
        self._keys = list(keys)

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._keys.pop(0) if self._keys else 'Q'

    async def read_line(self, prompt=''):
        return ''


class ReadEchoAreaReplyTossesAndHasOriginLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.read_echo_area_toss_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, EchomailNetwork, EchoArea,
                                    EchomailMessage, BinkPNode, EchoAreaNode)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            net = EchomailNetwork(name='ANotherNetwork', network_type='binkp',
                                  our_address='1200:1/1', is_active=True)
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='ANN.REPLYTEST', name='Reply Test',
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.flush()
            cls.area_id = area.id

            node = BinkPNode(name='GateKeeper', ftn_address='1200:1/2',
                             password='', is_active=True, network_id=net.id)
            db.session.add(node)
            db.session.flush()
            cls.node_id = node.id
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))

            msg = EchomailMessage(area_id=area.id, network_id=net.id,
                                  from_name='Mojo', to_name='All',
                                  subject='Just joined', body='Testing this out.',
                                  direction='inbound')
            db.session.add(msg)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _hold_queue_count(self):
        from anetbbs.models import BinkPHoldQueue
        with self.app.app_context():
            return BinkPHoldQueue.query.filter_by(node_id=self.node_id).count()

    def test_reply_tosses_to_subscribed_downstream_node_and_sets_origin_line(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        from anetbbs.models import EchomailMessage

        before = self._hold_queue_count()
        session = _FakeSession(keys=['ENTER'])
        ui = BBSMenuUI(session)

        async def _fake_launch_aneview(*args, **kwargs):
            return 'reply'

        async def _fake_launch_anedit(*args, **kwargs):
            return 'Got you here!\n\nWelcome aboard!\n\n\n-StingRay'

        with self.app.app_context():
            expected_tear = self.app.config.get('ECHOMAIL_TEAR_LINE', '--- ANetBBS v1.0')
            expected_origin = self.app.config.get('ECHOMAIL_ORIGIN_LINE', 'ANetBBS')

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_aneview', _fake_launch_aneview), \
             patch('anetbbs.features.anedit.launch_anedit', _fake_launch_anedit):
            asyncio.run(ui.read_echo_area(self.area_id, 'Reply Test'))

        self.assertEqual(
            self._hold_queue_count(), before + 1,
            "read_echo_area()'s reply branch must call toss_message() so a "
            'subscribed downstream BinkP node actually receives the reply')

        with self.app.app_context():
            reply = (EchomailMessage.query
                    .filter_by(area_id=self.area_id, direction='outbound')
                    .order_by(EchomailMessage.id.desc()).first())
            self.assertIsNotNone(reply, 'reply message was not saved')
            self.assertEqual(reply.tear_line, expected_tear)
            self.assertEqual(reply.origin_line, expected_origin)


if __name__ == '__main__':
    unittest.main()
