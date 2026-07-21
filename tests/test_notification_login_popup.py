"""Regression tests for the terminal login notification pop-up
(anetbbs.core.session.BBSSession._show_notification_summary).

Real request: the previous version of this method just printed a passive
one-line count ("*** You have new: 2 notifications") that scrolled past
like any other banner -- easy to miss, and it didn't say who or where.
It's now a blocking pop-up: lists every unread Notification by name/detail
plus PM/IM counts, and requires the user to press Enter (read_line) before
continuing. Called unbound against a lightweight fake session object
(same technique already used for door-game tests in this repo), since
constructing a real BBSSession needs a live reader/writer pair this method
never actually touches.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class _FakeSession:
    def __init__(self, user_id):
        self.user = {'id': user_id}
        self.written = []
        self.read_line_calls = 0

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        self.read_line_calls += 1
        if prompt:
            await self.write(prompt)
        return ''


class NotificationLoginPopupTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'login_popup.db'))
        with self.app.app_context():
            from anetbbs.models import db, User
            u = User(username='opal', email='opal@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            self.user_id = u.id

    def _run(self, session):
        from anetbbs.core.session import BBSSession
        asyncio.run(BBSSession._show_notification_summary(session))

    def test_silent_when_nothing_unread(self):
        session = _FakeSession(self.user_id)
        with self.app.app_context():
            self._run(session)
        self.assertEqual(session.written, [])
        self.assertEqual(session.read_line_calls, 0)

    def test_lists_notification_by_name_and_requires_enter(self):
        from anetbbs.features.notify import notify
        session = _FakeSession(self.user_id)
        with self.app.app_context():
            notify(self.user_id, 'echomail_reply', title='Nadia wrote to you',
                  body='in FidoNet (General)', target_url='/echomail/1/1')
            self._run(session)
        joined = ''.join(session.written)
        self.assertIn('Nadia wrote to you', joined)
        self.assertIn('in FidoNet (General)', joined)
        self.assertIn('YOU HAVE NEW NOTIFICATIONS', joined)
        self.assertIn('Press ENTER to continue', joined)
        self.assertEqual(session.read_line_calls, 1,
                         'must block on read_line (Enter) once, not proceed silently')

    def test_pm_and_im_counts_still_shown(self):
        from anetbbs.models import db, PrivateMessage, InstantMessage, User
        session = _FakeSession(self.user_id)
        with self.app.app_context():
            sender = User(username='quill', email='quill@example.com', password_hash='x')
            db.session.add(sender)
            db.session.commit()
            db.session.add(PrivateMessage(sender_id=sender.id, recipient_id=self.user_id,
                                          subject='hi', body='hello'))
            db.session.add(InstantMessage(recipient_id=self.user_id,
                                          sender_label='quill@otherbbs', body='ping'))
            db.session.commit()
            self._run(session)
        joined = ''.join(session.written)
        self.assertIn('1', joined)
        self.assertIn('Private Message', joined)
        self.assertIn('InterBBS IM', joined)
        self.assertEqual(session.read_line_calls, 1)


if __name__ == '__main__':
    unittest.main()
