"""Regression tests for a real live bug: in the "Send InterBBS MSG"
recipient pickers (BBSMenuUI._msp_pick_directory_bbs(),
_msp_pick_online_user(), _msp_pick_recipient(), send_imsg() in
anetbbs/features/bbs_ui.py), Q/ESC silently fell through to manual
user@host entry instead of actually cancelling -- both outcomes
collapsed to the same `None` return value, so a caller had no way to
tell "user wants to type it manually" apart from "user backed out".

Jerry, testing v1.1.8 live: "in SEND InterBBS MSG - you cannot Q quit
back or use esc to quit, it goes to manual .. lol I think that should
be M instead of Q and Q and ESC will work to quit."

Fixed by giving manual entry its own dedicated 'M' hotkey and its own
sentinel return value (the string 'MANUAL'), reserving `None` strictly
for "the user genuinely cancelled" at every step of the picker chain.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, arrow_keys=None, lines=None, window_size=(80, 24)):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.written = []
        self._arrow_keys = list(arrow_keys or [])
        self._lines = list(lines or [])
        self.window_size = window_size

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._arrow_keys.pop(0) if self._arrow_keys else 'Q'

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return self._lines.pop(0) if self._lines else ''

    def transcript(self):
        return ''.join(self.written)


class MspPickerCancelVsManualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.msp_picker_cancel_vs_manual_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, BbsDirectoryEntry
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            BbsDirectoryEntry.query.delete()
            db.session.add(BbsDirectoryEntry(
                hostname='alpha.example.com', name='Alpha BBS',
                sysop='Alice', location='Testville', software='ANetBBS',
                msp_port=18, systat_port=11, source='manual'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    # -- _msp_pick_directory_bbs() ------------------------------------

    def test_q_cancels_with_none_not_manual(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['Q'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            result = asyncio.run(ui._msp_pick_directory_bbs())
        self.assertIsNone(result)

    def test_esc_cancels_with_none_not_manual(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['ESC'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            result = asyncio.run(ui._msp_pick_directory_bbs())
        self.assertIsNone(result)

    def test_m_hotkey_returns_manual_sentinel(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['M'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            result = asyncio.run(ui._msp_pick_directory_bbs())
        self.assertEqual(result, 'MANUAL')

    def test_enter_still_picks_a_row(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['ENTER'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            result = asyncio.run(ui._msp_pick_directory_bbs())
        self.assertIsInstance(result, dict)
        self.assertEqual(result['hostname'], 'alpha.example.com')

    def test_hint_no_longer_calls_q_manual_entry(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['Q'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui._msp_pick_directory_bbs())
        text = session.transcript()
        self.assertNotIn('Q=type user@host manually', text)
        self.assertIn('Manual entry', text)

    def test_empty_directory_still_falls_through_to_manual(self):
        from anetbbs.models import BbsDirectoryEntry, db
        from anetbbs.features.bbs_ui import BBSMenuUI
        with self.app.app_context():
            BbsDirectoryEntry.query.delete()
            db.session.commit()
        try:
            session = _FakeSession(lines=[''])
            ui = BBSMenuUI(session)
            with self._patched_app():
                result = asyncio.run(ui._msp_pick_directory_bbs())
            self.assertEqual(result, 'MANUAL')
        finally:
            with self.app.app_context():
                db.session.add(BbsDirectoryEntry(
                    hostname='alpha.example.com', name='Alpha BBS',
                    sysop='Alice', location='Testville', software='ANetBBS',
                    msp_port=18, systat_port=11, source='manual'))
                db.session.commit()

    # -- _msp_pick_recipient() propagation -----------------------------

    def test_recipient_propagates_none_on_cancel(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['Q'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            result = asyncio.run(ui._msp_pick_recipient())
        self.assertIsNone(result)

    def test_recipient_propagates_manual_sentinel(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['M'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            result = asyncio.run(ui._msp_pick_recipient())
        self.assertEqual(result, 'MANUAL')

    def test_recipient_propagates_cancel_from_the_online_user_step(self):
        # A non-empty probe reply reaches the online-user lightbar
        # itself (rather than the auto-manual-fallback for an empty/
        # failed probe), so 'Q' there is actually exercised.
        from anetbbs.features.bbs_ui import BBSMenuUI
        reply = (
            "ANetBBS test.example.com - Test\r\n\r\n"
            f"{'Node':>4}  {'User':<22} {'Action':<24} {'Idle':>5}\r\n"
            f"{'-'*4:>4}  {'-'*22:<22} {'-'*24:<24} {'-'*5:>5}\r\n"
            f"{1:>4}  {'alice':<22} {'Reading mail':<24} {'0:01':>5}\r\n"
        )
        session = _FakeSession(arrow_keys=['ENTER', 'Q'])
        ui = BBSMenuUI(session)
        with self._patched_app(), \
             patch('anetbbs.msp.systat.query_systat', return_value=reply):
            result = asyncio.run(ui._msp_pick_recipient())
        self.assertIsNone(result)

    # -- send_imsg() end-to-end -----------------------------------------

    def test_send_imsg_q_cancels_without_prompting_for_manual_entry(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['Q'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.send_imsg())
        text = session.transcript()
        self.assertNotIn('Destination (user@host)', text,
                         'Q must cancel outright, not fall through to the '
                         'manual entry prompt')

    def test_send_imsg_m_prompts_for_manual_entry(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(arrow_keys=['M'], lines=['bob@beta.example.com', ''])
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.send_imsg())
        text = session.transcript()
        self.assertIn('Destination (user@host)', text)


if __name__ == '__main__':
    unittest.main()
