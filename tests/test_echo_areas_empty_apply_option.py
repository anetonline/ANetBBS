"""Regression test: the terminal "Echomail Networks" screen used to
return immediately with "No echomail areas configured." whenever
net_rows was empty -- which happens on any fresh install (zero active
networks, zero subscribed areas by seed default), BEFORE the code even
reached the "A = Apply for ANotherNetwork QWK node" option. That made
the apply option unreachable for exactly the sysop it exists for: a
new install with nothing configured yet. Live-caught by Jerry testing
his Pi install after updating.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FakeSession:
    def __init__(self, inputs):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 10,
                     'is_admin': False}
        self._inputs = list(inputs)
        self.write = AsyncMock()

    async def read_line(self, prompt=''):
        return self._inputs.pop(0) if self._inputs else ''


class EchoAreasEmptyApplyOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.echo_areas_apply_test.db')
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
            # Deliberately do NOT seed any active networks or subscribed
            # areas -- this is the exact fresh-install state that
            # triggered the bug.

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_apply_option_reachable_with_zero_configured_areas(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = FakeSession(inputs=['A', 'Q'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch.object(ui, '_apply_qwk_node', new=AsyncMock()) as mock_apply:
            asyncio.run(ui.list_echo_areas())

        mock_apply.assert_called_once()

    def test_shows_a_helpful_message_instead_of_bailing_out(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = FakeSession(inputs=['Q'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(ui.list_echo_areas())

        all_output = ''.join(
            call.args[0] for call in session.write.call_args_list
            if call.args)
        self.assertIn('apply', all_output.lower())
        self.assertNotIn('no echomail areas configured.\r\n', all_output.lower())


if __name__ == '__main__':
    unittest.main()
