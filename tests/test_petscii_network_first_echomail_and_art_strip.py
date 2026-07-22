"""Regression tests for two real reports testing v1.0b2.189 against the
live ANetBBS install (real subscriptions across tqwnet/Fidonet/DOVE-Net/
sp00knet/ANotherNetwork, real uploaded door-game files with FILE_ID.DIZ-
style ANSI-art descriptions):

1. Echomail area listing combined every subscribed network's areas into
   one flat list ("not all combine") -- now a two-step picker: choose a
   network first, then see only that network's areas.
2. File descriptions containing CP437/Unicode box-drawing art (very
   common in real FILE_ID.DIZ banners) were passed straight to
   session.write()'s PETSCII encoder, which falls back to a literal '?'
   PER CHARACTER with no representation -- a wall of question marks
   ("omg file areas look awful"). Now stripped/collapsed before display.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, user, responses):
        self.user = user
        self._responses = list(responses)
        self.written = []
        self._forced_width = 40

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    async def clear_screen(self):
        self.written.append('[CLR]')

    @property
    def petscii_width(self):
        return self._forced_width

    def transcript(self):
        return ''.join(self.written)


class StripForPetsciiUnitTests(unittest.TestCase):
    def test_box_drawing_art_collapses_to_spaces_not_question_marks(self):
        from anetbbs.features.petscii_ui import _strip_for_petscii
        raw = '═══║ A sleek, modern door game viewer. ║═══'
        result = _strip_for_petscii(raw)
        self.assertNotIn('?', result)
        self.assertNotIn('═', result)
        self.assertIn('A sleek, modern door game viewer.', result)

    def test_plain_ascii_text_is_unaffected(self):
        from anetbbs.features.petscii_ui import _strip_for_petscii
        self.assertEqual(_strip_for_petscii('Hello, World! 123'), 'Hello, World! 123')

    def test_wrap_body_uses_the_stripped_text(self):
        from anetbbs.features.petscii_ui import _wrap_body
        raw = '═════════ MUST HAVE SCROLLER INSTALLED ═════════'
        lines = _wrap_body(raw, 40)
        joined = ' '.join(lines)
        self.assertNotIn('?', joined)
        self.assertIn('MUST HAVE SCROLLER INSTALLED', joined)


class NetworkFirstEchomailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_netfirst_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, EchoArea
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100)
            db.session.add(alice)
            db.session.commit()
            cls.alice_id = alice.id

            # Real reported shape: several distinct networks, each with
            # its own areas.
            net_a = EchomailNetwork(name='Fidonet', network_type='binkp', is_active=True)
            net_b = EchomailNetwork(name='DOVE-Net', network_type='qwk', is_active=True)
            db.session.add_all([net_a, net_b])
            db.session.flush()
            for n in range(3):
                db.session.add(EchoArea(
                    network_id=net_a.id, tag=f'FIDO{n}', name=f'Fidonet Area {n}',
                    is_active=True, is_subscribed=True, is_sysop_only=False,
                    min_access_level=10))
            db.session.add(EchoArea(
                network_id=net_b.id, tag='DOVE0', name='Ads',
                is_active=True, is_subscribed=True, is_sysop_only=False,
                min_access_level=10))
            db.session.commit()
            cls.net_a_id, cls.net_b_id = net_a.id, net_b.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _alice(self):
        return {'id': self.alice_id, 'username': 'alice', 'access_level': 100}

    def test_top_level_shows_networks_not_areas(self):
        from anetbbs.features.petscii_ui import _echomail_menu
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_echomail_menu(session))
        txt = session.transcript()
        self.assertIn('Fidonet', txt)
        self.assertIn('DOVE-Net', txt)
        self.assertNotIn('Fidonet Area', txt,
                         'top level must show network names, not area names')
        self.assertNotIn('Ads', txt)

    def test_picking_a_network_shows_only_its_own_areas(self):
        from anetbbs.features.petscii_ui import _echomail_menu
        # Network list is sorted by name -- DOVE-Net < Fidonet.
        session = _FakeSession(self._alice(), ['1', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_echomail_menu(session))
        txt = session.transcript()
        self.assertIn('Ads', txt, 'DOVE-Net area must appear after picking DOVE-Net')
        self.assertNotIn('Fidonet Area', txt,
                         "Fidonet's areas must not leak into DOVE-Net's listing")

    def test_different_network_shows_only_that_networks_areas(self):
        from anetbbs.features.petscii_ui import _echomail_menu
        session = _FakeSession(self._alice(), ['2', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_echomail_menu(session))
        txt = session.transcript()
        self.assertIn('Fidonet Area 0', txt)
        self.assertNotIn('Ads', txt,
                         "DOVE-Net's area must not leak into Fidonet's listing")


if __name__ == '__main__':
    unittest.main()
