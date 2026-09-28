"""Regression tests for the terminal "Echomail Networks" screen's
conversion from a read_line()/type-a-number chooser to a real arrow-key
lightbar (BBSMenuUI.list_echo_areas(), anetbbs/features/bbs_ui.py),
requested live 2026-09-29 ("echomail, is missing the lightbar and a
search") -- same _rss_lightbar widget as the boards/threads/PM/file
browser conversions earlier the same day. Also covers the "S" search
hotkey (routes to search_messages()) and the clarified "A=Apply(QWK
Node)" hint wording ("the apply, which is for quick, just says apply.
Lets change it to, apply for a QWK NODE").

The zero-networks case (still read_line()-based) is covered separately
in test_echo_areas_empty_apply_option.py and is untouched by this
conversion.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, keys=None):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.window_size = (80, 24)
        self.written = []
        self._keys = list(keys or [])

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._keys.pop(0) if self._keys else 'Q'

    def transcript(self):
        return ''.join(self.written)


def _strip_ansi(text):
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


class EchoNetworksLightbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.echo_networks_lightbar_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, EchoArea, EchomailNetwork
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            net = EchomailNetwork(name='TestNet', network_type='binkp',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='TEST.GENERAL',
                            name='General', is_active=True,
                            is_subscribed=True, min_access_level=0)
            db.session.add(area)
            db.session.commit()
            cls.net_id = net.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def test_enter_opens_the_highlighted_network(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(keys=['ENTER'])
        ui = BBSMenuUI(session)
        with self._patched_app(), \
             patch.object(ui, '_list_network_areas', new=AsyncMock()) as mock_areas:
            asyncio.run(ui.list_echo_areas())
        mock_areas.assert_called_once()
        self.assertEqual(mock_areas.call_args.args[0], self.net_id)

    def test_s_hotkey_routes_to_search_messages(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(keys=['S'])
        ui = BBSMenuUI(session)
        with self._patched_app(), \
             patch.object(ui, 'search_messages', new=AsyncMock()) as mock_search:
            asyncio.run(ui.list_echo_areas())
        mock_search.assert_called_once()

    def test_a_hotkey_routes_to_qwk_apply(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(keys=['A'])
        ui = BBSMenuUI(session)
        with self._patched_app(), \
             patch.object(ui, '_apply_qwk_node', new=AsyncMock()) as mock_apply:
            asyncio.run(ui.list_echo_areas())
        mock_apply.assert_called_once()

    def test_hint_clarifies_apply_is_for_a_qwk_node(self):
        # Jerry: "the apply, which is for quick, just says apply. Lets
        # change it to, apply for a QWK NODE" -- the compact hint line
        # (as opposed to the longer explanatory line above the list)
        # used to just say "A=apply".
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(keys=['Q'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.list_echo_areas())
        text = _strip_ansi(session.transcript())
        self.assertIn('Apply(QWK Node)', text)

    def test_selected_row_does_not_rely_on_reverse_video(self):
        from anetbbs.features.ansi_ui import FG
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(keys=['Q'])  # row 0 stays selected throughout
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.list_echo_areas())
        text = session.transcript()
        self.assertIn('\x1b[0m' + FG['yel'] + '> ', text,
                      'selected row must explicitly cancel SEL and draw '
                      'its own visible marker+color')

    def test_row_width_never_overflows_80_columns(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(keys=['Q'])
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.list_echo_areas())
        import re
        for w in session.written:
            visible_call = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', w)
            for line in visible_call.split('\r\n'):
                self.assertLessEqual(
                    len(line), 80,
                    f'rendered line exceeds 80 columns ({len(line)}): {line!r}')


if __name__ == '__main__':
    unittest.main()
