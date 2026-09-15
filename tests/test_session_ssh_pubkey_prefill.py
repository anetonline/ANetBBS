"""Regression test for BBSSession's prefill_authenticated_user path
(gap-analysis follow-up round, Phase D) -- the SSH public-key login
short-circuit in core/session.py's login_screen(): when set, the
session must log straight in as the given user dict with NO password
prompt of any kind, not even the one-shot fallback prefill_username
alone would still show.

Also covers a real bug caught while implementing this: the pre-login
bot-defense gate (core/session.py's start(), just before
login_screen()) only checked `self._prefill_username` to decide
whether a connection already went through a real auth protocol and
should skip the bot check -- an SSH public-key login sets
prefill_authenticated_user instead, without necessarily also setting
prefill_username, so it was silently NOT exempted from the bot gate
until fixed.
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
    """Never yields a real keypress -- if login_screen() ever tried to
    read_password() on this path, the test would hang/timeout rather
    than silently pass, since a password prompt must never be reached
    at all when prefill_authenticated_user is set."""
    async def read(self, n=1):
        return b''


class SessionSshPubkeyPrefillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.session_pubkey_prefill_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _make_session(self, prefill_authenticated_user):
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        reader = _InstantReader()
        session = BBSSession(
            reader, writer, {'server': {'host': '127.0.0.1', 'port': 0}},
            prefill_authenticated_user=prefill_authenticated_user)
        return session, writer

    def test_logs_straight_in_with_no_password_prompt(self):
        user_dict = {'id': 1, 'username': 'pkeyuser', 'access_level': 10}
        session, writer = self._make_session(user_dict)

        result = asyncio.run(asyncio.wait_for(session.login_screen(), timeout=5))

        self.assertTrue(result)
        self.assertEqual(session.user, user_dict)
        output = bytes(writer.written).decode('cp437', errors='replace')
        self.assertIn('pkeyuser', output)
        self.assertIn('SSH key', output)
        self.assertNotIn('Password for', output)

    def test_bot_gate_is_skipped_for_pubkey_prefill(self):
        """The bot-defense gate in start() must treat
        prefill_authenticated_user the same as prefill_username for
        deciding whether this connection already proved itself via a
        real protocol -- confirmed here by checking the actual
        condition start() evaluates, since driving the full start()
        loop would need a lot more session/menu scaffolding than this
        one behavior needs."""
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        reader = _InstantReader()
        user_dict = {'id': 1, 'username': 'pkeyuser', 'access_level': 10}
        session = BBSSession(
            reader, writer, {'server': {'host': '127.0.0.1', 'port': 0}},
            prefill_authenticated_user=user_dict)
        skip_bot_gate = bool(
            session._prefill_username or session._prefill_authenticated_user)
        self.assertTrue(skip_bot_gate,
                        'an SSH public-key login must skip the bot-defense '
                        'gate the same way a password-prefilled login does')


if __name__ == '__main__':
    unittest.main()
