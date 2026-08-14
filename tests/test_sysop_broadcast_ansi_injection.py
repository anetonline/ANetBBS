"""Regression test for a real vulnerability found in a security audit:
anetbbs.core.session.BBSSession._show_pending_broadcasts() wrote
SysopBroadcast.text (and the sender's username) raw to every user's
terminal on login, with no escape-sequence sanitization -- same
injection class as the login notification popup (see
test_notification_login_popup.py's ansi-injection test), fixed the
same way via anetbbs.core.text_safety.strip_untrusted_escapes().
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
    def __init__(self):
        self.written = []

    async def write(self, text):
        self.written.append(text)


class SysopBroadcastAnsiInjectionTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'broadcast.db'))

    def _run(self, session):
        from anetbbs.core.session import BBSSession
        asyncio.run(BBSSession._show_pending_broadcasts(session))

    def test_ansi_escape_injection_in_broadcast_text_is_stripped(self):
        from anetbbs.models import db, SysopBroadcast, User
        session = _FakeSession()
        with self.app.app_context():
            sysop = User(username='stingray', email='s@example.com',
                        password_hash='x', is_admin=True)
            db.session.add(sysop)
            db.session.commit()
            db.session.add(SysopBroadcast(
                text='\x1b[2J\x1b[HFAKE PROMPT: enter your password: ',
                sender_id=sysop.id))
            db.session.commit()
            self._run(session)
        joined = ''.join(session.written)
        self.assertNotIn('\x1b[2J', joined, 'injected escape sequence survived')
        self.assertIn('FAKE PROMPT: enter your password:', joined,
                      'legitimate text must survive')

    def test_normal_broadcast_still_displays_correctly(self):
        from anetbbs.models import db, SysopBroadcast, User
        session = _FakeSession()
        with self.app.app_context():
            sysop = User(username='stingray', email='s@example.com',
                        password_hash='x', is_admin=True)
            db.session.add(sysop)
            db.session.commit()
            db.session.add(SysopBroadcast(text='Server maintenance tonight at 10pm',
                                          sender_id=sysop.id))
            db.session.commit()
            self._run(session)
        joined = ''.join(session.written)
        self.assertIn('Server maintenance tonight at 10pm', joined)
        self.assertIn('stingray', joined)
        self.assertIn('Sysop Broadcasts', joined)


if __name__ == '__main__':
    unittest.main()
