"""Regression test for a real High finding from a security/performance
audit (2026-09-10): menu_engine.py's _act_oneliners() (action_type
'oneliners' -- Mystic-style "leave a one-liner" wall) wrote another
user's OneLiner.text directly to the viewing user's real ANSI terminal
with NO escape-sequence stripping. Any logged-in user can leave a
one-liner (no sysop privilege required), so a malicious one-liner could
embed raw ANSI/CSI control sequences to manipulate -- clear, spoof a
fake prompt on, reposition text on -- every OTHER user's real terminal
who views the one-liner wall. This is exactly the cross-user display
vulnerability class anetbbs/core/text_safety.py's strip_untrusted_escapes()
exists to close (already used for finger profile fields, notification
titles, sysop broadcasts, etc.) -- this call site was simply missed.

Fixed by routing the displayed text through strip_untrusted_escapes()
before it's written to the viewer's terminal.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeWriter:
    def __init__(self, peer=('1.2.3.4', 1234)):
        self._peer = peer
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return self._peer if key == 'peername' else None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass


class _InstantReader:
    """Feeds `chunks` with no delay, then goes dry (returns b'' forever)."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class MenuEngineOnelinerAnsiInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.menu_oneliner_ansi_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, OneLiner
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            attacker = User(username='attacker', email='attacker@example.com',
                            password_hash='x', access_level=10)
            db.session.add(attacker)
            db.session.flush()
            # A malicious one-liner embedding a raw ANSI "clear screen +
            # home" sequence -- exactly the ANSI-bomb shape
            # text_safety.py's own docstring describes.
            db.session.add(OneLiner(
                user_id=attacker.id,
                text='\x1b[2J\x1b[Hpwned'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_session(self, reader):
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        session = BBSSession(reader, writer, config={})
        session.user = {'id': 1, 'access_level': 10, 'is_admin': False}
        session.window_size = (80, 24)
        return session, writer

    def test_another_users_oneliner_ansi_escapes_are_stripped(self):
        from anetbbs.features import menu_engine
        from anetbbs.web_app import create_app

        class _FakeUI:
            def __init__(self, session):
                self.session = session

        # Enter -- skip leaving our own one-liner.
        reader = _InstantReader([b'\r'])
        session, writer = self._make_session(reader)
        ui = _FakeUI(session)

        with self.app.app_context():
            asyncio.run(menu_engine._act_oneliners(ui, None))

        out = bytes(writer.written)
        self.assertNotIn(b'\x1b[2J', out,
                         "another user's raw ANSI escape sequence reached "
                         "this viewer's real terminal un-stripped")
        self.assertIn(b'pwned', out,
                      'the harmless text content should still display')


if __name__ == '__main__':
    unittest.main()
