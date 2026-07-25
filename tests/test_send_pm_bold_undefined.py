"""Regression test: real bug found in a pre-release audit (pyflakes) --
BBSMenuUI's terminal "Send Private Message" flow (anetbbs.features.
bbs_ui._send_pm) referenced BOLD in its final success message without
importing it (unlike its sibling compose functions _post_compose /
_compose_echomail, which do import it). Every successful terminal PM
send crashed with NameError: name 'BOLD' is not defined right after
the message was already saved to the database -- the PM went through,
but the sender got an unhandled exception instead of the confirmation.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, user, responses):
        self.user = user
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


class SendPmBoldUndefinedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.send_pm_bold_test.db')
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
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_sending_a_pm_does_not_crash_on_the_success_message(self):
        from anetbbs.models import db, User, PrivateMessage
        from anetbbs.features.bbs_ui import BBSMenuUI

        with self.app.app_context():
            sender = User(username='pmsender', email='pmsender@example.com',
                          password_hash='x', access_level=100)
            recipient = User(username='pmrecipient', email='pmrecipient@example.com',
                             password_hash='x', access_level=100)
            db.session.add_all([sender, recipient])
            db.session.commit()
            sender_id = sender.id

        session = _FakeSession(
            user={'id': sender_id, 'username': 'pmsender', 'access_level': 100},
            responses=['pmrecipient', 'Test subject'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_anedit',
                  new_callable=AsyncMock, return_value='Test body'):
            asyncio.run(ui.send_pm())

        self.assertIn('[OK]', session.transcript())
        self.assertIn('Sent to pmrecipient', session.transcript())

        with self.app.app_context():
            pm = PrivateMessage.query.filter_by(sender_id=sender_id).first()
            self.assertIsNotNone(pm)
            self.assertEqual(pm.subject, 'Test subject')
            self.assertEqual(pm.body, 'Test body')


if __name__ == '__main__':
    unittest.main()
