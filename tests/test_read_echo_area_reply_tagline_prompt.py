"""Regression test: replying to (or starting a new message from) an
echomail area via read_echo_area()'s R/N flow never offered a tagline
at all -- reported live ("I still dont see an add tagline option in
terminal when sending an echomail"). Root cause: _maybe_prompt_tagline()
was wired into _post_compose/_send_pm/_compose_echomail, but
read_echo_area() has its OWN separate reply/new-message code path that
calls launch_anedit() directly (reached by reading an area's message
list, then pressing R or N inside the ANView reader) -- a fourth
compose call site that was missed the first time.

Also covers the later correction: the tagline picker is passed as a
`tagline_picker` callback (invoked by ANEdit itself at send time), not
a pre-fetched `available_tagline` value -- see test_anedit_tagline_toggle.py
for why.
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


class ReadEchoAreaReplyTaglinePromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.read_echo_area_tagline_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, EchomailNetwork, EchoArea,
                                    EchomailMessage, Tagline)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            net = EchomailNetwork(name='ReplyTaglineTestNet', network_type='binkp',
                                 our_address='1:1/1', is_active=True)
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='TEST.AREA', name='Test Area',
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()
            cls.area_id = area.id

            msg = EchomailMessage(area_id=area.id, network_id=net.id,
                                  from_name='Someone', to_name='All',
                                  subject='Existing message', body='body text',
                                  direction='inbound')
            db.session.add(msg)
            db.session.commit()

            Tagline.query.delete()
            db.session.add(Tagline(text='THE_ONLY_TAGLINE', is_active=True))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_reply_from_read_echo_area_prompts_for_a_tagline(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        # First ENTER picks the (only) message in the area's message-list
        # lightbar; second ENTER picks the (only) tagline in
        # _maybe_prompt_tagline()'s own separate lightbar.
        session = FakeSession(keys=['ENTER', 'ENTER'])
        ui = BBSMenuUI(session)

        captured = {}

        async def _fake_launch_aneview(*args, **kwargs):
            return 'reply'

        async def _fake_launch_anedit(*args, **kwargs):
            # Mirrors what the real ANEdit.run() does: call the picker
            # callback once, at send time -- NOT a pre-fetched value.
            picker = kwargs.get('tagline_picker')
            captured['picker_given'] = picker is not None
            captured['picked'] = await picker() if picker else None
            return None  # abort -- avoids needing to drive a real save

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_aneview', _fake_launch_aneview), \
             patch('anetbbs.features.anedit.launch_anedit', _fake_launch_anedit):
            asyncio.run(ui.read_echo_area(self.area_id, 'Test Area'))

        self.assertTrue(captured.get('picker_given'),
                        'launch_anedit must be given a tagline_picker callback '
                        '(reply flow reached)')
        self.assertEqual(captured.get('picked'), 'THE_ONLY_TAGLINE',
                         '_maybe_prompt_tagline() must be consulted before '
                         'replying from inside read_echo_area(), not just '
                         'from the dedicated Compose Echomail menu item')


if __name__ == '__main__':
    unittest.main()
