"""Regression test: terminal bulletins used to be paged with the old
[MORE] page-break pager (_page_text/_page_lines) instead of the
scrollable ANView reader already used for echo/private messages --
reported live ("still has the old style page break when reading
bulletins that are longer than a page").

Bulletins are authored via the web admin's plain-textarea form
(BulletinForm in anetbbs/web/admin.py), so their content is ordinary
Unicode text -- NOT the CP437-byte-as-latin1 mojibake convention
launch_aneview() assumes for terminal/FidoNet-composed messages.
Naively reusing launch_aneview() for bulletins would round-trip any
non-ASCII character (curly quotes, em dashes, accented letters)
through a CP437 decode and silently corrupt it into an unrelated
glyph -- this test guards against that regression specifically, plus
confirms raw ANSI escapes in a bulletin still pass through unchanged.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FakeSession:
    def __init__(self, inputs):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.window_size = (80, 24)
        self.written = []
        self._inputs = list(inputs)

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        return self._inputs.pop(0) if self._inputs else 'Q'


class _StubANView:
    """Captures constructor args instead of running the real interactive
    viewer (which would block forever on _read_key())."""
    last_instance = None

    def __init__(self, session, lines, subject=""):
        self.session = session
        self.lines = lines
        self.subject = subject
        _StubANView.last_instance = self

    async def run(self):
        return 'back'


class BulletinAnViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.bulletin_anview_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Message as Bulletin
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            user = User.query.filter_by(username='sysop_test').first()
            if not user:
                user = User(username='sysop_test', email='sysop_test@example.com',
                            password_hash='x', access_level=100, is_admin=True)
                db.session.add(user)
                db.session.commit()
            # Unicode content a web-authored bulletin realistically has:
            # a curly quote, an em dash, and an accented letter -- plus
            # a raw ANSI-colored line, matching how a sysop might paste
            # a colored announcement.
            cls.long_body = (
                'Welcome—read this: café owner’s notice.\n'
                '\x1b[36mColored line stays as-is\x1b[0m\n'
                + '\n'.join(f'Body line {i}' for i in range(1, 40))
            )
            db.session.add(Bulletin(
                author_id=user.id, title='Long Announcement',
                content=cls.long_body, is_pinned=True))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_bulletin_reader_uses_anview_not_the_more_pager(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        _StubANView.last_instance = None
        session = FakeSession(inputs=['1', 'Q'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.ANView', _StubANView):
            asyncio.run(ui.list_bulletins())

        self.assertIsNotNone(_StubANView.last_instance,
                             'ANView must be constructed to view a bulletin')
        self.assertEqual(_StubANView.last_instance.subject, 'Long Announcement')

        all_written = ''.join(session.written)
        self.assertNotIn('--- end ---', all_written,
                         'the old [MORE] pager end-marker must not appear -- '
                         'bulletins should route through ANView now')
        self.assertNotIn('[MORE]', all_written)

    def test_unicode_content_is_not_corrupted_by_a_cp437_roundtrip(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        _StubANView.last_instance = None
        session = FakeSession(inputs=['1', 'Q'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.ANView', _StubANView):
            asyncio.run(ui.list_bulletins())

        joined = '\n'.join(_StubANView.last_instance.lines)
        self.assertIn('café', joined,
                     'accented letter must survive unchanged -- a CP437 '
                     'mojibake round-trip would turn it into a different glyph')
        self.assertIn('—', joined, 'em dash must survive unchanged')
        self.assertIn('’', joined, 'curly quote must survive unchanged')

    def test_raw_ansi_line_passes_through_unchanged(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        _StubANView.last_instance = None
        session = FakeSession(inputs=['1', 'Q'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.ANView', _StubANView):
            asyncio.run(ui.list_bulletins())

        self.assertIn('\x1b[36mColored line stays as-is\x1b[0m',
                      _StubANView.last_instance.lines)


if __name__ == '__main__':
    unittest.main()
