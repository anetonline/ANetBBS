"""Regression tests for the real-name echomail/netmail posting policy
(Jerry's request: "Some networks require real names to be used in some
areas... should be a setting to use real names and or handle").

Covers:
  - resolve_post_name() (anetbbs/features/access_control.py) directly.
  - Model round-trips for the new columns.
  - All six local compose surfaces that construct an EchomailMessage/
    NetmailMessage from_name: web echomail.py's compose() and
    netmail_compose(), web netmail.py's compose(), web telegram.py's
    send(), terminal bbs_ui.py's _compose_echomail() and the reply-from-
    read path in read_echo_area(), and PETSCII petscii_ui.py's
    _echo_compose(). Each surface is checked both for the hard-block
    (area/network requires a real name, user hasn't set one) and the
    success path (real name used when set/required).
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
    """Same fail-fast fake used by test_local_compose_tosses_to_downstream.py
    -- an empty response queue raises instead of silently defaulting."""
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


class ResolvePostNameUnitTests(unittest.TestCase):
    def test_required_but_no_real_name_is_a_hard_block(self):
        from anetbbs.features.access_control import resolve_post_name
        name, err = resolve_post_name(
            {'real_name': '', 'echomail_name_pref': 'handle',
             'display_name': '', 'username': 'stingray'}, True)
        self.assertIsNone(name)
        self.assertIsNotNone(err)

    def test_required_and_real_name_set_uses_real_name(self):
        from anetbbs.features.access_control import resolve_post_name
        name, err = resolve_post_name(
            {'real_name': 'Jerry Reed', 'echomail_name_pref': 'handle',
             'display_name': '', 'username': 'stingray'}, True)
        self.assertEqual(name, 'Jerry Reed')
        self.assertIsNone(err)

    def test_not_required_pref_handle_uses_handle(self):
        from anetbbs.features.access_control import resolve_post_name
        name, err = resolve_post_name(
            {'real_name': 'Jerry Reed', 'echomail_name_pref': 'handle',
             'display_name': 'StingRay', 'username': 'stingray'}, False)
        self.assertEqual(name, 'StingRay')
        self.assertIsNone(err)

    def test_not_required_pref_real_name_uses_real_name(self):
        from anetbbs.features.access_control import resolve_post_name
        name, err = resolve_post_name(
            {'real_name': 'Jerry Reed', 'echomail_name_pref': 'real_name',
             'display_name': 'StingRay', 'username': 'stingray'}, False)
        self.assertEqual(name, 'Jerry Reed')
        self.assertIsNone(err)

    def test_not_required_pref_real_name_but_none_set_falls_back_to_handle(self):
        from anetbbs.features.access_control import resolve_post_name
        name, err = resolve_post_name(
            {'real_name': '', 'echomail_name_pref': 'real_name',
             'display_name': 'StingRay', 'username': 'stingray'}, False)
        self.assertEqual(name, 'StingRay')
        self.assertIsNone(err)

    def test_no_username_or_display_name_falls_back_to_anonymous(self):
        from anetbbs.features.access_control import resolve_post_name
        name, err = resolve_post_name(
            {'real_name': '', 'echomail_name_pref': 'handle',
             'display_name': '', 'username': ''}, False)
        self.assertEqual(name, 'Anonymous')
        self.assertIsNone(err)

    def test_duck_types_orm_style_object_via_getattr(self):
        from anetbbs.features.access_control import resolve_post_name

        class _FakeUser:
            real_name = 'Jerry Reed'
            echomail_name_pref = 'handle'
            display_name = 'StingRay'
            username = 'stingray'

        name, err = resolve_post_name(_FakeUser(), True)
        self.assertEqual(name, 'Jerry Reed')
        self.assertIsNone(err)


class RealNamePostingPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.real_name_policy_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, EchoArea
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            no_name = User(username='nonamerealtest', email='nnrt@example.com',
                           password_hash='x', access_level=100, is_admin=True,
                           real_name=None)
            has_name = User(username='hasnamerealtest', email='hnrt@example.com',
                            password_hash='x', access_level=100, is_admin=True,
                            real_name='Jerry Reed')
            db.session.add_all([no_name, has_name])
            db.session.commit()
            cls.no_name_id = no_name.id
            cls.has_name_id = has_name.id

            net = EchomailNetwork(name='RealNamePolicyNet', network_type='binkp',
                                  is_active=True, our_address='9:9/1',
                                  require_real_name_netmail=True)
            db.session.add(net)
            db.session.flush()
            cls.net_id = net.id

            open_net = EchomailNetwork(name='RealNamePolicyOpenNet',
                                       network_type='binkp', is_active=True,
                                       our_address='9:8/1',
                                       require_real_name_netmail=False)
            db.session.add(open_net)
            db.session.flush()
            cls.open_net_id = open_net.id

            area = EchoArea(network_id=net.id, tag='RNP.REQUIRED',
                            name='Real Name Required', is_active=True,
                            is_subscribed=True, is_sysop_only=False,
                            min_access_level=10, require_real_name=True)
            db.session.add(area)
            db.session.flush()
            cls.required_area_id = area.id

            open_area = EchoArea(network_id=net.id, tag='RNP.OPEN',
                                 name='Real Name Not Required', is_active=True,
                                 is_subscribed=True, is_sysop_only=False,
                                 min_access_level=10, require_real_name=False)
            db.session.add(open_area)
            db.session.commit()
            cls.open_area_id = open_area.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _last_message_from_name(self, model_name, **filters):
        from anetbbs import models
        Model = getattr(models, model_name)
        with self.app.app_context():
            row = Model.query.filter_by(**filters).order_by(Model.id.desc()).first()
            return row.from_name if row else None

    # -- model round-trip -------------------------------------------------

    def test_model_columns_round_trip(self):
        from anetbbs.models import User, EchoArea, EchomailNetwork
        with self.app.app_context():
            u = User.query.get(self.has_name_id)
            self.assertEqual(u.real_name, 'Jerry Reed')
            self.assertEqual(u.echomail_name_pref, 'handle')
            area = EchoArea.query.get(self.required_area_id)
            self.assertTrue(area.require_real_name)
            net = EchomailNetwork.query.get(self.net_id)
            self.assertTrue(net.require_real_name_netmail)

    # -- web echomail.py compose() -----------------------------------------

    def test_web_echomail_compose_blocked_without_real_name(self):
        client = self._client_as(self.no_name_id)
        resp = client.post(f'/echomail/{self.required_area_id}/compose', data={
            'area_id': str(self.required_area_id),
            'to_name': 'All',
            'subject': 'Should be blocked',
            'body': 'This should not be posted.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self._last_message_from_name(
            'EchomailMessage', area_id=self.required_area_id,
            subject='Should be blocked'))

    def test_web_echomail_compose_allowed_with_real_name(self):
        client = self._client_as(self.has_name_id)
        resp = client.post(f'/echomail/{self.required_area_id}/compose', data={
            'area_id': str(self.required_area_id),
            'to_name': 'All',
            'subject': 'Web echomail real name test',
            'body': 'This should post under the real name.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._last_message_from_name(
            'EchomailMessage', area_id=self.required_area_id,
            subject='Web echomail real name test'), 'Jerry Reed')

    # -- web echomail.py netmail_compose() (QWK netmail) --------------------

    def test_web_qwk_netmail_compose_blocked_without_real_name(self):
        from anetbbs.models import EchomailNetwork
        with self.app.app_context():
            EchomailNetwork.query.get(self.net_id).network_type = 'qwk'
            from anetbbs.models import db
            db.session.commit()
        try:
            client = self._client_as(self.no_name_id)
            resp = client.post('/echomail/netmail/compose', data={
                'network_id': str(self.net_id),
                'to_name': 'Some Recipient',
                'to_address': '',
                'subject': 'QWK netmail blocked test',
                'body': 'Should not send.',
            }, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIsNone(self._last_message_from_name(
                'EchomailMessage', network_id=self.net_id,
                subject='QWK netmail blocked test'))
        finally:
            with self.app.app_context():
                from anetbbs.models import db
                EchomailNetwork.query.get(self.net_id).network_type = 'binkp'
                db.session.commit()

    def test_web_qwk_netmail_compose_allowed_with_real_name(self):
        from anetbbs.models import EchomailNetwork, db
        with self.app.app_context():
            EchomailNetwork.query.get(self.net_id).network_type = 'qwk'
            db.session.commit()
        try:
            client = self._client_as(self.has_name_id)
            resp = client.post('/echomail/netmail/compose', data={
                'network_id': str(self.net_id),
                'to_name': 'Some Recipient',
                'to_address': '',
                'subject': 'QWK netmail allowed test',
                'body': 'Should send under real name.',
            }, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(self._last_message_from_name(
                'EchomailMessage', network_id=self.net_id,
                subject='QWK netmail allowed test'), 'Jerry Reed')
        finally:
            with self.app.app_context():
                EchomailNetwork.query.get(self.net_id).network_type = 'binkp'
                db.session.commit()

    # -- web netmail.py compose() (true FTN netmail) -------------------------

    def test_web_netmail_compose_blocked_without_real_name(self):
        client = self._client_as(self.no_name_id)
        resp = client.post('/netmail/compose', data={
            'to_address': '9:9/2',
            'to_name': 'Remote Sysop',
            'subject': 'FTN netmail blocked test',
            'body': 'Should not send.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self._last_message_from_name(
            'NetmailMessage', network_id=self.net_id,
            subject='FTN netmail blocked test'))

    def test_web_netmail_compose_allowed_with_real_name(self):
        client = self._client_as(self.has_name_id)
        resp = client.post('/netmail/compose', data={
            'to_address': '9:9/2',
            'to_name': 'Remote Sysop',
            'subject': 'FTN netmail allowed test',
            'body': 'Should send under real name.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._last_message_from_name(
            'NetmailMessage', network_id=self.net_id,
            subject='FTN netmail allowed test'), 'Jerry Reed')

    def test_web_netmail_compose_not_gated_on_network_without_the_policy(self):
        client = self._client_as(self.no_name_id)
        resp = client.post('/netmail/compose', data={
            'to_address': '9:8/2',
            'to_name': 'Remote Sysop',
            'subject': 'FTN netmail open network test',
            'body': 'Should send under handle.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._last_message_from_name(
            'NetmailMessage', network_id=self.open_net_id,
            subject='FTN netmail open network test'), 'nonamerealtest')

    # -- web telegram.py send() ----------------------------------------------

    def test_telegram_send_blocked_without_real_name(self):
        client = self._client_as(self.no_name_id)
        resp = client.post('/telegram/send', data={
            'to_address': '9:9/3',
            'to_name': 'Remote Sysop',
            'body': 'Telegram blocked test body marker.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            row = (NetmailMessage.query
                   .filter_by(network_id=self.net_id)
                   .filter(NetmailMessage.body.contains(
                       'Telegram blocked test body marker.'))
                   .first())
            self.assertIsNone(row)

    def test_telegram_send_allowed_with_real_name(self):
        client = self._client_as(self.has_name_id)
        resp = client.post('/telegram/send', data={
            'to_address': '9:9/3',
            'to_name': 'Remote Sysop',
            'body': 'Telegram real name test body.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            row = (NetmailMessage.query
                   .filter_by(network_id=self.net_id)
                   .filter(NetmailMessage.body.contains('Telegram real name test body.'))
                   .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(row)
            self.assertEqual(row.from_name, 'Jerry Reed')

    # -- terminal bbs_ui.py _compose_echomail() -------------------------------

    def test_terminal_compose_echomail_blocked_without_real_name(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = _FakeSession(
            {'id': self.no_name_id, 'username': 'nonamerealtest',
             'is_admin': True, 'access_level': 100},
            responses=[])

        async def _pick_required_area(self_ui, rows, *args, **kwargs):
            for idx, row in enumerate(rows):
                if row[0] == self.required_area_id:
                    return ('enter', idx)
            raise AssertionError('required area not found in rows')

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch.object(BBSMenuUI, '_rss_lightbar', _pick_required_area):
            ui = BBSMenuUI(session)
            line_inputs = iter(['1'])

            async def _read_line(prompt=''):
                return next(line_inputs, 'Q')
            session.read_line = _read_line
            asyncio.run(ui.compose_echomail())

        self.assertTrue(
            any('requires your real name' in w for w in session.written),
            'terminal compose should show the real-name-required rejection')

    def test_terminal_compose_echomail_allowed_with_real_name(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = _FakeSession(
            {'id': self.has_name_id, 'username': 'hasnamerealtest',
             'is_admin': True, 'access_level': 100, 'real_name': 'Jerry Reed'},
            responses=[])

        async def _pick_required_area(self_ui, rows, *args, **kwargs):
            for idx, row in enumerate(rows):
                if row[0] == self.required_area_id:
                    return ('enter', idx)
            raise AssertionError('required area not found in rows')

        async def _fake_launch_anedit(session, subject=None, username=None,
                                      tagline_picker=None):
            return 'Body composed under real name.'

        line_inputs = iter(['1', 'All', 'Terminal real name allowed test'])

        async def _read_line(prompt=''):
            return next(line_inputs, '')

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch.object(BBSMenuUI, '_rss_lightbar', _pick_required_area), \
             patch('anetbbs.features.anedit.launch_anedit', _fake_launch_anedit):
            ui = BBSMenuUI(session)
            session.read_line = _read_line
            asyncio.run(ui.compose_echomail())

        self.assertEqual(self._last_message_from_name(
            'EchomailMessage', area_id=self.required_area_id,
            subject='Terminal real name allowed test'), 'Jerry Reed')

    # -- terminal bbs_ui.py read_echo_area() reply/new path -------------------

    def test_terminal_reply_from_read_blocked_without_real_name(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        from anetbbs.models import db, EchomailMessage

        with self.app.app_context():
            seed = EchomailMessage(
                area_id=self.required_area_id, network_id=self.net_id,
                from_name='Seed Poster', to_name='All',
                subject='Seed message to reply to', body='Seed body.',
                direction='inbound')
            db.session.add(seed)
            db.session.commit()

        session = _FakeSession(
            {'id': self.no_name_id, 'username': 'nonamerealtest',
             'is_admin': True, 'access_level': 100},
            responses=[''])

        _lightbar_calls = iter([('enter', 0)])

        async def _fake_lightbar(self_ui, rows, *args, **kwargs):
            return next(_lightbar_calls, ('quit',))

        async def _fake_launch_aneview(session, body, **kwargs):
            return 'reply'

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch.object(BBSMenuUI, '_rss_lightbar', _fake_lightbar), \
             patch('anetbbs.features.anedit.launch_aneview', _fake_launch_aneview):
            ui = BBSMenuUI(session)
            asyncio.run(ui.read_echo_area(self.required_area_id, 'RNP.REQUIRED'))

        self.assertTrue(
            any('requires your real name' in w for w in session.written),
            'reply-from-read should show the real-name-required rejection')

    # -- PETSCII petscii_ui.py _echo_compose() ---------------------------------

    def test_petscii_echo_compose_blocked_without_real_name(self):
        from anetbbs.features.petscii_ui import _echo_compose

        session = _FakeSession(
            {'id': self.no_name_id, 'username': 'nonamerealtest',
             'is_admin': True, 'access_level': 100},
            responses=[''])

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(_echo_compose(
                session, self.required_area_id, 'RNP.REQUIRED'))

        self.assertTrue(
            any('requires your real name' in w for w in session.written),
            'PETSCII compose should show the real-name-required rejection')

    def test_petscii_echo_compose_allowed_with_real_name(self):
        from anetbbs.features.petscii_ui import _echo_compose

        session = _FakeSession(
            {'id': self.has_name_id, 'username': 'hasnamerealtest',
             'is_admin': True, 'access_level': 100, 'real_name': 'Jerry Reed'},
            responses=['All', 'PETSCII real name allowed test',
                      'Body under real name.', '/send', ''])

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(_echo_compose(
                session, self.required_area_id, 'RNP.REQUIRED'))

        self.assertEqual(self._last_message_from_name(
            'EchomailMessage', area_id=self.required_area_id,
            subject='PETSCII real name allowed test'), 'Jerry Reed')


if __name__ == '__main__':
    unittest.main()
