"""Regression tests for the terminal "while already online" notification
check (anetbbs.features.notify.check_new_notifications), the second half
of the echomail/QWK reply-notification feature -- session.py's login-time
_show_notification_summary() already covers "what was already unread when
you logged on"; this covers "something arrived while you were already
connected," checked once per menu redraw (menu_engine.py's run_menu() and
bbs_ui.py's BBSMenuUI.show_main() fallback both call it), mirroring the
existing sysop-reply pop_messages() check's granularity.
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

    async def write(self, text):
        self.written.append(text)


class CheckNewNotificationsTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'check_notif.db'))
        with self.app.app_context():
            from anetbbs.models import db, User
            u = User(username='xena', email='xena@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            self.user_id = u.id

    def test_first_call_establishes_baseline_silently(self):
        from anetbbs.features.notify import notify, check_new_notifications
        with self.app.app_context():
            notify(self.user_id, 'echomail_reply', title='Yara wrote to you',
                  body='in FidoNet (General)', target_url='/echomail/1/1')
            session = _FakeSession(self.user_id)
            asyncio.run(check_new_notifications(session))
            # Pre-existing (already unread at "login") must NOT be
            # re-announced by the menu-loop check -- that's
            # _show_notification_summary()'s job.
            self.assertEqual(session.written, [])
            self.assertIsNotNone(getattr(session, '_last_notif_id', None))

    def test_notification_arriving_after_baseline_is_announced(self):
        from anetbbs.features.notify import notify, check_new_notifications
        with self.app.app_context():
            session = _FakeSession(self.user_id)
            asyncio.run(check_new_notifications(session))  # baseline, nothing yet
            self.assertEqual(session.written, [])

            notify(self.user_id, 'echomail_reply', title='Zack wrote to you',
                  body='in ANet_Net (Tech Talk)', target_url='/echomail/2/2')
            asyncio.run(check_new_notifications(session))
            joined = ''.join(session.written)
            self.assertIn('Zack wrote to you', joined)
            self.assertIn('ANet_Net (Tech Talk)', joined)

    def test_no_repeat_announcement_on_subsequent_checks(self):
        from anetbbs.features.notify import notify, check_new_notifications
        with self.app.app_context():
            session = _FakeSession(self.user_id)
            asyncio.run(check_new_notifications(session))  # baseline
            notify(self.user_id, 'echomail_reply', title='Abe wrote to you',
                  body='in FidoNet (Chat)', target_url='/echomail/3/3')
            asyncio.run(check_new_notifications(session))  # announces it
            session.written.clear()
            asyncio.run(check_new_notifications(session))  # nothing new
            self.assertEqual(session.written, [])


if __name__ == '__main__':
    unittest.main()
