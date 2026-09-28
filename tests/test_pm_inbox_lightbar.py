"""Regression tests for the PM inbox's conversion from type-a-number
selection to a real arrow-key lightbar (BBSMenuUI.list_pm_inbox(),
anetbbs/features/bbs_ui.py), requested live 2026-09-29 as part of
"make ANetBBS more message/forum/reading friendly... do away with all
the initial/original generic n-next/b-back type areas (ALL) and have
the lightbar scrollable menu" -- same _rss_lightbar widget already used
by the file-area browser and board thread list conversions.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, user_id, keys=None, lines=None):
        # create_app() auto-seeds a default admin account (see
        # web_app.py's _create_default_data()), so a hardcoded id=1
        # here would not actually match whatever id the test's own
        # seeded recipient user really got -- always pass the real one.
        self.user = {'id': user_id, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.window_size = (80, 24)
        self.written = []
        self._keys = list(keys or [])
        self._lines = list(lines or [])

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._keys.pop(0) if self._keys else 'Q'

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return self._lines.pop(0) if self._lines else ''

    def transcript(self):
        return ''.join(self.written)


def _strip_ansi(text):
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


class PmInboxLightbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.pm_inbox_lightbar_test.db')
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
            recipient = User(username='pminboxtest', email='pit@example.com',
                             password_hash='x')
            sender = User(username='pmsender', email='pms@example.com',
                         password_hash='x')
            db.session.add_all([recipient, sender])
            db.session.commit()
            cls.recipient_id = recipient.id
            cls.sender_id = sender.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.models import db, PrivateMessage
        with self.app.app_context():
            PrivateMessage.query.delete()
            db.session.commit()

    def _seed_messages(self, n, prefix='msg'):
        from anetbbs.models import db, PrivateMessage
        with self.app.app_context():
            for i in range(n):
                db.session.add(PrivateMessage(
                    sender_id=self.sender_id, recipient_id=self.recipient_id,
                    subject=f'{prefix} subject {i}', body=f'{prefix} body {i}'))
            db.session.commit()

    def _ui(self, keys=None, lines=None):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(self.recipient_id, keys=keys, lines=lines)
        return BBSMenuUI(session), session

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def test_empty_inbox_shows_message_and_returns(self):
        ui, session = self._ui(lines=[''])
        with self._patched_app(), \
             patch.object(ui, '_rss_lightbar', new=AsyncMock(
                 side_effect=AssertionError(
                     '_rss_lightbar must not be called for an empty inbox'))):
            asyncio.run(ui.list_pm_inbox())
        self.assertIn('inbox is empty', _strip_ansi(session.transcript()))

    def test_enter_reads_the_highlighted_message_and_marks_it_read(self):
        self._seed_messages(3, prefix='readme')
        ui, session = self._ui(keys=['DOWN', 'ENTER'], lines=[''])
        with self._patched_app():
            asyncio.run(ui.list_pm_inbox())
        text = _strip_ansi(session.transcript())
        self.assertIn('readme body 1', text,
                     'moving DOWN once then ENTER should open the 2nd message')

        from anetbbs.models import PrivateMessage
        with self.app.app_context():
            msgs = (PrivateMessage.query
                   .filter_by(recipient_id=self.recipient_id)
                   .order_by(PrivateMessage.created_at.desc()).all())
            self.assertIsNotNone(msgs[1].read_at,
                                 'opening a message must mark it read')
            self.assertIsNone(msgs[0].read_at,
                              'messages not opened must stay unread')

    def test_unread_marker_shown_then_cleared_after_reading(self):
        self._seed_messages(1, prefix='markertest')
        ui, session = self._ui(keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.list_pm_inbox())
        self.assertIn('*', session.transcript(),
                      'an unread message must show the unread marker')

    def test_q_backs_out_without_opening_anything(self):
        self._seed_messages(2, prefix='noopen')
        ui, session = self._ui(keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.list_pm_inbox())
        text = _strip_ansi(session.transcript())
        self.assertNotIn('noopen body', text)

        from anetbbs.models import PrivateMessage
        with self.app.app_context():
            unread = (PrivateMessage.query
                     .filter_by(recipient_id=self.recipient_id, read_at=None)
                     .count())
            self.assertEqual(unread, 2, 'backing out must not mark anything read')

    def test_non_enter_key_redraws_without_opening_a_message(self):
        # Any unrecognized key (not Enter, not Q) must just redraw the
        # lightbar, not silently open the highlighted message.
        self._seed_messages(1, prefix='stray')
        ui, session = self._ui(keys=['X', 'Q'])
        with self._patched_app():
            asyncio.run(ui.list_pm_inbox())
        text = _strip_ansi(session.transcript())
        self.assertNotIn('stray body', text)


if __name__ == '__main__':
    unittest.main()
