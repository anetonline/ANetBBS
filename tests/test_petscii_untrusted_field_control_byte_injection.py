"""Regression test: petscii_ui.py built several single-line renders
(echomail/board/PM listing rows, echomail From/To/Subject headers) by
embedding raw DB content directly into an f-string, with none of the
sanitization _wrap_body()/_strip_for_petscii() already give multi-line
body text.

session.write()'s PETSCII encoder (petscii_codec.encode()) passes any
C0 (0x00-0x1F) or 0x80-0x9F byte straight through UNCHANGED as a real
PETSCII control code -- by design, for this module's own deliberately-
embedded control constants (CLR_HOME, REVERSE_ON, color bytes) -- so a
raw control byte reaching encode() from unsanitized DB content renders
as a live screen-control code on the viewing user's real terminal.
Echomail from_name/subject is the clearest real case: genuinely
untrusted data tossed in from an external FidoNet-style network node.

Fixed by routing every such field through the new _safe_field() helper
before it's embedded. This test drives the real listing/read functions
end-to-end against a real (sqlite) DB and asserts the written output
never contains the raw control bytes planted in the fixture data.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

_EVIL = '\x93Evil\x1b[2J\x1b[HName'  # PETSCII CLR_HOME + an ANSI CSI erase-screen


class _FakeSession:
    def __init__(self, user, responses):
        self.user = user
        self._responses = list(responses)
        self.written = []
        self._forced_width = 40

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

    async def clear_screen(self):
        self.written.append('[CLR]')

    @property
    def petscii_width(self):
        return self._forced_width

    def transcript(self):
        return ''.join(self.written)


class UntrustedFieldSanitizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_field_sanitize_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, EchomailNetwork, EchoArea,
                                    EchomailMessage, Board, Post, PrivateMessage)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100)
            bob = User(username='bob', email='bob@example.com',
                      password_hash='x', access_level=100)
            db.session.add_all([alice, bob])
            db.session.commit()
            cls.alice_id, cls.bob_id = alice.id, bob.id

            net = EchomailNetwork(name='Fidonet', network_type='binkp', is_active=True)
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='FIDO0', name='Fidonet Area',
                            is_active=True, is_subscribed=True, is_sysop_only=False,
                            min_access_level=10)
            db.session.add(area)
            db.session.flush()
            cls.area_id = area.id

            msg = EchomailMessage(
                area_id=area.id, network_id=net.id,
                from_name=_EVIL, to_name=_EVIL,
                subject=_EVIL, body='Hello from another BBS.')
            db.session.add(msg)

            board = Board(name='General', is_active=True, min_access_level=0,
                          min_write_level=0, order=0)
            db.session.add(board)
            db.session.flush()
            cls.board_id = board.id
            post = Post(board_id=board.id, author_id=alice.id,
                       subject=_EVIL, content='Hello world.')
            db.session.add(post)

            pm = PrivateMessage(sender_id=bob.id, recipient_id=alice.id,
                                subject=_EVIL, body='Hi there.')
            db.session.add(pm)
            db.session.commit()
            cls.msg_id = msg.id
            cls.post_id = post.id
            cls.pm_id = pm.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _alice(self):
        return {'id': self.alice_id, 'username': 'alice', 'access_level': 100}

    def _assert_clean(self, txt):
        self.assertNotIn('\x93', txt)
        self.assertNotIn('\x1b', txt)

    def test_echomail_listing_strips_control_bytes(self):
        from anetbbs.features.petscii_ui import _echo_messages
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_echo_messages(session, self.area_id, 'Area'))
        self._assert_clean(session.transcript())

    def test_echomail_message_read_strips_control_bytes(self):
        from anetbbs.features.petscii_ui import _echo_message_read
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_echo_message_read(session, self.msg_id, self.area_id, 'Area'))
        self._assert_clean(session.transcript())

    def test_board_thread_listing_strips_control_bytes(self):
        from anetbbs.features.petscii_ui import _board_threads
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_board_threads(session, self.board_id, 'General'))
        self._assert_clean(session.transcript())

    def test_board_thread_read_strips_control_bytes(self):
        from anetbbs.features.petscii_ui import _thread_read
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_thread_read(session, self.post_id, self.board_id, 'General'))
        self._assert_clean(session.transcript())

    def test_pm_listing_strips_control_bytes(self):
        from anetbbs.features.petscii_ui import _pm_menu
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_pm_menu(session))
        self._assert_clean(session.transcript())

    def test_pm_read_strips_control_bytes(self):
        from anetbbs.features.petscii_ui import _pm_read
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_pm_read(session, self.pm_id))
        self._assert_clean(session.transcript())


if __name__ == '__main__':
    unittest.main()
