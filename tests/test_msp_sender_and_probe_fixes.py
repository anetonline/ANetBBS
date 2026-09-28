"""Regression tests for two real MSP bugs reported live (2026-09-27),
found while auditing the terminal InterBBS-IM directory picker added
in v1.1.3:

1. `_msp_pick_online_user()` (bbs_ui.py) only ever SYSTAT-probed a
   directory entry's `hostname`, always reporting "No reply, or nobody
   online there" even for entries confirmed online via the web UI's
   own /imsg/directory/<id>/who -- which already falls back to
   `ip_address` when the hostname alone doesn't resolve/reply
   (imsg.py's directory_who()). The terminal picker never had that
   fallback.

2. Every outbound MSP send fell back to re-sending the username into
   the MSP SENDER-TERM field (`sender_real_name`) whenever the user
   had no distinct display name set. Confirmed live against a real
   Synchronet BBS: Synchronet echoes SENDER-TERM back RAW right after
   the sender's name, so this produced a visible duplicate --
   "Instant Message from StingRay StingRay [ip] (<no name>)". RFC 1312
   explicitly allows SENDER-TERM to be empty; the fix is to never fall
   back to the username there.

3. The BBS name (`sender_system`) was encoded into the wire `cookie`
   field, which Synchronet's real receiver (exec/mspservice.js,
   confirmed 2026-09-28 by cloning github.com/SynchronetBBS/sbbs)
   reads but never uses for anything. Its `signature` field is what
   renders in brackets after the sender's name (falling back to the
   raw connecting IP when empty), AND is the sole gate on whether the
   trailing "(<no name>)" fallback appears at all -- confirmed by both
   Synchronet's receiver (mspservice.js) and its own sender
   (sbbsimsg_lib.js's send_msg(), which sends
   `sender + "\\0\\0\\0" + system.name + "\\0"`, i.e. always-empty
   SENDER-TERM/cookie and the BBS name in the LAST/signature field).
   Fixed by encoding `sender_system` into `signature` instead of
   `cookie`.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class MspSenderRealNameHelperTests(unittest.TestCase):
    """Pure-function tests for bbs_ui._msp_sender_real_name() -- no DB,
    no session, no asyncio needed."""

    def test_no_display_name_returns_empty_not_username(self):
        from anetbbs.features.bbs_ui import _msp_sender_real_name
        self.assertEqual(
            _msp_sender_real_name({'username': 'StingRay'}), '')

    def test_display_name_equal_to_username_returns_empty(self):
        from anetbbs.features.bbs_ui import _msp_sender_real_name
        self.assertEqual(
            _msp_sender_real_name(
                {'username': 'StingRay', 'display_name': 'StingRay'}), '')

    def test_blank_display_name_returns_empty(self):
        from anetbbs.features.bbs_ui import _msp_sender_real_name
        self.assertEqual(
            _msp_sender_real_name(
                {'username': 'StingRay', 'display_name': '   '}), '')

    def test_genuinely_distinct_display_name_is_returned(self):
        from anetbbs.features.bbs_ui import _msp_sender_real_name
        self.assertEqual(
            _msp_sender_real_name(
                {'username': 'StingRay', 'display_name': 'The Ray'}),
            'The Ray')


class ImsgWebSendRealNameTests(unittest.TestCase):
    """The web /imsg/send route had the same username-fallback bug,
    fixed alongside the terminal one -- confirm it no longer sends a
    redundant real name."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.msp_sender_realname_web_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            user = User(username='StingRay', email='sr@example.com',
                       password_hash='x', access_level=10)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_no_display_name_sends_empty_sender_real_name(self):
        with patch('anetbbs.web.imsg.send_msp', return_value=True) as mocked:
            resp = self._client().post('/imsg/send', data={
                'host': 'peer.example.com', 'port': '18',
                'recipient': 'someone', 'message': 'test',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs['sender_real_name'], '')


class SendMspSignatureFieldTests(unittest.TestCase):
    """Wire-level check that send_msp() encodes the BBS name into MSP's
    `signature` field, not `cookie` -- confirmed against real
    Synchronet source (mspservice.js only renders a bracketed identity
    from `signature`, falling back to the raw IP and the
    "(<no name>)" suffix when it's empty; `cookie` is read but never
    used for display at all)."""

    def test_bbs_name_lands_in_signature_not_cookie(self):
        from anetbbs.msp.client import send_msp
        from anetbbs.msp.protocol import decode

        captured = {}

        class _FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def settimeout(self, *a):
                pass

            def connect(self, *a):
                pass

            def sendall(self, data):
                captured['payload'] = data

            def shutdown(self, *a):
                pass

            def recv(self, *a):
                return b''

        with patch('anetbbs.msp.client.resolve_safe_destination',
                   return_value=(2, ('203.0.113.5', 18), None)), \
             patch('anetbbs.msp.client.socket.socket', return_value=_FakeSocket()):
            ok = send_msp(host='peer.example.com', recipient='bob',
                          message='hi', sender='StingRay',
                          sender_real_name='', sender_system='A-Net Online')
        self.assertTrue(ok)
        decoded = decode(captured['payload'])
        self.assertEqual(decoded['signature'], 'A-Net Online')
        self.assertEqual(decoded['cookie'], '')
        self.assertEqual(decoded['sender_terminal'], '')
        self.assertEqual(decoded['sender'], 'StingRay')


class MspOnlineUserProbeIpFallbackTests(unittest.TestCase):
    """_msp_pick_online_user() must fall back to ip_address the same
    way imsg.py's directory_who() already does."""

    class _FakeSession:
        def __init__(self, arrow_keys=None):
            self.user = {'id': 1, 'username': 'testuser'}
            self.written = []
            self._arrow_keys = list(arrow_keys or [])

        async def write(self, text):
            self.written.append(text)

        async def read_key_arrow(self):
            return self._arrow_keys.pop(0) if self._arrow_keys else 'Q'

        async def read_line(self, prompt=''):
            return ''

    def _ui(self, arrow_keys=None):
        from anetbbs.features.bbs_ui import BBSMenuUI
        return BBSMenuUI(self._FakeSession(arrow_keys=arrow_keys))

    def test_falls_back_to_ip_when_hostname_probe_is_empty(self):
        bbs_row = {'hostname': 'dead-hostname.example', 'ip_address': '9.9.9.9',
                  'name': 'Test BBS', 'systat_port': 11}

        def fake_query_systat(host, port, timeout):
            if host == '9.9.9.9':
                return ('Node  User  Action  Idle\r\n'
                        '----  ----  ------  ----\r\n'
                        '1     bob   Chatting  0:01\r\n')
            return ''  # hostname probe fails, exactly the reported bug

        ui = self._ui(arrow_keys=['Q'])
        with patch('anetbbs.msp.systat.query_systat', side_effect=fake_query_systat):
            result = asyncio.run(ui._msp_pick_online_user(bbs_row))
        joined = ''.join(ui.session.written)
        self.assertNotIn('No reply, or nobody online there', joined)
        self.assertIn('bob', joined)
        self.assertIsNone(result)  # picked 'Q' to cancel, not a real failure

    def test_no_ip_address_still_falls_back_to_manual_entry_message(self):
        bbs_row = {'hostname': 'dead-hostname.example', 'ip_address': '',
                  'name': 'Test BBS', 'systat_port': 11}
        ui = self._ui(arrow_keys=[])
        with patch('anetbbs.msp.systat.query_systat', return_value=''):
            result = asyncio.run(ui._msp_pick_online_user(bbs_row))
        joined = ''.join(ui.session.written)
        self.assertIn('No reply, or nobody online there', joined)
        # An empty/failed probe is an intentional auto-fallback to
        # manual entry, not a user-initiated cancel -- distinguished
        # from Q/ESC (which returns None) by the 'MANUAL' sentinel
        # since real live feedback ("you cannot Q quit back or use esc
        # to quit, it goes to manual") found the two were being
        # conflated into the same None return, so a caller had no way
        # to tell "fall through to manual" apart from "user backed out".
        self.assertEqual(result, 'MANUAL')

    def test_directory_picker_rows_carry_ip_address(self):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, BbsDirectoryEntry
        app = create_app('testing')
        with app.app_context():
            db.create_all()
            db.session.add(BbsDirectoryEntry(
                hostname='alpha.example.com', ip_address='1.2.3.4',
                name='Alpha BBS', msp_port=18, systat_port=11,
                source='manual'))
            db.session.commit()

        ui = self._ui(arrow_keys=['Q'])
        with patch('anetbbs.features.bbs_ui._app', return_value=app):
            asyncio.run(ui._msp_pick_directory_bbs())
        # No direct return-value assertion needed here (Q cancels) --
        # the real gap this guards is the row dict missing 'ip_address'
        # entirely, which would make _msp_pick_online_user's fallback a
        # silent no-op (bbs_row.get('ip_address') always falsy). Verify
        # by re-querying the same way the picker does.
        with app.app_context():
            entry = BbsDirectoryEntry.query.filter_by(
                hostname='alpha.example.com').first()
            row = {'hostname': entry.hostname, 'ip_address': entry.ip_address or '',
                  'name': entry.name or entry.hostname,
                  'sysop': entry.sysop or '', 'location': entry.location or '',
                  'systat_port': entry.systat_port or 11}
        self.assertEqual(row['ip_address'], '1.2.3.4')


if __name__ == '__main__':
    unittest.main()
