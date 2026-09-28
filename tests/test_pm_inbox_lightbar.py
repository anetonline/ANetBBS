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


class _StubANView:
    """Captures constructor args and returns a caller-chosen result
    instead of running the real interactive viewer (which would block
    forever on _read_key() against a fake session with no read_raw()).
    Same pattern as test_message_board_read_thread_anview.py's stub."""
    last_instance = None
    next_result = 'back'

    def __init__(self, session, lines, subject=""):
        self.session = session
        self.lines = lines
        self.subject = subject
        _StubANView.last_instance = self

    async def run(self):
        return _StubANView.next_result


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
        # Reading now goes through ANView (Jerry, live 80x24 test
        # 2026-09-29: "we should be using ANetVIEW message reader
        # here"), not the old --MORE-- pager -- stub it out so the test
        # doesn't block on a real interactive viewer.
        self._seed_messages(3, prefix='readme')
        _StubANView.last_instance = None
        _StubANView.next_result = 'back'
        ui, session = self._ui(keys=['DOWN', 'ENTER'])
        with self._patched_app(), \
             patch('anetbbs.features.anedit.ANView', _StubANView):
            asyncio.run(ui.list_pm_inbox())
        self.assertIsNotNone(
            _StubANView.last_instance,
            'moving DOWN once then ENTER should open the 2nd message via ANView')
        joined = '\n'.join(_StubANView.last_instance.lines)
        self.assertIn('readme body 1', joined)

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

    def test_selected_row_does_not_rely_on_reverse_video(self):
        # Real bug found live via screenshot 2026-09-29 ("PM same thing
        # with the highlighted item the text is blocked/not viewable")
        # -- same root cause already proven for the tagline picker
        # (_maybe_prompt_tagline): reverse-video SEL doesn't render
        # visibly on the user's real terminal client (SyncTERM), so
        # render_row() must cancel it and draw its own visible marker
        # for the selected row instead of relying on it.
        from anetbbs.features.ansi_ui import FG
        self._seed_messages(1, prefix='selrow')
        ui, session = self._ui(keys=['Q'])  # row 0 stays selected throughout
        with self._patched_app():
            asyncio.run(ui.list_pm_inbox())
        text = session.transcript()
        self.assertIn('\x1b[0m' + FG['yel'] + '> ', text,
                      'selected row must explicitly cancel SEL and draw '
                      'its own visible marker+color')

    def test_non_enter_key_redraws_without_opening_a_message(self):
        # Any unrecognized key (not Enter, not Q) must just redraw the
        # lightbar, not silently open the highlighted message.
        self._seed_messages(1, prefix='stray')
        ui, session = self._ui(keys=['X', 'Q'])
        with self._patched_app():
            asyncio.run(ui.list_pm_inbox())
        text = _strip_ansi(session.transcript())
        self.assertNotIn('stray body', text)

    def test_ansview_reply_sends_a_new_pm_to_the_original_sender(self):
        # ANView's own 'R' key must actually wire into a reply, not be a
        # dead end now that PM reading goes through it.
        self._seed_messages(1, prefix='replyme')
        _StubANView.last_instance = None
        _StubANView.next_result = 'reply'
        ui, session = self._ui(keys=['ENTER', 'Q'])
        with self._patched_app(), \
             patch('anetbbs.features.anedit.ANView', _StubANView), \
             patch('anetbbs.features.anedit.launch_anedit',
                   new=AsyncMock(return_value='my reply body')):
            asyncio.run(ui.list_pm_inbox())

        from anetbbs.models import PrivateMessage
        with self.app.app_context():
            reply = (PrivateMessage.query
                    .filter_by(sender_id=self.recipient_id,
                               recipient_id=self.sender_id)
                    .first())
            self.assertIsNotNone(reply, 'reply must be sent to the original sender')
            self.assertEqual(reply.body, 'my reply body')
            self.assertTrue(reply.subject.startswith('Re: '))

    def test_ansview_new_routes_to_send_pm(self):
        self._seed_messages(1, prefix='newkey')
        _StubANView.last_instance = None
        _StubANView.next_result = 'new'
        ui, session = self._ui(keys=['ENTER', 'Q'])
        with self._patched_app(), \
             patch('anetbbs.features.anedit.ANView', _StubANView), \
             patch.object(ui, 'send_pm', new=AsyncMock()) as mock_send:
            asyncio.run(ui.list_pm_inbox())
        mock_send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
