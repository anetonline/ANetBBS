"""Regression tests for the terminal login-time "scan for new messages"
preference (anetbbs.core.session.BBSSession._maybe_scan_new_messages,
User.msg_scan_pref in models.py).

Real gap Jerry flagged: classic BBS software asks (or auto-scans, or
skips entirely, per a per-user preference) for new mail/messages at
login -- ANetBBS's terminal client always showed the notification
pop-up (_show_notification_summary(), see
tests/test_notification_login_popup.py) unconditionally, with no
preference gating it at all. This adds the classic 'auto'/'ask'/'off'
tri-state and, when something's actually found, a one-key shortcut
straight into the relevant reader (PM inbox / boards / echomail)
instead of just dropping the user back at a list they can't act on.

Uses the same real-seeded-DB + scripted-response fake-session technique
as test_door_games_menu_layout.py and test_notification_login_popup.py.
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
    def __init__(self, user_id, responses, msg_scan_pref='ask', term_mode='ansi'):
        self.user = {'id': user_id, 'msg_scan_pref': msg_scan_pref}
        self.written = []
        self.read_line_calls = 0
        self._responses = list(responses)
        self.window_size = (80, 24)
        self.term_mode = term_mode

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        self.read_line_calls += 1
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    async def _show_notification_summary(self):
        # _maybe_scan_new_messages() (a real BBSSession method, called
        # unbound against this fake below) calls self._show_notification_summary()
        # -- delegate to the real implementation rather than reimplementing
        # its DB queries here, same technique test_notification_login_popup.py
        # already exercises directly.
        from anetbbs.core.session import BBSSession
        return await BBSSession._show_notification_summary(self)

    def transcript(self):
        return ''.join(self.written)


class MsgScanPreferenceTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'msg_scan.db'))
        with self.app.app_context():
            from anetbbs.models import db, User
            u = User(username='rowan', email='rowan@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            self.user_id = u.id

    def _add_unread_pm(self):
        from anetbbs.models import db, PrivateMessage, User
        with self.app.app_context():
            sender = User(username='sable', email='sable@example.com', password_hash='x')
            db.session.add(sender)
            db.session.commit()
            db.session.add(PrivateMessage(sender_id=sender.id, recipient_id=self.user_id,
                                          subject='hi', body='hello'))
            db.session.commit()

    def _run(self, session):
        from anetbbs.core.session import BBSSession
        asyncio.run(BBSSession._maybe_scan_new_messages(session))

    def test_off_pref_skips_scan_entirely(self):
        self._add_unread_pm()
        session = _FakeSession(self.user_id, [], msg_scan_pref='off')
        with self.app.app_context():
            self._run(session)
        self.assertEqual(session.written, [])
        self.assertEqual(session.read_line_calls, 0)

    def test_ask_pref_prompts_and_skips_summary_on_no(self):
        self._add_unread_pm()
        session = _FakeSession(self.user_id, ['n'], msg_scan_pref='ask')
        with self.app.app_context():
            self._run(session)
        joined = session.transcript()
        self.assertIn('Scan for new messages?', joined)
        self.assertNotIn('YOU HAVE NEW NOTIFICATIONS', joined,
                         'declining the scan must not show the summary')
        self.assertEqual(session.read_line_calls, 1,
                         'must stop after the single ask prompt')

    def test_ask_pref_default_enter_means_yes(self):
        self._add_unread_pm()
        # ask -> '' (blank Enter = default Yes) -> summary shown -> ''
        # dismisses the summary's own Enter-to-continue -> '' declines
        # the jump-in offer.
        session = _FakeSession(self.user_id, ['', '', ''], msg_scan_pref='ask')
        with self.app.app_context():
            self._run(session)
        joined = session.transcript()
        self.assertIn('YOU HAVE NEW NOTIFICATIONS', joined)
        self.assertIn('Read now?', joined)

    def test_unset_pref_defaults_to_ask(self):
        self._add_unread_pm()
        session = _FakeSession(self.user_id, ['n'], msg_scan_pref=None)
        with self.app.app_context():
            self._run(session)
        self.assertIn('Scan for new messages?', session.transcript())

    def test_auto_pref_shows_summary_without_asking(self):
        self._add_unread_pm()
        session = _FakeSession(self.user_id, ['', ''], msg_scan_pref='auto')
        with self.app.app_context():
            self._run(session)
        joined = session.transcript()
        self.assertNotIn('Scan for new messages?', joined,
                         'auto must not prompt for permission')
        self.assertIn('YOU HAVE NEW NOTIFICATIONS', joined)

    def test_nothing_pending_shows_no_jump_in_offer(self):
        session = _FakeSession(self.user_id, [''], msg_scan_pref='auto')
        with self.app.app_context():
            self._run(session)
        joined = session.transcript()
        self.assertNotIn('YOU HAVE NEW NOTIFICATIONS', joined)
        self.assertNotIn('Read now?', joined)
        self.assertEqual(session.read_line_calls, 0)

    def test_jump_in_offer_only_lists_whats_actually_pending(self):
        # Only a PM is pending -- the jump-in line must offer [P] and
        # must NOT offer [B]oards or [E]chomail, since nothing of those
        # kinds is actually waiting.
        self._add_unread_pm()
        session = _FakeSession(self.user_id, ['', ''], msg_scan_pref='auto')
        with self.app.app_context():
            self._run(session)
        import re
        joined = re.sub(r'\x1b\[[0-9;]*m', '', session.transcript())
        self.assertIn('[P]rivate Messages', joined)
        self.assertNotIn('[B]oards', joined)
        self.assertNotIn('[E]chomail', joined)

    def test_selecting_p_jumps_into_pm_inbox(self):
        self._add_unread_pm()
        # dismiss summary -> 'P' picks the jump-in -> 'Q' exits the PM
        # inbox reader's own pick-a-message loop.
        session = _FakeSession(self.user_id, ['', 'P', 'Q'], msg_scan_pref='auto')
        with self.app.app_context():
            self._run(session)
        joined = session.transcript()
        self.assertIn('PM INBOX', joined,
                      'answering P must actually launch the PM reader')

    def test_petscii_session_gets_no_jump_in_offer(self):
        self._add_unread_pm()
        session = _FakeSession(self.user_id, [''], msg_scan_pref='auto',
                               term_mode='petscii')
        with self.app.app_context():
            self._run(session)
        joined = session.transcript()
        self.assertIn('YOU HAVE NEW NOTIFICATIONS', joined)
        self.assertNotIn('Read now?', joined,
                         'PETSCII sessions must not get the ANSI-native jump-in offer')


if __name__ == '__main__':
    unittest.main()
