"""Regression test: composing a message in the terminal never let the
user choose a tagline at all -- reported live in two stages:

1. "I tried a test message and it never asked me if I wanted to add a
   tagline" -- root cause: the terminal side was opt-in via a hidden
   `/tag` slash command inside ANEdit, with no active prompt, and `/tag`
   wasn't even listed in ANEdit's real displayed help screen
   (_show_help()) -- only in the dead _SLASH_HELP constant nothing ever
   renders.

2. After adding a plain y/n "add a random tagline?" prompt: "you should
   be able to pick from a scrollable list, not just a random one. So you
   should be able to see it" -- the y/n prompt still picked blind.

Fixed by _maybe_prompt_tagline() (anetbbs/features/bbs_ui.py) using the
same _rss_lightbar scrollable selector as every other terminal list in
this app, called from all three terminal compose sites (_post_compose,
_send_pm, _compose_echomail) right before launch_anedit(). Also added
/tag to the real help screen as a bonus power-user toggle.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FakeSession:
    def __init__(self, keys):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.written = []
        self._keys = list(keys)

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._keys.pop(0) if self._keys else 'Q'

    async def read_line(self, prompt=''):
        return ''


class MaybePromptTaglineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.terminal_tagline_prompt_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, Tagline
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

    def _seed(self, texts):
        from anetbbs.models import db, Tagline
        with self.app.app_context():
            Tagline.query.delete()
            for t in texts:
                db.session.add(Tagline(text=t, is_active=True))
            db.session.commit()

    def test_picks_the_selected_tagline_on_enter(self):
        from anetbbs.features.bbs_ui import BBSMenuUI, _maybe_prompt_tagline

        self._seed(['Alpha line', 'Beta line', 'Gamma line'])
        session = FakeSession(keys=['DOWN', 'ENTER'])  # sorted: Alpha, Beta, Gamma -> picks Beta
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            result = asyncio.run(_maybe_prompt_tagline(ui))

        self.assertEqual(result, 'Beta line')

    def test_returns_none_when_user_quits_the_picker(self):
        from anetbbs.features.bbs_ui import BBSMenuUI, _maybe_prompt_tagline

        self._seed(['Alpha line', 'Beta line'])
        session = FakeSession(keys=['Q'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            result = asyncio.run(_maybe_prompt_tagline(ui))

        self.assertIsNone(result)

    def test_shows_a_scrollable_picker_not_a_yes_no_prompt(self):
        """Guards against regressing to the earlier plain y/n prompt --
        the fix must show an actual browsable list of tagline text."""
        from anetbbs.features.bbs_ui import BBSMenuUI, _maybe_prompt_tagline

        self._seed(['Alpha line', 'Beta line'])
        session = FakeSession(keys=['ENTER'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(_maybe_prompt_tagline(ui))

        all_written = ''.join(session.written)
        self.assertIn('Alpha line', all_written,
                     'the actual tagline text must be visible in the picker, '
                     'not just a blind yes/no prompt')
        self.assertNotIn('Add a random tagline?', all_written,
                         'must not regress to the old blind y/n prompt')

    def test_unselected_row_has_an_explicit_foreground_color(self):
        """NOT-selected rows must have an explicit color, matching every
        other lightbar row in the app."""
        from anetbbs.features.bbs_ui import BBSMenuUI, _maybe_prompt_tagline
        from anetbbs.features.ansi_ui import FG

        self._seed(['Alpha line', 'Beta line'])
        # DOWN selects 'Beta line' (index 1), leaving 'Alpha line'
        # (index 0) as an unselected row in the same draw.
        session = FakeSession(keys=['DOWN', 'ENTER'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(_maybe_prompt_tagline(ui))

        all_written = ''.join(session.written)
        self.assertIn(FG['wht'] + 'Alpha line', all_written,
                     'a NOT-selected row must have an explicit color, '
                     'matching every other lightbar row in the app')

    def test_selected_row_does_not_rely_on_reverse_video_at_all(self):
        """Reported live three times: the selected row rendered as a
        completely blank/unreadable bar. Attempt 1 added an explicit
        color (FG['wht']) on top of the reverse-video SEL wrapper --
        still invisible. Attempt 2 removed the color, matching every
        other lightbar row's convention of relying purely on
        reverse-video against default colors -- STILL invisible,
        proving reverse+bold itself (not what render_row does with
        color) is broken in this terminal client (SyncTERM). The fix
        sidesteps reverse-video for this row entirely: cancel SEL's
        escape codes with a literal \\x1b[0m and draw a plain
        '> marker + bright color' instead, which doesn't depend on how
        this specific client's reverse-video interacts with bold."""
        from anetbbs.features.bbs_ui import BBSMenuUI, _maybe_prompt_tagline
        from anetbbs.features.ansi_ui import FG

        self._seed(['Alpha line', 'Beta line'])
        session = FakeSession(keys=['ENTER'])  # picks index 0, 'Alpha line', while selected
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(_maybe_prompt_tagline(ui))

        all_written = ''.join(session.written)
        self.assertIn('\x1b[0m' + FG['yel'] + '> Alpha line', all_written,
                     'the selected row must explicitly cancel the reverse-'
                     'video wrapper and draw its own visible marker+color, '
                     'not depend on reverse-video working at all')
        self.assertNotIn(FG['wht'] + 'Alpha line', all_written,
                         'the SELECTED row must use its own marker+color '
                         'style, not the unselected-row color')

    def test_does_not_prompt_when_pool_is_empty(self):
        from anetbbs.features.bbs_ui import BBSMenuUI, _maybe_prompt_tagline

        self._seed([])
        session = FakeSession(keys=[])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            result = asyncio.run(_maybe_prompt_tagline(ui))

        self.assertIsNone(result)
        self.assertEqual(session.written, [],
                         'must not show a picker at all when the pool is empty')


if __name__ == '__main__':
    unittest.main()
