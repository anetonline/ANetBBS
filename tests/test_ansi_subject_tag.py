"""Tests for maybe_tag_ansi_subject() (anetbbs/models.py) -- prepends
'[ANSI] ' to a message subject when its body contains real ANSI escape
sequences, so a reader knows to expect colored/box-drawing content
before opening it.

Requested by the sysop after a real incident: a message from a peer
(Codefenix, on his Synchronet BBS) arrived with a plain subject even
though its body was full CP437/ANSI art -- readers on non-ANSI-capable
clients had no warning before opening it. Wired into all 9 places a
message subject gets set: the 3 inbound import paths (poller.py,
binkp_server.py, qwk_hub_ftp.py) and the 6 local compose surfaces (web
echomail compose + netmail_compose, web netmail.py, web telegram.py,
terminal bbs_ui.py's two compose paths, PETSCII's _echo_compose) --
this file focuses on the shared helper's own logic; integration tests
elsewhere already exercise several of those call sites end-to-end.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod
from anetbbs.models import maybe_tag_ansi_subject


def test_plain_text_body_leaves_subject_unchanged():
    assert maybe_tag_ansi_subject('Hello', 'just plain text') == 'Hello'


def test_cp437_block_chars_without_ansi_codes_do_not_tag():
    # Box-drawing characters alone (no real escape sequences) show up
    # constantly in ordinary quoted replies/taglines -- must NOT trigger
    # the tag on their own.
    body = 'plain \xdb\xdb\xdb block chars, no escape codes'
    assert maybe_tag_ansi_subject('Hello', body) == 'Hello'


def test_real_ansi_escape_sequence_tags_the_subject():
    body = '\x1b[31mred text\x1b[0m'
    assert maybe_tag_ansi_subject('ConstructiveChaos BBS', body) == \
        '[ANSI] ConstructiveChaos BBS'


def test_already_tagged_subject_is_not_double_tagged():
    body = '\x1b[31mred text\x1b[0m'
    result = maybe_tag_ansi_subject('[ANSI] Already tagged', body)
    assert result == '[ANSI] Already tagged'
    assert result.count('[ANSI]') == 1


def test_already_tagged_subject_is_case_insensitively_detected():
    body = '\x1b[31mred text\x1b[0m'
    result = maybe_tag_ansi_subject('[ansi] lowercase tag', body)
    assert result.count('ANSI') + result.count('ansi') == 1


def test_empty_or_none_body_leaves_subject_unchanged():
    assert maybe_tag_ansi_subject('Hello', '') == 'Hello'
    assert maybe_tag_ansi_subject('Hello', None) == 'Hello'


def test_empty_subject_with_ansi_body_still_tags():
    body = '\x1b[31mred text\x1b[0m'
    assert maybe_tag_ansi_subject('', body) == '[ANSI]'
    assert maybe_tag_ansi_subject(None, body) == '[ANSI]'


class InboundImportAnsiTagTests(unittest.TestCase):
    """Confirms the tag actually reaches a real inbound-imported row --
    the shared helper's own logic is covered above; this is the wiring
    check for the specific surface matching Jerry's real report (a
    peer-sent message arriving via BinkP/poller with ANSI content)."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ansi_subject_tag_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

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

    def test_inbound_message_with_ansi_body_gets_tagged_subject(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='AnsiTagTestNet', network_type='binkp')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='ANSITAG.TEST',
                            name='Ansi Tag Test', is_active=True,
                            is_subscribed=True)
            db.session.add(area)
            db.session.commit()

            msg_data = {
                'area_tag': 'ANSITAG.TEST', 'msg_id': 'ANSITAGTEST_1',
                'from_name': 'Codefenix', 'to_name': 'All',
                'subject': 'ConstructiveChaos BBS',
                'body': '\x1b[31mred ANSI art\x1b[0m',
            }
            result = _import_message(net, msg_data)
            self.assertEqual(result, 1)

            msg = EchomailMessage.query.filter_by(
                area_id=area.id, msg_id='ANSITAGTEST_1').first()
            self.assertIsNotNone(msg)
            self.assertEqual(msg.subject, '[ANSI] ConstructiveChaos BBS')

    def test_local_web_compose_with_ansi_body_gets_tagged_subject(self):
        """One representative local-compose surface (web echomail
        compose()) -- all 6 local compose surfaces call the same shared
        maybe_tag_ansi_subject() helper identically, so the risk here is
        a wiring typo, not per-surface logic divergence; the helper's
        own logic is already covered exhaustively above."""
        from anetbbs.models import db, User, EchomailNetwork, EchoArea, EchomailMessage
        with self.app.app_context():
            user = User(username='ansitagwebuser', email='atwu@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            net = EchomailNetwork(name='AnsiTagWebNet', network_type='binkp',
                                  is_active=True, our_address='9:9/1')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='ANSITAG.WEB',
                            name='Ansi Tag Web Test', is_active=True,
                            is_subscribed=True, is_sysop_only=False,
                            min_access_level=10)
            db.session.add(area)
            db.session.commit()
            user_id = user.id
            area_id = area.id

        client = self._client_as(user_id)
        resp = client.post(f'/echomail/{area_id}/compose', data={
            'area_id': str(area_id),
            'to_name': 'All',
            'subject': 'Local compose ansi test',
            'body': '\x1b[32mgreen art\x1b[0m',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            msg = EchomailMessage.query.filter(
                EchomailMessage.area_id == area_id,
                EchomailMessage.subject.contains('Local compose ansi test')
            ).first()
            self.assertIsNotNone(msg)
            self.assertEqual(msg.subject, '[ANSI] Local compose ansi test')


if __name__ == '__main__':
    unittest.main()
