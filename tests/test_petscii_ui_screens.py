"""Regression tests for anetbbs.features.petscii_ui's Phase 1 menu
screens (message boards, echomail, private messages, file areas,
who's-online, profile) -- the real screens built after the login
vertical slice was confirmed working on real SyncTERM/PETSCII hardware
(see the "PETSCII Terminal Support (Phase 1)" plan's staged build
order). These reuse the same data-layer models/queries the ANSI
screens use, never their rendering code.

Uses the same _patched_app() pattern already established by
tests/test_compose_echomail_area_lightbar.py (and siblings) for testing
bbs_ui.py-adjacent terminal features against a real, shared SQLite DB:
petscii_ui.py's _app_ctx() calls anetbbs.features.bbs_ui._app()
internally, so patching that one function point makes every DB-touching
screen in this file use the SAME test app/db instead of each
constructing its own fresh (and differently-configured) Flask app.
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
    """Feeds a queue of canned read_line() responses (one per call, in
    order) and captures every write() call as a joined transcript."""
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
            # Fail fast and loud, not silently spin forever: an empty
            # string doesn't match any menu's 'Q'/exit condition, so a
            # queue that runs dry inside a `while True:` menu loop hangs
            # the test at 100% CPU instead of failing -- caught live
            # when a first draft of this exact test file did precisely
            # that for several CPU-minutes before being killed manually.
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty -- add another response '
                'or fix the menu flow, do not let this fall through to a '
                'default value inside a while-True loop')
        return self._responses.pop(0)

    async def clear_screen(self):
        self.written.append('[CLR]')

    @property
    def petscii_width(self):
        return self._forced_width

    def transcript(self):
        return ''.join(self.written)


class PetsciiScreensTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_ui_screens_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, Board, EchomailNetwork, EchoArea,
                                    FileArea, FileUpload, UserSession)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100, display_name='Alice A.',
                        location='Nowhere', bio='A test user.', login_count=5)
            db.session.add(alice)
            bob = User(username='bob', email='bob@example.com',
                      password_hash='x', access_level=100)
            db.session.add(bob)
            db.session.commit()
            cls.alice_id = alice.id
            cls.bob_id = bob.id

            board = Board(name='General Chat', description='x', is_active=True,
                          min_access_level=10, order=1)
            db.session.add(board)
            db.session.commit()
            cls.board_id = board.id

            net = EchomailNetwork(name='ANotherNetwork', network_type='qwk',
                                  is_active=True)
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='ANET_GEN', name='General',
                            is_active=True, is_subscribed=True,
                            is_sysop_only=False, min_access_level=10)
            db.session.add(area)
            db.session.commit()
            cls.net_id = net.id
            cls.area_id = area.id

            farea = FileArea(name='Test Files', tag='TESTFILES', is_active=True,
                             is_sysop_only=False, min_access_level=10,
                             storage_path=None)
            db.session.add(farea)
            db.session.commit()
            cls.farea_id = farea.id
            upload = FileUpload(uploader_id=alice.id, filename='doc.txt',
                                original_filename='doc.txt', file_path='/tmp/doc.txt',
                                file_size=2048, description='A test file.',
                                is_public=True, file_area_id=farea.id)
            db.session.add(upload)
            db.session.commit()

            usess = UserSession(user_id=alice.id, page='[telnet] main menu')
            db.session.add(usess)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _alice_user(self):
        return {'id': self.alice_id, 'username': 'alice', 'is_admin': False,
                'access_level': 100}


class BoardsScreenTests(PetsciiScreensTestCase):
    def test_post_new_thread_then_read_it_back(self):
        from anetbbs.features.petscii_ui import _boards_menu
        # 1(board) -> N(new post) -> subject -> body lines -> /send -> ''(press
        # ENTER after "Posted.") -> Q(back to board list) -> Q(exit boards menu)
        session = _FakeSession(self._alice_user(),
                               ['1', 'N', 'Hello Board', 'First line', 'Second line',
                                '/send', '', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_boards_menu(session))
        with self.app.app_context():
            from anetbbs.models import Post
            post = Post.query.filter_by(subject='Hello Board').first()
            self.assertIsNotNone(post)
            self.assertIn('First line', post.content)
            self.assertIn('Second line', post.content)
            self.assertEqual(post.author_id, self.alice_id)

    def test_no_boards_available_does_not_crash(self):
        # A user whose access_level is below every board's min_access_level
        # (10) sees an empty list via the REAL query -- no mocking needed,
        # and mocking Board.query directly is unsafe here anyway (merely
        # accessing the descriptor to patch it requires a live app context,
        # which patch()'s own save-the-original step doesn't have yet).
        from anetbbs.features.petscii_ui import _boards_menu
        session = _FakeSession({'id': self.bob_id, 'username': 'bob', 'access_level': 1},
                               ['Q'])
        with self._patched_app():
            asyncio.run(_boards_menu(session))
        self.assertIn('No boards available.', session.transcript())


class EchomailScreenTests(PetsciiScreensTestCase):
    def test_reply_to_a_real_user_creates_notification(self):
        from anetbbs.features.petscii_ui import _echomail_menu
        # 1(area) -> N(new) -> To -> Subject -> body -> /send -> ''(press ENTER
        # after "Message posted.") -> Q(back to message list) -> Q(exit area list)
        session = _FakeSession(self._alice_user(),
                               ['1', 'N', 'bob', 'Hi Bob', 'a message body',
                                '/send', '', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_echomail_menu(session))
        with self.app.app_context():
            from anetbbs.models import EchomailMessage, Notification
            msg = EchomailMessage.query.filter_by(subject='Hi Bob').first()
            self.assertIsNotNone(msg)
            self.assertEqual(msg.to_name, 'bob')
            notif = Notification.query.filter_by(
                user_id=self.bob_id, kind='echomail_reply').first()
            self.assertIsNotNone(notif, 'a PETSCII-composed reply to a real user must notify them')

    def test_read_message_then_reply_prefills_to_and_subject(self):
        from anetbbs.features.petscii_ui import _echomail_menu
        with self.app.app_context():
            from anetbbs.models import db, EchomailMessage
            seed = EchomailMessage(area_id=self.area_id, network_id=self.net_id,
                                   from_name='alice', to_name='All',
                                   subject='Original Subject', body='original body',
                                   direction='inbound')
            db.session.add(seed)
            db.session.commit()

        # 1(area) -> 1(read first/only msg) -> R(reply) -> accept default To/
        # Subject (blank -> default) -> body -> /send -> ''(press ENTER after
        # "Message posted.") -> Q(back to message list) -> Q(exit area list)
        session = _FakeSession(self._alice_user(),
                               ['1', '1', 'R', '', '', 'reply body text',
                                '/send', '', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_echomail_menu(session))
        with self.app.app_context():
            from anetbbs.models import EchomailMessage
            reply = EchomailMessage.query.filter_by(
                subject='Re: Original Subject').first()
            self.assertIsNotNone(reply)
            self.assertEqual(reply.to_name, 'alice')  # defaulted to original sender


class PrivateMessageScreenTests(PetsciiScreensTestCase):
    def test_compose_and_send_creates_message_and_notification(self):
        from anetbbs.features.petscii_ui import _pm_menu
        # N(new) -> to -> subject -> body -> /send -> ''(press ENTER after
        # "Sent.") -> Q(exit inbox)
        session = _FakeSession(self._alice_user(),
                               ['N', 'bob', 'Hey Bob', 'a private note', '/send', '', 'Q'])
        with self._patched_app():
            asyncio.run(_pm_menu(session))
        with self.app.app_context():
            from anetbbs.models import PrivateMessage, Notification
            pm = PrivateMessage.query.filter_by(subject='Hey Bob').first()
            self.assertIsNotNone(pm)
            self.assertEqual(pm.sender_id, self.alice_id)
            self.assertEqual(pm.recipient_id, self.bob_id)
            notif = Notification.query.filter_by(user_id=self.bob_id, kind='pm').first()
            self.assertIsNotNone(notif)

    def test_compose_to_nonexistent_user_shows_error_not_crash(self):
        from anetbbs.features.petscii_ui import _pm_compose
        # to -> subject -> body -> /send -> ''(press ENTER after the
        # "No such user" message)
        session = _FakeSession(self._alice_user(),
                               ['ghostuser', 'Subject', 'body', '/send', ''])
        with self._patched_app():
            asyncio.run(_pm_compose(session))
        self.assertIn('No such user', session.transcript())

    def test_reading_marks_message_read(self):
        with self.app.app_context():
            from anetbbs.models import db, PrivateMessage
            pm = PrivateMessage(sender_id=self.bob_id, recipient_id=self.alice_id,
                               subject='Read Me', body='content here')
            db.session.add(pm)
            db.session.commit()
            pm_id = pm.id

        from anetbbs.features.petscii_ui import _pm_read
        session = _FakeSession(self._alice_user(), ['Q'])
        with self._patched_app():
            asyncio.run(_pm_read(session, pm_id))
        with self.app.app_context():
            from anetbbs.models import PrivateMessage
            refreshed = PrivateMessage.query.get(pm_id)
            self.assertIsNotNone(refreshed.read_at)


class FilesWhosOnlineProfileTests(PetsciiScreensTestCase):
    def test_files_browse_lists_uploaded_file_via_db_fallback(self):
        from anetbbs.features.petscii_ui import _files_browse
        session = _FakeSession(self._alice_user(), [''])
        with self._patched_app():
            asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
        self.assertIn('doc.txt', session.transcript())
        self.assertIn('2K', session.transcript())

    def test_whos_online_lists_recently_active_user(self):
        from anetbbs.features.petscii_ui import _whos_online
        session = _FakeSession(self._alice_user(), [''])
        with self._patched_app():
            asyncio.run(_whos_online(session))
        self.assertIn('alice', session.transcript())

    def test_profile_shows_own_fields(self):
        from anetbbs.features.petscii_ui import _profile
        session = _FakeSession(self._alice_user(), [''])
        with self._patched_app():
            asyncio.run(_profile(session))
        transcript = session.transcript()
        self.assertIn('alice', transcript)
        self.assertIn('Nowhere', transcript)
        self.assertIn('A test user.', transcript)


if __name__ == '__main__':
    unittest.main()
