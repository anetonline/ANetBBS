"""Regression tests for real bugs found on the Pi (v1.0b2.186 test pass):

1. Echomail AREA listing (_echomail_menu, distinct from _echo_messages'
   per-area MESSAGE listing already fixed) had NO pagination at all, and
   its network-name column width math (`name_w = w - 16`) assumed a
   bracketed network name would never exceed ~9 chars -- a real network
   name like "ANotherNetwork (QWK)" (20 chars) blew that budget and
   wrapped onto a second physical line on both 40- and 80-column
   screens, confirmed via real SyncTERM screenshots.
2. Board and file-area top-level listings had the same missing-
   pagination gap as echomail's (only the SECOND-level listings --
   threads/messages -- were fixed previously).
3. The profile screen was view-only; added an edit flow for the three
   fields it already displays (display_name, location, bio).
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


def _visible_width(line):
    """Strip this fake session's own [CLR] sentinel and the two
    non-printing PETSCII reverse-video control bytes (\\x12/\\x92, real
    C64 hardware doesn't consume a screen column for these) before
    measuring on-screen width."""
    return len(line.replace('[CLR]', '').replace('\x12', '').replace('\x92', ''))


class PetsciiAreaListingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_area_listing_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, EchoArea
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100)
            db.session.add(alice)
            db.session.commit()
            cls.alice_id = alice.id

            # The exact reported shape: a network name long enough to
            # blow the old flat `w - 16` budget on a 40-col screen.
            net = EchomailNetwork(name='ANotherNetwork (QWK)', network_type='qwk',
                                  is_active=True)
            db.session.add(net)
            db.session.flush()
            for n in range(25):  # matches the real 25-area report
                db.session.add(EchoArea(
                    network_id=net.id, tag=f'AREA{n}', name=f'Area {n}',
                    is_active=True, is_subscribed=True, is_sysop_only=False,
                    min_access_level=10, category='General', order=n))
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

    def _alice(self):
        return {'id': self.alice_id, 'username': 'alice', 'access_level': 100}

    def test_no_row_exceeds_terminal_width_with_a_long_network_name(self):
        from anetbbs.features.petscii_ui import _echomail_menu
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_echomail_menu(session))
        for line in session.transcript().split('\r\n'):
            self.assertLessEqual(_visible_width(line), 40,
                                 f'line exceeds 40 columns and would wrap on '
                                 f'real PETSCII hardware: {line!r}')

    def test_area_list_offers_more_when_it_exceeds_one_page(self):
        # Network picker now comes first (see the network-first redesign
        # test file); the sole seeded network only has 1 row so pick it
        # ('1'), then check the AREA list -- the real 25-area scenario --
        # for pagination.
        from anetbbs.features.petscii_ui import _echomail_menu
        session = _FakeSession(self._alice(), ['1', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_echomail_menu(session))
        self.assertIn('M=more', session.transcript(),
                      '25 areas must not all dump onto one unbroken screen')

    def test_80_column_session_also_never_wraps(self):
        from anetbbs.features.petscii_ui import _echomail_menu
        session = _FakeSession(self._alice(), ['Q'])
        session._forced_width = 80
        with self._patched_app():
            asyncio.run(_echomail_menu(session))
        for line in session.transcript().split('\r\n'):
            self.assertLessEqual(_visible_width(line), 80, f'80-col line overflowed: {line!r}')

    def test_boards_top_level_list_paginates(self):
        from anetbbs.features.petscii_ui import _boards_menu, PAGE_LINES
        from anetbbs.models import db, Board
        with self.app.app_context():
            for n in range(PAGE_LINES + 5):
                db.session.add(Board(name=f'Board {n}', is_active=True,
                                     min_access_level=10, order=n))
            db.session.commit()

        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_boards_menu(session))
        self.assertIn('M=more', session.transcript())

    def test_file_areas_top_level_list_paginates(self):
        from anetbbs.features.petscii_ui import _files_menu, PAGE_LINES
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            for n in range(PAGE_LINES + 5):
                db.session.add(FileArea(name=f'Files {n}', tag=f'FTAG{n}',
                                        is_active=True, is_sysop_only=False,
                                        min_access_level=10))
            db.session.commit()

        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_files_menu(session))
        self.assertIn('M=more', session.transcript())


class PetsciiProfileEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_profile_edit_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100,
                        display_name='Alice A.', location='Nowhere', bio='old bio')
            db.session.add(alice)
            db.session.commit()
            cls.alice_id = alice.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        # Reset alice's editable fields before each test -- these tests
        # share one class-level alice fixture (real app+db setup is
        # expensive), so an earlier test's edit must not leak into a
        # later one (unittest runs test methods in alphabetical order,
        # not definition order).
        with self.app.app_context():
            from anetbbs.models import db, User
            u = User.query.get(self.alice_id)
            u.display_name = 'Alice A.'
            u.location = 'Nowhere'
            u.bio = 'old bio'
            db.session.commit()

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _alice(self):
        return {'id': self.alice_id, 'username': 'alice', 'access_level': 100}

    def test_editing_bio_persists_to_the_database(self):
        from anetbbs.features.petscii_ui import _profile
        # view -> E(edit) -> 3(bio) -> new bio text -> Q(done editing) -> Q(back)
        session = _FakeSession(self._alice(),
                               ['E', '3', 'a brand new bio', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_profile(session))
        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(self.alice_id)
            self.assertEqual(u.bio, 'a brand new bio')

    def test_blank_response_clears_the_field(self):
        from anetbbs.features.petscii_ui import _profile
        session = _FakeSession(self._alice(), ['E', '2', '', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_profile(session))
        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(self.alice_id)
            self.assertIsNone(u.location)

    def test_view_only_flow_without_editing_still_works(self):
        from anetbbs.features.petscii_ui import _profile
        session = _FakeSession(self._alice(), [''])
        with self._patched_app():
            asyncio.run(_profile(session))
        with self.app.app_context():
            from anetbbs.models import User
            u = User.query.get(self.alice_id)
            self.assertEqual(u.bio, 'old bio', 'viewing without editing must not touch the DB')


if __name__ == '__main__':
    unittest.main()
