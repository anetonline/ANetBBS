"""Regression test: the "Compose > <network>" area picker inside
_compose_echomail() used a manual numbered list with a "-- more (Enter /
Q) --" page-break prompt every 17 rows (plus separate "── category ──"
separator lines), instead of the scrollable _rss_lightbar selector
already used by the read-side area picker (_list_network_areas) and
message boards. Reported live via a real terminal capture showing
`-- more (Enter / Q) --` on a 13-area "AnotherNetwork" compose screen
("compose echomail should be lightbar scrollable").
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FakeSession:
    def __init__(self, keys, window_size=(80, 24)):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.written = []
        self._keys = list(keys)
        self.window_size = window_size

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._keys.pop(0) if self._keys else 'Q'

    async def read_line(self, prompt=''):
        return ''


class ComposeEchomailAreaLightbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.compose_echomail_lightbar_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, EchomailNetwork, EchoArea
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            net = EchomailNetwork.query.filter_by(name='AnotherNetwork').first()
            if not net:
                net = EchomailNetwork(name='AnotherNetwork', network_type='binkp',
                                     is_active=True, our_address='1:1/1')
                db.session.add(net)
                db.session.commit()
            cls.net_id = net.id

            # More areas than _LB_VISIBLE -- the exact scenario that
            # triggered the old "-- more --" pager, spread across
            # several categories like the real report.
            EchoArea.query.filter_by(network_id=net.id).delete()
            cats = ['General', 'BBS Scene', 'Data']
            for i in range(1, 21):
                db.session.add(EchoArea(
                    network_id=net.id, tag=f'ANN.AREA{i:02d}',
                    name=f'Area {i:02d}', category=cats[i % len(cats)],
                    is_active=True, is_subscribed=True,
                    is_sysop_only=False, min_access_level=10, order=i))
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

    def test_area_picker_does_not_show_the_old_more_pager(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        # 1=pick network, then Q to back out of the area lightbar. Q on
        # the area lightbar returns to the network chooser (matches
        # read_echo_area's nested-Q-backs-out-one-level precedent), so
        # the network prompt must answer 'Q' on its SECOND visit or this
        # loops forever picking network 1 again each time.
        session = FakeSession(keys=[])

        net_inputs = iter(['1', 'Q'])

        async def _read_line(prompt=''):
            return next(net_inputs, 'Q')
        session.read_line = _read_line

        async def _read_key_arrow():
            return 'Q'
        session.read_key_arrow = _read_key_arrow

        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.compose_echomail())

        all_written = ''.join(session.written)
        self.assertNotIn('-- more (Enter / Q) --', all_written,
                         'the old page-break prompt must not appear -- the '
                         'area picker should be a scrollable lightbar now')

    def test_area_picker_lists_all_areas_for_lightbar_scrolling(self):
        """The lightbar draws a fixed viewport internally, but the area
        list handed to it must contain every area (not truncated at the
        old PAGE=17 boundary) so Up/Dn/PgDn can actually reach them all."""
        from anetbbs.features.bbs_ui import BBSMenuUI
        from unittest.mock import patch

        captured = {}
        real_rss_lightbar = BBSMenuUI._rss_lightbar

        async def _spy_rss_lightbar(self, rows, *args, **kwargs):
            captured['n_rows'] = len(rows)
            return ('quit',)

        session = FakeSession(keys=[])

        net_inputs = iter(['1', 'Q'])

        async def _read_line(prompt=''):
            return next(net_inputs, 'Q')
        session.read_line = _read_line

        ui = BBSMenuUI(session)
        with self._patched_app(), \
             patch.object(BBSMenuUI, '_rss_lightbar', _spy_rss_lightbar):
            asyncio.run(ui.compose_echomail())

        self.assertEqual(captured.get('n_rows'), 20,
                         'all 20 areas must be passed to the lightbar, not '
                         'paginated into pages of 17 like the old picker')

    def test_selecting_an_area_via_enter_reaches_the_compose_step(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        # '1' picks the only network; ENTER (default lightbar selection,
        # index 0) picks the first area; then empty subject cancels
        # compose cleanly (avoids driving the full ANEdit flow).
        session = FakeSession(keys=['ENTER'])

        line_inputs = iter(['1', '', ''])

        async def _read_line(prompt=''):
            return next(line_inputs, '')
        session.read_line = _read_line

        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.compose_echomail())

        all_written = ''.join(session.written)
        self.assertIn('COMPOSE', all_written.upper())
        self.assertNotIn('-- more (Enter / Q) --', all_written)

    def test_no_row_overflows_a_wide_132_col_terminal(self):
        """Reported live via screenshot on a real 132x37 SyncTERM
        session: rows corrupted with stray fragments ("tegory", "S
        Scene", "neral") after scrolling down then back up. Root cause:
        render_row_carea/render_header_carea computed the Name column
        width as `_w - 28`, undercounting the row's real overhead
        (idx+tag+spacing+category = 37 visible chars, not 28) by 9
        chars -- on a 132-col terminal the row text overflowed the
        line width and the terminal auto-wrapped the overflow onto the
        next line, which _rss_lightbar's absolute-cursor-position
        partial redraws then only partially overwrote, leaving stale
        fragments behind. This strips ANSI escapes and measures the
        real visible width of every row + the header line against
        ui_width() to guard against any row ever exceeding it again.
        """
        import re
        from anetbbs.features.bbs_ui import BBSMenuUI
        from anetbbs.features.ansi_ui import ui_width

        ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

        session = FakeSession(keys=[], window_size=(132, 37))

        net_inputs = iter(['1', 'Q'])

        async def _read_line(prompt=''):
            return next(net_inputs, 'Q')
        session.read_line = _read_line

        async def _read_key_arrow():
            return 'Q'
        session.read_key_arrow = _read_key_arrow

        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.compose_echomail())

        _w = ui_width(session)
        for raw in session.written:
            for line in raw.split('\r\n'):
                visible = ANSI_RE.sub('', line)
                self.assertLessEqual(
                    len(visible), _w,
                    f'row exceeds the {_w}-col terminal width and will '
                    f'auto-wrap, corrupting subsequent rows: {visible!r}')


if __name__ == '__main__':
    unittest.main()
