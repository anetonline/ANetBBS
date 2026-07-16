"""Regression test: the terminal "File Library - Areas" screen used to
dump every configured file area top-to-bottom with a plain read_line()
number prompt, no pagination -- a sysop with more file areas than fit
one screen (reported live: "I have so many it is over a page") had to
rely on the terminal client's own scrollback to see the top entries.
Message areas and the RSS reader already use the shared _rss_lightbar
scrollable selector for exactly this reason; file areas now use the
same pattern.
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
    def __init__(self, keys):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.written = []
        self._keys = list(keys)
        self.initial_draw_len = None

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        if self.initial_draw_len is None:
            self.initial_draw_len = len(self.written)
        return self._keys.pop(0) if self._keys else 'Q'

    async def read_line(self, prompt=''):
        return ''


class FileAreasLightbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_areas_lightbar_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, FileArea
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            # More areas than _LB_VISIBLE (15) -- the exact scenario
            # that used to force terminal-client scrollback.
            for i in range(1, 21):
                db.session.add(FileArea(
                    tag=f'AREA{i:02d}', name=f'zzz Area {i:02d}',
                    description='', is_active=True, is_subscribed=True,
                    is_sysop_only=False, min_access_level=10,
                    upload_permission='users', storage_path=''))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_initial_draw_does_not_dump_every_area(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = FakeSession(keys=['Q'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(ui.list_files())

        initial_draw = ''.join(session.written[:session.initial_draw_len])
        self.assertIn('zzz Area 01', initial_draw,
                      'first area should be visible in the initial draw')
        self.assertNotIn('zzz Area 20', initial_draw,
                         'the 20th area must NOT be in the initial draw -- '
                         'if it is, the screen went back to dumping every '
                         'area instead of a bounded, scrollable window')

    def test_end_then_enter_opens_the_last_area(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = FakeSession(keys=['END', 'ENTER'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch.object(ui, '_file_area_browse', new=AsyncMock()) as mock_browse:
            asyncio.run(ui.list_files())

        mock_browse.assert_called_once()
        args = mock_browse.call_args.args
        # (area_id, area_name, area_filter, can_upload, uploads_dir,
        #  web_base, protos, storage_path)
        self.assertEqual(args[1], 'zzz Area 20')
        self.assertEqual(args[2], 'area')

    def test_a_hotkey_still_opens_all_files(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = FakeSession(keys=['A'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch.object(ui, '_file_area_browse', new=AsyncMock()) as mock_browse:
            asyncio.run(ui.list_files())

        mock_browse.assert_called_once()
        args = mock_browse.call_args.args
        self.assertEqual(args[2], 'all')


if __name__ == '__main__':
    unittest.main()
