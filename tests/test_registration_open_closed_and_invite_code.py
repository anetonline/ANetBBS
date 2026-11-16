"""Regression test: there was no way for a sysop to close new-account
registration at all, or require a pre-shared invite code (Synchronet's
own convention) -- web/auth.py's register() was unconditionally
reachable, only gated by IP ban / country block / rate limit.
REGISTRATION_OPEN (default True, unchanged existing behavior) and
REGISTRATION_INVITE_CODE (blank = none required, default) added to
config.py; enforced at both the web route (web/auth.py) and the
terminal entry point (core/session.py's handle_registration() --
shared by telnet/SSH/rlogin/PETSCII, all of which funnel through the
same BBSSession method).
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _fresh_app(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class WebRegistrationOpenClosedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.reg_open_closed_test.db')
        cls.app = _fresh_app(cls._tmp_db)
        from anetbbs.models import db
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _register_payload(self, username, email, invite_code=None):
        from anetbbs.models import DEFAULT_SECURITY_QUESTIONS as SECURITY_QUESTIONS
        payload = {
            'username': username, 'email': email,
            'password': 'correcthorsebatterystaple',
            'password2': 'correcthorsebatterystaple',
            'question_1': SECURITY_QUESTIONS[0], 'answer_1': 'answer1',
            'question_2': SECURITY_QUESTIONS[1], 'answer_2': 'answer2',
            'question_3': SECURITY_QUESTIONS[2], 'answer_3': 'answer3',
        }
        if invite_code is not None:
            payload['invite_code'] = invite_code
        return payload

    def _post_register(self, remote_addr, payload):
        # Each test gets its own simulated source IP -- register()'s own
        # rate limit (REGISTER_LIMIT_COUNT = 3 per
        # REGISTER_LIMIT_WINDOW_MINUTES) counts EVERY POST, including
        # refused ones, and this class shares one DB across all test
        # methods (setUpClass, not setUp) -- sharing one IP across 4
        # tests would trip the real rate limit by the 4th request,
        # failing it for a completely unrelated reason.
        client = self.app.test_client()
        return client.post('/auth/register', data=payload, follow_redirects=True,
                           environ_overrides={'REMOTE_ADDR': remote_addr})

    def test_open_by_default_registration_succeeds(self):
        resp = self._post_register(
            '203.0.113.1', self._register_payload('regopentest1', 'ro1@example.com'))
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import User
            self.assertIsNotNone(User.query.filter_by(username='regopentest1').first())

    def test_closed_refuses_registration_with_a_friendly_message(self):
        self.app.config['REGISTRATION_OPEN'] = False
        try:
            resp = self._post_register(
                '203.0.113.2', self._register_payload('regclosedtest1', 'rc1@example.com'))
        finally:
            self.app.config['REGISTRATION_OPEN'] = True
        self.assertEqual(resp.status_code, 200)
        self.assertIn('currently closed', resp.get_data(as_text=True))
        with self.app.app_context():
            from anetbbs.models import User
            self.assertIsNone(User.query.filter_by(username='regclosedtest1').first(),
                             'no account must be created while registration is closed')

    def test_invite_code_required_when_configured_wrong_code_refused(self):
        self.app.config['REGISTRATION_INVITE_CODE'] = 'letmein'
        try:
            resp = self._post_register('203.0.113.3', self._register_payload(
                'regbadcodetest', 'rb1@example.com', invite_code='wrongcode'))
        finally:
            self.app.config['REGISTRATION_INVITE_CODE'] = ''
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Incorrect invite code', resp.get_data(as_text=True))
        with self.app.app_context():
            from anetbbs.models import User
            self.assertIsNone(User.query.filter_by(username='regbadcodetest').first())

    def test_invite_code_required_when_configured_correct_code_succeeds(self):
        self.app.config['REGISTRATION_INVITE_CODE'] = 'letmein'
        try:
            resp = self._post_register('203.0.113.4', self._register_payload(
                'reggoodcodetest', 'rg1@example.com', invite_code='letmein'))
        finally:
            self.app.config['REGISTRATION_INVITE_CODE'] = ''
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import User
            self.assertIsNotNone(User.query.filter_by(username='reggoodcodetest').first())


class _FakeTerminalSession:
    """Minimal fake for BBSSession.handle_registration()'s own gate
    checks, without running the rest of the real registration flow."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.written = []

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return self._responses.pop(0) if self._responses else ''

    def transcript(self):
        return ''.join(self.written)


class TerminalRegistrationOpenClosedTests(unittest.TestCase):
    """handle_registration() is shared by telnet/SSH/rlogin/PETSCII --
    testing it directly covers all of them."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.reg_open_closed_terminal_test.db')
        cls.app = _fresh_app(cls._tmp_db)
        from anetbbs.models import db
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_closed_refuses_before_prompting_for_anything(self):
        from anetbbs.core.session import BBSSession
        session = _FakeTerminalSession([])
        self.app.config['REGISTRATION_OPEN'] = False
        try:
            with __import__('unittest.mock', fromlist=['patch']).patch(
                    'anetbbs.features.bbs_ui._app', return_value=self.app):
                result = asyncio.run(
                    BBSSession.handle_registration(session))
        finally:
            self.app.config['REGISTRATION_OPEN'] = True
        self.assertFalse(result)
        self.assertIn('currently closed', session.transcript())

    def test_required_email_verification_directs_terminal_signup_to_website(self):
        from anetbbs.core.session import BBSSession

        session = _FakeTerminalSession([])
        session.read_line = AsyncMock(wraps=session.read_line)
        session.read_password = AsyncMock()
        session.user_manager = Mock()

        with (
            patch.dict(self.app.config, {
                'REGISTRATION_OPEN': True,
                'REGISTRATION_INVITE_CODE': '',
                'REGISTRATION_REQUIRE_VERIFIED_EMAIL': True,
            }),
            patch('anetbbs.features.bbs_ui._app', return_value=self.app),
        ):
            result = asyncio.run(BBSSession.handle_registration(session))

        self.assertIs(result, False)
        self.assertIn('Please register on the BBS website to verify your email.',
                      session.transcript())
        session.read_line.assert_not_called()
        session.read_password.assert_not_called()
        session.user_manager.create_user.assert_not_called()

    def test_wrong_invite_code_refuses(self):
        from anetbbs.core.session import BBSSession
        session = _FakeTerminalSession(['wrongcode'])
        self.app.config['REGISTRATION_INVITE_CODE'] = 'letmein'
        try:
            with __import__('unittest.mock', fromlist=['patch']).patch(
                    'anetbbs.features.bbs_ui._app', return_value=self.app):
                result = asyncio.run(
                    BBSSession.handle_registration(session))
        finally:
            self.app.config['REGISTRATION_INVITE_CODE'] = ''
        self.assertFalse(result)
        self.assertIn('Incorrect invite code', session.transcript())


if __name__ == '__main__':
    unittest.main()
