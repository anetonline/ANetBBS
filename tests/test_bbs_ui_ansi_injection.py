"""Regression tests for an ANSI-escape-injection vulnerability found in
anetbbs/features/bbs_ui.py during a security/performance audit --  the
same bug class already found and fixed in wall.py/mrc_chat.py
elsewhere in this codebase: a genuinely remote-controlled metadata
field gets rendered straight into a lightbar row or a detail-view
header line with no ANSI/control-byte stripping, letting a malicious
or compromised peer repaint the local caller's own terminal.

Two call sites covered here:

  1. BBSMenuUI.read_echo_area()'s lightbar row renderer embeds an
     EchomailMessage's `subject`/`from_name` directly -- both are
     forgeable by ANY FidoNet peer (anetbbs/echomail/poller.py's
     _import_message() stores them verbatim off the wire).

  2. BBSMenuUI.list_imsg_inbox() embeds an InstantMessage's
     `sender_label` (and, in the preview, `body`) directly -- both are
     raw, unauthenticated text from a remote MSP peer
     (anetbbs/msp/server.py's _deliver(), which has its own comment
     flagging these exact fields as untrusted).

Fixed by stripping well-formed CSI/OSC escape sequences (and the C0
control range as a safety net) at render time, via a local
_strip_untrusted() matching wall.py/mrc_chat.py's own implementation.
Deliberately NOT applied to full message bodies read through the
ANSI-aware launch_aneview() pager (echomail/PM bodies are expected,
decades-old BBS precedent to carry real ANSI art) -- only to
structural list/header fields, where a raw escape is never expected.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

_EVIL_ESC = '\x1b[2J\x1b[1;1HPWNED'


class _FakeSession:
    """Matches the shape tests/test_bbs_ui_file_quota.py's _FakeSession
    uses, plus read_key_arrow() for the lightbar screens this file's
    tests exercise (_rss_lightbar)."""

    def __init__(self, user, responses=None, keys=None):
        self.user = user
        self.window_size = (80, 24)
        self._responses = list(responses or [])
        self._keys = list(keys or [])
        self.written = []

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

    async def read_key_arrow(self):
        if not self._keys:
            raise AssertionError(
                '_FakeSession.read_key_arrow() called with an empty '
                'scripted key queue')
        return self._keys.pop(0)

    def transcript(self):
        return ''.join(self.written)


class BbsUiAnsiInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.bbs_ui_ansi_injection_test.db')
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

    def test_echomail_lightbar_row_strips_forged_from_name_and_subject(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage
        from anetbbs.features.bbs_ui import BBSMenuUI

        with self.app.app_context():
            net = EchomailNetwork(name='InjectTestNet', network_type='binkp',
                                  our_address='1:1/1')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='INJECT.TEST', name='Inject Test',
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id,
                from_name=f'Evil{_EVIL_ESC}Peer',
                to_name='All',
                subject=f'Hi{_EVIL_ESC}There',
                body='hello',
                direction='inbound',
            )
            db.session.add(msg)
            db.session.commit()
            area_id = area.id

        user = {'id': 1, 'username': 'victim', 'access_level': 10}
        # First read_key_arrow() drives the initial lightbar draw (implicit
        # via _draw_full() inside _rss_lightbar -- no key needed for that),
        # then 'Q' quits immediately without opening the message.
        session = _FakeSession(user, keys=['Q'])
        ui = BBSMenuUI(session)

        with self._patched_app():
            asyncio.run(ui.read_echo_area(area_id, 'INJECT.TEST'))

        transcript = session.transcript()
        self.assertNotIn('\x1b[2J\x1b[1;1HPWNED', transcript,
                          'raw injected escape sequence must not reach the terminal')
        self.assertNotIn(_EVIL_ESC, transcript)
        # The harmless text around the injection should still show up
        # (proves this is sanitization, not silent data loss).
        self.assertIn('Evil', transcript)
        self.assertIn('Peer', transcript)

    def test_imsg_inbox_list_and_detail_strip_forged_sender_label(self):
        from anetbbs.models import db, InstantMessage, User
        from anetbbs.features.bbs_ui import BBSMenuUI

        with self.app.app_context():
            u = User(username='imsg_victim', email='imsg_victim@example.com',
                     password_hash='x', is_admin=False, access_level=10)
            db.session.add(u)
            db.session.commit()
            im = InstantMessage(
                recipient_id=u.id,
                sender_label=f'Mallory{_EVIL_ESC}Sender',
                sender_host='203.0.113.5',
                body=f'gotcha{_EVIL_ESC}here',
                is_read=False,
                origin='msp',
            )
            db.session.add(im)
            db.session.commit()
            user_id = u.id

        user_dict = {'id': user_id, 'username': 'imsg_victim', 'access_level': 10}
        # List view shows the injected sender in the row; 'Q' quits before
        # opening the message (proves the LIST render itself is sanitized,
        # not just the detail view).
        session = _FakeSession(user_dict, responses=['Q'])
        ui = BBSMenuUI(session)

        with self._patched_app():
            asyncio.run(ui.list_imsg_inbox())

        transcript = session.transcript()
        self.assertNotIn(_EVIL_ESC, transcript)
        self.assertIn('Mallory', transcript)
        self.assertIn('Sender', transcript)

        # Now open the message (detail view renders "From: ..." plus the
        # body). '1' picks the message, '' answers _page_lines()'s
        # "Press Enter to continue" prompt for the (short, one-line) body,
        # then 'Q' quits the outer inbox loop.
        session2 = _FakeSession(user_dict, responses=['1', '', 'Q'])
        ui2 = BBSMenuUI(session2)
        with self._patched_app():
            asyncio.run(ui2.list_imsg_inbox())

        transcript2 = session2.transcript()
        self.assertNotIn(_EVIL_ESC, transcript2)
        self.assertIn('gotcha', transcript2)
        self.assertIn('here', transcript2)


if __name__ == '__main__':
    unittest.main()
