"""Regression tests for a real gap reported live (2026-09-25): terminal-
mode (SSH/telnet) users could already send/receive MSP messages but had
no way to browse the BBS directory itself, unlike the web UI's own
/imsg/directory page. Added a new "V) BBS Directory" main-menu entry
(browse_bbs_directory() in bbs_ui.py) -- a scrollable lightbar list
(reusing the same _rss_lightbar widget every other terminal list screen
already uses, and the same query shape _msp_pick_directory_bbs() already
used for the InterBBS-IM recipient picker) with a full detail view per
entry on Enter.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FakeSession:
    def __init__(self, arrow_keys=None, window_size=(80, 24)):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.written = []
        self._arrow_keys = list(arrow_keys or [])
        self.window_size = window_size

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._arrow_keys.pop(0) if self._arrow_keys else 'Q'

    async def read_line(self, prompt=''):
        return ''


class BbsDirectoryTerminalBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.bbs_directory_terminal_browser_test.db')
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
                software_version='1.1', msp_port=18, systat_port=11,
                source='manual'))
            db.session.add(BbsDirectoryEntry(
                hostname='beta.example.com', name='Beta BBS',
                sysop='Bob', location='Sampleburg', software='Synchronet',
                software_version='3.20', msp_port=18, systat_port=11,
                notes='Great sysop, terrible pizza.', source='sbbsimsg'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _patched_app(self):
        from unittest.mock import patch
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def test_action_type_is_registered_and_reaches_the_browser(self):
        from anetbbs.features.menu_engine import _ACTIONS
        self.assertIn('bbs_directory', _ACTIONS)

    def test_main_menu_has_a_directory_entry(self):
        from anetbbs.features.menu_engine import DEFAULT_MENUS
        main = next(m for m in DEFAULT_MENUS if m['name'] == 'main')
        hotkeys = [i['hotkey'] for i in main['items']]
        action_types = [i['action_type'] for i in main['items']]
        self.assertIn('bbs_directory', action_types)
        # No duplicate hotkey collision with any existing main-menu item.
        self.assertEqual(len(hotkeys), len(set(hotkeys)),
                         f'duplicate hotkey in main menu: {hotkeys}')

    def test_browsing_lists_every_directory_entry(self):
        session = FakeSession(arrow_keys=['Q'])
        from anetbbs.features.bbs_ui import BBSMenuUI
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.browse_bbs_directory())
        joined = ''.join(session.written)
        self.assertIn('Alpha BBS', joined)
        self.assertIn('Beta BBS', joined)

    def test_last_seen_is_eastern_converted_not_raw_utc(self):
        # Guards against the exact gap test_no_raw_utc_timestamp_display.py
        # exists to catch project-wide: a raw .strftime() on a UTC
        # datetime instead of going through fmt_eastern(). A UTC
        # midnight timestamp becomes the previous evening in Eastern
        # (UTC-4/5), so a raw-vs-converted mix-up shows up as a wrong
        # calendar date, not just a wrong clock time.
        from datetime import datetime
        from anetbbs.models import BbsDirectoryEntry, db
        with self.app.app_context():
            entry = BbsDirectoryEntry.query.filter_by(
                hostname='alpha.example.com').first()
            entry.last_seen_at = datetime(2026, 1, 15, 2, 0)  # 2026-01-15 02:00 UTC
            db.session.commit()
        try:
            session = FakeSession(arrow_keys=['Q'])
            from anetbbs.features.bbs_ui import BBSMenuUI
            ui = BBSMenuUI(session)
            with self._patched_app():
                asyncio.run(ui.browse_bbs_directory())
            joined = ''.join(session.written)
            self.assertIn('2026-01-14', joined,
                          'expected the Eastern (UTC-5 in January) calendar '
                          'date, not the raw UTC one -- a raw .strftime() '
                          'would show 2026-01-15 instead')
            self.assertNotIn('2026-01-15 02:00', joined)
        finally:
            with self.app.app_context():
                entry = BbsDirectoryEntry.query.filter_by(
                    hostname='alpha.example.com').first()
                entry.last_seen_at = None
                db.session.commit()

    def test_selecting_an_entry_shows_full_detail_then_returns_to_list(self):
        # DOWN to the second row, ENTER for detail, then (via read_line
        # returning '' immediately) back to the list, then Q to exit.
        session = FakeSession(arrow_keys=['DOWN', 'ENTER', 'Q'])
        from anetbbs.features.bbs_ui import BBSMenuUI
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.browse_bbs_directory())
        joined = ''.join(session.written)
        self.assertIn('beta.example.com', joined)
        self.assertIn('Bob', joined)
        self.assertIn('Synchronet', joined)
        self.assertIn('3.20', joined)
        self.assertIn('Great sysop, terrible pizza.', joined)

    def test_empty_directory_shows_a_message_and_returns_cleanly(self):
        from anetbbs.models import BbsDirectoryEntry, db
        with self.app.app_context():
            BbsDirectoryEntry.query.delete()
            db.session.commit()
        try:
            session = FakeSession(arrow_keys=[])
            from anetbbs.features.bbs_ui import BBSMenuUI
            ui = BBSMenuUI(session)
            with self._patched_app():
                asyncio.run(ui.browse_bbs_directory())
            joined = ''.join(session.written)
            self.assertIn('No BBSes in the directory yet', joined)
        finally:
            with self.app.app_context():
                db.session.add(BbsDirectoryEntry(
                    hostname='alpha.example.com', name='Alpha BBS',
                    sysop='Alice', location='Testville', software='ANetBBS',
                    software_version='1.1', msp_port=18, systat_port=11,
                    source='manual'))
                db.session.add(BbsDirectoryEntry(
                    hostname='beta.example.com', name='Beta BBS',
                    sysop='Bob', location='Sampleburg', software='Synchronet',
                    software_version='3.20', msp_port=18, systat_port=11,
                    notes='Great sysop, terrible pizza.', source='sbbsimsg'))
                db.session.commit()


if __name__ == '__main__':
    unittest.main()
