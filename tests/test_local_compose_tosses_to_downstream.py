"""Regression tests for a real gap found live: a real sysop reported
never receiving a single ANotherNetwork packet despite dozens of local
messages existing in areas his node was correctly subscribed to (per
EchoAreaNode rows). Root cause: toss_message() (anetbbs/echomail/tosser.py)
was only ever called from the three INBOUND-import paths (poller.py,
binkp_server.py, qwk_hub_ftp.py) -- none of the three paths that let a
message originate LOCALLY (web/echomail.py's compose(), bbs_ui.py's
_compose_echomail(), petscii_ui.py's _echo_compose()) ever called it, so
a message composed directly on the hub just sat there, visible locally,
but was never queued into BinkPHoldQueue for any downstream node.

Each of the three composers got the identical three-line fix
(`from ..echomail.tosser import toss_message; toss_message(<id>)`) right
after the message is committed. This file drives all three end-to-end
and asserts a BinkPHoldQueue entry now exists for a subscribed node.
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
    """Same fail-fast fake used by test_petscii_ui_screens.py -- an
    empty response queue raises instead of silently defaulting, so a
    stuck while-True loop fails the test instead of hanging forever."""
    def __init__(self, user, responses, window_size=(80, 24)):
        self.user = user
        self._responses = list(responses)
        self.written = []
        self._forced_width = 40
        self.window_size = window_size

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    async def read_key_arrow(self):
        if not self._responses:
            raise AssertionError(
                'read_key_arrow() called with an empty scripted response queue')
        return self._responses.pop(0)

    async def clear_screen(self):
        self.written.append('[CLR]')

    @property
    def petscii_width(self):
        return self._forced_width


class LocalComposeTossesToDownstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.local_compose_toss_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, EchoArea, BinkPNode, EchoAreaNode
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            user = User(username='jerryreed', email='jerryreed@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

            net = EchomailNetwork(name='ANotherNetwork', network_type='binkp',
                                  is_active=True, our_address='1200:1/1')
            db.session.add(net)
            db.session.flush()
            cls.net_id = net.id

            node = BinkPNode(name='GateKeeper', ftn_address='1200:1/2',
                             password='', is_active=True, network_id=net.id)
            db.session.add(node)
            db.session.flush()
            cls.node_id = node.id

            def make_area(tag):
                area = EchoArea(network_id=net.id, tag=tag, name=tag,
                                is_active=True, is_subscribed=True,
                                is_sysop_only=False, min_access_level=10)
                db.session.add(area)
                db.session.flush()
                db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
                db.session.commit()
                return area.id

            cls.web_area_id = make_area('ANN.WEBTEST')
            cls.bbsui_area_id = make_area('ANN.BBSUITEST')
            cls.petscii_area_id = make_area('ANN.PETTEST')

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

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_web_compose_queues_message_for_subscribed_downstream_node(self):
        before = self._hold_queue_count()
        client = self._client_as(self.user_id)
        resp = client.post(f'/echomail/{self.web_area_id}/compose', data={
            'area_id': str(self.web_area_id),
            'to_name': 'All',
            'subject': 'Web compose toss test',
            'body': 'Hello from the web composer.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self._hold_queue_count(), before + 1,
            'web compose() must call toss_message() so a subscribed '
            'downstream BinkP node actually receives locally-posted mail')

    def test_terminal_compose_queues_message_for_subscribed_downstream_node(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        before = self._hold_queue_count()
        session = _FakeSession(
            {'id': self.user_id, 'username': 'jerryreed', 'is_admin': True,
             'access_level': 100},
            responses=['1', 'Q'])  # pick the only network, then Q out of area lightbar

        # Drive straight past the network/area lightbar picker by patching
        # it to immediately return the known test area (matches the
        # precedent in test_compose_echomail_area_lightbar.py of spying on
        # _rss_lightbar rather than scripting real arrow-key navigation).
        async def _pick_bbsui_area(self_ui, rows, *args, **kwargs):
            return ('enter', 0)

        async def _fake_launch_anedit(session, subject=None, username=None,
                                      tagline_picker=None):
            return 'Hello from the terminal composer.'

        line_inputs = iter(['1', 'All', 'Terminal compose toss test'])

        async def _read_line(prompt=''):
            return next(line_inputs, '')
        session.read_line = _read_line

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch.object(BBSMenuUI, '_rss_lightbar', _pick_bbsui_area), \
             patch('anetbbs.features.anedit.launch_anedit', _fake_launch_anedit):
            ui = BBSMenuUI(session)
            asyncio.run(ui.compose_echomail())

        self.assertEqual(
            self._hold_queue_count(), before + 1,
            'bbs_ui.py _compose_echomail() must call toss_message() so a '
            'subscribed downstream BinkP node actually receives '
            'locally-posted mail')

    def test_petscii_compose_queues_message_for_subscribed_downstream_node(self):
        from anetbbs.features.petscii_ui import _echo_compose

        before = self._hold_queue_count()
        session = _FakeSession(
            {'id': self.user_id, 'username': 'jerryreed', 'is_admin': True,
             'access_level': 100},
            responses=['All', 'PETSCII compose toss test',
                      'Hello from the PETSCII composer.', '/send', ''])

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(_echo_compose(session, self.petscii_area_id, 'ANN.PETTEST'))

        self.assertEqual(
            self._hold_queue_count(), before + 1,
            'petscii_ui.py _echo_compose() must call toss_message() so a '
            'subscribed downstream BinkP node actually receives '
            'locally-posted mail')


if __name__ == '__main__':
    unittest.main()
