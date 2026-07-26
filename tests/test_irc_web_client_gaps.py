"""Regression tests for real bugs found live in the web IRC client
(anetbbs/web/irc_web.py + templates/irc/index.html), reported by the
sysop after using it and having to fall back to the terminal client:

1. The connect form's default server/port/SSL/nick/channels fell back
   to a hardcoded 'irc.libera.chat'/6667/no-SSL/'' whenever a user had
   no saved IrcServerConfig -- completely ignoring Admin -> IRC Server
   Presets (IrcPreset), which only ever drove the TERMINAL IRC client
   (features/irc_chat.py). index() now falls back to the sysop's
   top-priority active preset instead.
2. A PRIVMSG/NOTICE whose IRC "target" is the user's OWN nick (i.e. any
   direct message -- the protocol's target param is always the
   recipient) got routed/bucketed by that same self-nick client-side,
   so multiple different senders' DMs (NickServ, ChanServ, other
   users) all landed in one shared, confusingly-labeled bucket instead
   of a per-sender pane. Now routed by sender instead.
3. `/msg target text` never echoed anything back to the sender (IRC
   servers don't echo your own PRIVMSGs) -- e.g. `/msg nickserv
   identify ...` looked like it silently did nothing.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class IrcWebClientGapsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.irc_web_gaps_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_user(self, username):
        from anetbbs.models import db, User
        u = User(username=username, email=f'{username}@example.com',
                password_hash='x', access_level=10)
        db.session.add(u)
        db.session.commit()
        return u

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
        return client

    def test_connect_form_defaults_to_sysop_preset_not_hardcoded_literal(self):
        from anetbbs.models import db, IrcPreset

        with self.app.app_context():
            user = self._make_user('ircdefaults_user')
            db.session.add(IrcPreset(
                name='Home Network', server='irc.home-network.example',
                port=6697, use_ssl=True, default_nick='HomeNick',
                channels='#lobby,#help', is_active=True, order=0))
            db.session.add(IrcPreset(
                name='Lower priority', server='irc.other.example',
                port=6667, is_active=True, order=5))
            db.session.commit()
            user_id = user.id

        resp = self._client_as(user_id).get('/irc/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('irc.home-network.example', body)
        self.assertIn('6697', body)
        self.assertNotIn('irc.libera.chat', body)

    def test_connect_form_still_falls_back_to_libera_with_no_presets_configured(self):
        from anetbbs.models import db, IrcPreset

        with self.app.app_context():
            # Other tests in this class add active presets to the same
            # shared class-level db -- explicitly clear them so this test
            # verifies the true no-presets-at-all fallback path.
            IrcPreset.query.delete()
            db.session.commit()
            user = self._make_user('ircnodefaults_user')
            user_id = user.id

        resp = self._client_as(user_id).get('/irc/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('irc.libera.chat', resp.get_data(as_text=True))

    def test_connect_form_prefers_users_own_saved_config_over_sysop_preset(self):
        from anetbbs.models import db, IrcPreset, IrcServerConfig

        with self.app.app_context():
            user = self._make_user('ircsaved_user')
            db.session.add(IrcPreset(
                name='Sysop Default', server='irc.sysop-default.example',
                port=6667, is_active=True, order=0))
            db.session.add(IrcServerConfig(
                user_id=user.id, server='irc.my-own-choice.example',
                port=6697, use_ssl=True, nick='MyNick'))
            db.session.commit()
            user_id = user.id

        resp = self._client_as(user_id).get('/irc/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('irc.my-own-choice.example', body)
        self.assertNotIn('irc.sysop-default.example', body)

    def test_direct_message_to_self_routes_by_sender_not_own_nick(self):
        """Protocol-level check on _IrcSession._handle_line: a PRIVMSG
        targeted at our own nick (any DM) must be re-keyed to the
        sender so the client can bucket per-conversation, not into one
        shared tab literally named after ourselves."""
        from anetbbs.web.irc_web import _IrcSession

        sess = _IrcSession('fake-sid', 'irc.example.com', 6667, False,
                           'MyNick', 'myuser', 'My Real Name', user_id=1)
        captured = []
        with patch.object(_IrcSession, '_emit',
                          lambda self, event, payload: captured.append((event, payload))):
            sess._handle_line(':NickServ!services@example.com PRIVMSG MyNick :Password accepted.')
            sess._handle_line(':NickServ!services@example.com NOTICE MyNick :You are now identified.')

        self.assertEqual(len(captured), 2)
        msg_event, msg_payload = captured[0]
        self.assertEqual(msg_event, 'irc_message')
        self.assertEqual(msg_payload['target'], 'NickServ')

        notice_event, notice_payload = captured[1]
        self.assertEqual(notice_event, 'irc_notice')
        self.assertEqual(notice_payload['target'], 'NickServ')

    def test_channel_message_target_unaffected_by_self_dm_routing_fix(self):
        from anetbbs.web.irc_web import _IrcSession

        sess = _IrcSession('fake-sid', 'irc.example.com', 6667, False,
                           'MyNick', 'myuser', 'My Real Name', user_id=1)
        captured = []
        with patch.object(_IrcSession, '_emit',
                          lambda self, event, payload: captured.append((event, payload))):
            sess._handle_line(':SomeUser!u@example.com PRIVMSG #lobby :hello there')

        self.assertEqual(len(captured), 1)
        event, payload = captured[0]
        self.assertEqual(event, 'irc_message')
        self.assertEqual(payload['target'], '#lobby')


if __name__ == '__main__':
    unittest.main()
