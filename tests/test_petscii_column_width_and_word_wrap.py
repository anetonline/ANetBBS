"""Regression tests for two more real bugs found testing v1.0b2.187 on
the Pi:

1. _echomail_menu's column-width fix from the PREVIOUS round sized both
   columns to add up to EXACTLY the terminal width -- filling the last
   column, which triggers most terminals' (real C64 hardware included)
   own auto-wrap behavior. The row's trailing \\r\\n then produced a
   genuine blank line after every row, and on a real 25-row screen that
   doubled the vertical space each row consumed, scrolling the top of an
   18-item page off-screen before the prompt even appeared ("you can't
   see 1-7"). Fixed by always leaving one spare column.
2. Subject/label columns across the listing screens truncated mid-word
   ("voluntarily" -> "volun") instead of at a word boundary, and reserved
   a fixed budget for the trailing name/count column regardless of how
   short the actual values were, leaving the subject narrower than it
   needed to be.
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
    # Strip PETSCII color bytes too, not just [CLR]/REVERSE_ON/REVERSE_OFF --
    # petscii_ui.py's _header()/menu_line() (see petscii_theme.py) now embed
    # real color control bytes, which (like reverse-video) are invisible on
    # a real C64 screen and must not count toward the row's visible width.
    from anetbbs.features.petscii_theme import COLOR_NAMES
    out = line.replace('[CLR]', '').replace('\x12', '').replace('\x92', '')
    for color_byte in COLOR_NAMES.values():
        out = out.replace(color_byte, '')
    return len(out)


class TruncateWordsUnitTests(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        from anetbbs.features.petscii_ui import _truncate_words
        self.assertEqual(_truncate_words('hello', 20), 'hello')

    def test_long_text_breaks_at_word_boundary_not_mid_word(self):
        from anetbbs.features.petscii_ui import _truncate_words
        original = 'Nintendo says users voluntarily give up'
        result = _truncate_words(original, 26)
        self.assertLessEqual(len(result), 26)
        # Every word in the result must be a COMPLETE word from the
        # original, never a fragment -- the old [:26] slice produced
        # "Nintendo says users volun" (a fragment of "voluntarily").
        original_words = set(original.split())
        for word in result.split():
            self.assertIn(word, original_words,
                          f'{word!r} is a fragment, not a complete word from the original')

    def test_single_word_longer_than_budget_falls_back_to_hard_cut(self):
        from anetbbs.features.petscii_ui import _truncate_words
        result = _truncate_words('Supercalifragilisticexpialidocious', 10)
        self.assertEqual(len(result), 10)


class NoExactWidthAutoWrapTests(unittest.TestCase):
    """The core regression: no listing row may print exactly `w`
    characters (would trigger a real terminal's auto-wrap and eat a
    line), across a range of realistic widths and network-name lengths."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_colwidth_test.db')
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
                        password_hash='x', access_level=100)
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

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _alice(self):
        return {'id': self.alice_id, 'username': 'alice', 'access_level': 100}

    def _seed_areas(self, netname, count=10):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        with self.app.app_context():
            net = EchomailNetwork(name=netname, network_type='qwk', is_active=True)
            db.session.add(net)
            db.session.flush()
            for n in range(count):
                db.session.add(EchoArea(
                    network_id=net.id, tag=f'AREA{n}', name=f'Area {n}',
                    is_active=True, is_subscribed=True, is_sysop_only=False,
                    min_access_level=10, category='General', order=n))
            db.session.commit()

    def test_no_row_ever_prints_exactly_the_full_terminal_width(self):
        from anetbbs.features.petscii_ui import _echomail_menu
        from anetbbs.models import db, EchoArea, EchomailNetwork
        for width in (40, 80):
            for netname in ('Net', 'ANotherNetwork (QWK)', 'A Very Long Network Name Indeed'):
                with self.app.app_context():
                    EchoArea.query.delete()
                    EchomailNetwork.query.delete()
                    db.session.commit()
                self._seed_areas(netname, count=5)
                session = _FakeSession(self._alice(), ['Q'])
                session._forced_width = width
                with self._patched_app():
                    asyncio.run(_echomail_menu(session))
                for line in session.transcript().split('\r\n'):
                    vw = _visible_width(line)
                    self.assertLess(vw, width,
                                    f'w={width} net={netname!r}: line fills the '
                                    f'full terminal width ({vw}/{width}) -- would '
                                    f'trigger the terminal\'s own auto-wrap: {line!r}')


class WiderSubjectColumnTests(unittest.TestCase):
    """The subject column must use space freed up by a short from-name/
    reply-count column rather than reserving a fixed budget regardless."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_widersubj_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, EchoArea, EchomailMessage
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100)
            db.session.add(alice)
            db.session.commit()
            cls.alice_id = alice.id

            net = EchomailNetwork(name='Net', network_type='qwk', is_active=True)
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='TECH', name='Technology',
                            is_active=True, is_subscribed=True, is_sysop_only=False,
                            min_access_level=10)
            db.session.add(area)
            db.session.commit()
            cls.area_id = area.id

            # Short, consistent from-name (like the real "Tech News Bot"
            # report) -- old fixed-22 budget wasted space vs. this.
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='Tech News Bot',
                to_name='All',
                subject='Nintendo says users voluntarily give up their privacy',
                body='x', direction='inbound'))
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

    def test_subject_shows_more_than_the_old_fixed_budget_and_no_midword_cut(self):
        from anetbbs.features.petscii_ui import _echo_messages
        session = _FakeSession(
            {'id': self.alice_id, 'username': 'alice', 'access_level': 100}, ['Q'])
        session._forced_width = 40
        with self._patched_app():
            asyncio.run(_echo_messages(session, self.area_id, 'Technology'))
        txt = session.transcript()
        # Old fixed w-22 budget at 40 cols = 18 chars -> "Nintendo says user"
        # (cutting "users" mid-word). New dynamic budget must do better.
        self.assertNotIn('says user ', txt, 'must not cut "users" mid-word')
        self.assertIn('Tech News Bot', txt)


if __name__ == '__main__':
    unittest.main()
