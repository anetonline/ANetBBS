"""Regression test for a live-caught DetachedInstanceError.

Jerry applied for a QWK node from his Pi (a peer install), got approved
on the hub, then pressed "A" again on the Pi to check status/see the
password -- and the terminal session crashed with:

    File "bbs_ui.py", line 1032, in _show_qwk_request_status
        date_str = req.created_at.strftime('%Y-%m-%d') if req.created_at else '?'
    sqlalchemy.orm.exc.DetachedInstanceError

Root cause: _apply_qwk_node() queried the existing QWKNodeRequest row
inside one `with _app().app_context():` block, then passed that ORM
object to _show_qwk_request_status() *after* the block (and its DB
session) had already closed. Touching an expired attribute on a
detached instance raises DetachedInstanceError instead of silently
re-fetching it.

Fixed by having _show_qwk_request_status() take an id and do its own
fresh query inside one consistent app_context, extracting every field
it needs into plain local variables before that context closes.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FakeSession:
    def __init__(self, inputs=None):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 10,
                     'is_admin': False}
        self._inputs = list(inputs or [])
        self.write = AsyncMock()

    async def read_line(self, prompt=''):
        return self._inputs.pop(0) if self._inputs else ''


class QwkApplyStatusDetachedInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_apply_status_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_URL'] = 'https://bbs.a-net.fyi'
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    _token_counter = 0

    def _make_request(self, **overrides):
        from anetbbs.models import db, QWKNodeRequest
        type(self)._token_counter += 1
        defaults = dict(
            bbs_name='Test Pi BBS', packet_id='TESTPI', sysop_name='Tester',
            applied_by_user_id=1, applied_by_username='testuser',
            applied_via='terminal_remote', status='approved',
            generated_password='hunter2',
            request_token=f'tok{self._token_counter}',
        )
        defaults.update(overrides)
        with self.app.app_context():
            req = QWKNodeRequest(**defaults)
            db.session.add(req)
            db.session.commit()
            return req.id

    def test_viewing_an_approved_request_on_a_peer_install_does_not_crash(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        req_id = self._make_request()

        session = FakeSession(inputs=[''])
        ui = BBSMenuUI(session)

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.headers = {'content-type': 'application/json'}
        fake_resp.json.return_value = {
            'ok': True, 'status': 'approved', 'generated_password': 'hunter2',
        }

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('requests.get', return_value=fake_resp):
            import asyncio
            # This is the exact call chain that crashed: _apply_qwk_node()
            # (which found an existing request) calling
            # _show_qwk_request_status(existing_id, ...) for a peer install.
            asyncio.run(ui._show_qwk_request_status(
                req_id, is_hub=False, registry_url='https://bbs.a-net.fyi'))

        all_output = ''.join(
            call.args[0] for call in session.write.call_args_list if call.args)
        self.assertIn('APPROVED', all_output)
        self.assertIn('hunter2', all_output)
        self.assertIn('TESTPI', all_output)

    def test_pending_request_status_does_not_crash(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        req_id = self._make_request(status='pending', generated_password=None,
                                    packet_id='PENDNG')

        session = FakeSession(inputs=[''])
        ui = BBSMenuUI(session)

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.headers = {'content-type': 'application/json'}
        fake_resp.json.return_value = {'ok': True, 'status': 'pending'}

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('requests.get', return_value=fake_resp):
            import asyncio
            asyncio.run(ui._show_qwk_request_status(
                req_id, is_hub=False, registry_url='https://bbs.a-net.fyi'))

        all_output = ''.join(
            call.args[0] for call in session.write.call_args_list if call.args)
        self.assertIn('PENDING', all_output)

    def test_hub_unreachable_falls_back_to_cached_status_without_crashing(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        import requests as _requests

        req_id = self._make_request()

        session = FakeSession(inputs=[''])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('requests.get', side_effect=_requests.RequestException('timeout')):
            import asyncio
            asyncio.run(ui._show_qwk_request_status(
                req_id, is_hub=False, registry_url='https://bbs.a-net.fyi'))

        all_output = ''.join(
            call.args[0] for call in session.write.call_args_list if call.args)
        self.assertIn('APPROVED', all_output)
        self.assertIn('unreachable', all_output.lower())


if __name__ == '__main__':
    unittest.main()
