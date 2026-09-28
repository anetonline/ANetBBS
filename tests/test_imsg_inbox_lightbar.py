"""Regression tests for the InterBBS Instant Message inbox's conversion
from type-a-number selection (plus R#/D# prefix commands) to a real
arrow-key lightbar (BBSMenuUI.list_imsg_inbox(),
anetbbs/features/bbs_ui.py), requested live 2026-09-29 as part of
"make ANetBBS more message/forum/reading friendly... ALL the initial/
original generic n-next/b-back type areas" -- same _rss_lightbar
widget already used by the PM inbox/thread-list/file-browser
conversions. Reply and delete still exist, just as R/D hotkeys against
the highlighted row instead of an R#/D# typed prefix.
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


class ImsgInboxLightbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.imsg_inbox_lightbar_test.db')
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
            u = User(username='imsginboxtest', email='iit@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.models import db, InstantMessage
        with self.app.app_context():
            InstantMessage.query.delete()
            db.session.commit()

    def _seed(self, n, prefix='im'):
        from anetbbs.models import db, InstantMessage
        with self.app.app_context():
            for i in range(n):
                db.session.add(InstantMessage(
                    recipient_id=self.user_id,
                    sender_label=f'{prefix}sender{i}@peer{prefix}{i}.example.com',
                    sender_host=f'peer{prefix}{i}.example.com',
                    body=f'{prefix} body {i}',
                    is_read=False, origin='msp'))
            db.session.commit()

    def _ui(self, keys=None, lines=None):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(self.user_id, keys=keys, lines=lines)
        return BBSMenuUI(session), session

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def test_empty_inbox_shows_message_and_returns(self):
        ui, session = self._ui(lines=[''])
        with self._patched_app(), \
             patch.object(ui, '_rss_lightbar', new=AsyncMock(
                 side_effect=AssertionError(
                     '_rss_lightbar must not be called for an empty inbox'))):
            asyncio.run(ui.list_imsg_inbox())
        self.assertIn('No InterBBS instant messages',
                      _strip_ansi(session.transcript()))

    def test_enter_reads_the_highlighted_message_and_marks_it_read(self):
        self._seed(3, prefix='readme')
        ui, session = self._ui(keys=['DOWN', 'ENTER', 'Q'], lines=[''])
        with self._patched_app():
            asyncio.run(ui.list_imsg_inbox())
        text = _strip_ansi(session.transcript())
        self.assertIn('readme body 1', text,
                     'moving DOWN once then ENTER should open the 2nd message')

        from anetbbs.models import InstantMessage
        with self.app.app_context():
            msgs = (InstantMessage.query
                   .filter_by(recipient_id=self.user_id)
                   .order_by(InstantMessage.received_at.desc()).all())
            self.assertTrue(msgs[1].is_read, 'opening a message must mark it read')
            self.assertFalse(msgs[0].is_read, 'messages not opened must stay unread')

    def test_d_deletes_the_highlighted_message(self):
        self._seed(2, prefix='delme')
        ui, session = self._ui(keys=['D', 'Q'])
        with self._patched_app():
            asyncio.run(ui.list_imsg_inbox())
        self.assertIn('Deleted', _strip_ansi(session.transcript()))

        from anetbbs.models import InstantMessage
        with self.app.app_context():
            remaining = InstantMessage.query.filter_by(recipient_id=self.user_id).count()
            self.assertEqual(remaining, 1)

    def test_deleting_the_last_message_returns_cleanly(self):
        self._seed(1, prefix='onlyone')
        ui, session = self._ui(keys=['D'])
        with self._patched_app():
            asyncio.run(ui.list_imsg_inbox())
        from anetbbs.models import InstantMessage
        with self.app.app_context():
            remaining = InstantMessage.query.filter_by(recipient_id=self.user_id).count()
            self.assertEqual(remaining, 0)

    def test_r_replies_to_the_highlighted_message(self):
        self._seed(1, prefix='replyme')
        ui, session = self._ui(keys=['R', 'Q'], lines=['hi there'])
        with self._patched_app(), \
             patch('anetbbs.msp.client.send_msp', return_value=True) as mock_send:
            asyncio.run(ui.list_imsg_inbox())
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs['message'], 'hi there')
        self.assertIn('Sent', _strip_ansi(session.transcript()))

    def test_q_backs_out_without_opening_anything(self):
        # The list row itself legitimately shows a truncated body
        # preview (unchanged from the original design) -- what this
        # guards is that Q does NOT open the full detail view (which
        # renders a "Host:" header line the list rows never show) or
        # mark anything read.
        self._seed(2, prefix='noopen')
        ui, session = self._ui(keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.list_imsg_inbox())
        text = _strip_ansi(session.transcript())
        self.assertNotIn('Host:', text)

        from anetbbs.models import InstantMessage
        with self.app.app_context():
            unread = InstantMessage.query.filter_by(
                recipient_id=self.user_id, is_read=False).count()
            self.assertEqual(unread, 2, 'backing out must not mark anything read')


if __name__ == '__main__':
    unittest.main()
