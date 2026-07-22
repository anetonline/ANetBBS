"""Regression tests for a real PETSCII usability bug reported live: long
echomail/file listings forced the reader through every single page with
no way to back out early -- the "-- More --" prompt read a line but
never checked what was typed, and several listing screens (echo message
subjects, board threads, PM inbox) had NO pagination at all, printing up
to 50 rows in one unbroken dump on a 25-row real C64 screen.

Fixed two ways:
  - _paginate() now checks for 'Q' at the "-- More --" prompt and stops
    early, returning False so callers can skip a redundant trailing
    prompt.
  - New _paginated_pick() helper adds page-at-a-time listing (with an
    'M=more' option once a page is full) to the echo/board/PM listing
    screens, which previously had none.
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
    """Same fail-loud-on-empty-queue fake used by test_petscii_ui_screens.py
    -- an exhausted response queue raises immediately instead of silently
    hanging a while-True menu loop."""
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


class DirectPaginateTests(unittest.TestCase):
    """Unit-level tests directly against _paginate() -- no DB needed."""

    def test_quitting_at_more_prompt_stops_printing_further_pages(self):
        from anetbbs.features.petscii_ui import _paginate, PAGE_LINES
        lines = [f'line{i}' for i in range(PAGE_LINES * 3)]  # 3 full pages
        session = _FakeSession(None, ['Q'])
        result = asyncio.run(_paginate(session, lines))
        self.assertFalse(result, '_paginate must report early-quit')
        txt = session.transcript()
        self.assertIn(f'line{PAGE_LINES - 1}', txt, 'first page must print in full')
        self.assertNotIn(f'line{PAGE_LINES}', txt,
                         'quitting at the first -- More -- prompt must stop '
                         'before the second page ever prints -- this is the '
                         'exact reported bug (forced through every page)')

    def test_pressing_enter_advances_through_all_pages(self):
        from anetbbs.features.petscii_ui import _paginate, PAGE_LINES
        lines = [f'line{i}' for i in range(PAGE_LINES * 3)]
        session = _FakeSession(None, ['', ''])  # 3 pages -> 2 "-- More --" prompts
        result = asyncio.run(_paginate(session, lines))
        self.assertTrue(result, 'reading to the end must report True, not early-quit')
        self.assertIn(f'line{PAGE_LINES * 3 - 1}', session.transcript())

    def test_short_listing_never_prompts_at_all(self):
        from anetbbs.features.petscii_ui import _paginate
        session = _FakeSession(None, [])  # queue empty -- must never be touched
        result = asyncio.run(_paginate(session, ['only one line']))
        self.assertTrue(result)
        self.assertIn('only one line', session.transcript())


class DirectPaginatedPickTests(unittest.TestCase):
    """Unit-level tests directly against _paginated_pick() -- no DB needed."""

    def test_long_prompt_label_wraps_instead_of_splitting_mid_word(self):
        """Real report: a long prompt_label ('download, E=extended info')
        combined with ', M=more, Q=back: ' exceeded 40 columns and
        hard-broke mid-word ('Q=back' -> 'Q=b' / 'ack')."""
        from anetbbs.features.petscii_ui import _paginated_pick, PAGE_LINES
        rows = list(range(PAGE_LINES + 5))  # forces M=more
        session = _FakeSession(None, ['Q'])
        asyncio.run(_paginated_pick(session, 'Title', rows,
                                    lambda i, r: f'row{r}', 'download, E=extended info'))
        txt = session.transcript()
        self.assertIn('M=more', txt)
        for line in txt.split('\r\n'):
            visible = (line.replace('[CLR]', '')
                      .replace('\x12', '').replace('\x92', ''))
            self.assertLessEqual(len(visible), 40, f'prompt line overflowed: {line!r}')
        self.assertNotIn('Q=b\r', txt)

    def test_short_list_prompt_has_no_more_option(self):
        from anetbbs.features.petscii_ui import _paginated_pick, PAGE_LINES
        rows = list(range(5))
        session = _FakeSession(None, ['Q'])
        asyncio.run(_paginated_pick(session, 'Title', rows,
                                    lambda i, r: f'row{r}', 'read'))
        self.assertNotIn('M=more', session.transcript())

    def test_long_list_shows_more_option_and_pages_forward(self):
        from anetbbs.features.petscii_ui import _paginated_pick, PAGE_LINES
        rows = list(range(PAGE_LINES + 5))  # spans 2 pages
        session = _FakeSession(None, ['M', 'Q'])
        result = asyncio.run(_paginated_pick(session, 'Title', rows,
                                             lambda i, r: f'row{i}:{r}', 'read'))
        self.assertEqual(result, 'Q')
        txt = session.transcript()
        self.assertIn('M=more', txt)
        # Page 1 shows rows 1..PAGE_LINES (1-based), page 2 shows the rest.
        self.assertIn(f'row1:0', txt)
        self.assertIn(f'row{PAGE_LINES + 1}:{PAGE_LINES}', txt,
                      'typing M must advance to the next page')

    def test_can_quit_immediately_without_seeing_every_row(self):
        from anetbbs.features.petscii_ui import _paginated_pick, PAGE_LINES
        rows = list(range(PAGE_LINES * 3))
        session = _FakeSession(None, ['Q'])
        result = asyncio.run(_paginated_pick(session, 'Title', rows,
                                             lambda i, r: f'row{i}', 'read'))
        self.assertEqual(result, 'Q')
        txt = session.transcript()
        self.assertNotIn(f'row{PAGE_LINES + 1}', txt,
                         'Q on the FIRST page must exit without ever showing '
                         'later pages -- this is the exact reported bug for '
                         'file-area browsing (no way to back out early)')

    def test_number_on_second_page_still_resolves_correctly(self):
        from anetbbs.features.petscii_ui import _paginated_pick, PAGE_LINES
        rows = [f'item{n}' for n in range(PAGE_LINES + 3)]
        target_1based = PAGE_LINES + 2  # on page 2
        session = _FakeSession(None, ['M', str(target_1based)])
        result = asyncio.run(_paginated_pick(session, 'Title', rows,
                                             lambda i, r: f'{i}. {r}', 'read'))
        self.assertEqual(result, target_1based)


class EchoListingPaginationTests(unittest.TestCase):
    """End-to-end: a real echo area with more messages than fit on one
    PETSCII page must let the reader quit before seeing all of them."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_pagination_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, EchoArea, EchomailMessage
        from anetbbs.features.petscii_ui import PAGE_LINES
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100)
            db.session.add(alice)
            db.session.commit()
            cls.alice_id = alice.id

            net = EchomailNetwork(name='ANotherNetwork', network_type='qwk', is_active=True)
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='ANET_GEN', name='General',
                            is_active=True, is_subscribed=True,
                            is_sysop_only=False, min_access_level=10)
            db.session.add(area)
            db.session.commit()
            cls.area_id = area.id

            # More messages than one PETSCII page holds.
            for n in range(PAGE_LINES + 5):
                db.session.add(EchomailMessage(
                    area_id=area.id, network_id=net.id, from_name='alice',
                    to_name='All', subject=f'Msg {n}', body='x', direction='inbound'))
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

    def test_quitting_the_message_list_immediately_does_not_force_all_pages(self):
        from anetbbs.features.petscii_ui import _echo_messages, PAGE_LINES
        session = _FakeSession(
            {'id': self.alice_id, 'username': 'alice', 'access_level': 100}, ['Q'])
        with self._patched_app():
            asyncio.run(_echo_messages(session, self.area_id, 'General'))
        txt = session.transcript()
        # Newest-first ordering -- the highest-numbered message is on page 1.
        self.assertIn(f'Msg {PAGE_LINES + 4}', txt)
        self.assertNotIn('Msg 0', txt,
                         'Q on the first page must exit before the last '
                         '(oldest, page-2) message ever prints')
        self.assertIn('M=more', txt, 'a listing longer than one page must offer M=more')


if __name__ == '__main__':
    unittest.main()
