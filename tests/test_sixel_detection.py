"""Tests for generalized sixel capability detection + the sixel_mode
user profile preference, built for a Firehawke feature request
("Fuller CTerm support and Display Codes") -- Sixel detection flagged
as the highest-priority piece to scope first.

Found two real, concrete gaps before writing this:
  1. Sixel auto-detection (DA1 device-attributes query) already
     existed but was dead code in practice -- the RSS reader's entry
     point unconditionally asked a manual "Does your terminal support
     sixel? [Y/N]" prompt on EVERY session, pre-populating the same
     session._sixel_ok cache flag the DA1 detector checks first via
     hasattr() -- so the DA1 logic never actually ran in production.
  2. It was also RSS-specific and per-session only, never persisted,
     so there was no way to force it on for a client that supports
     sixel but doesn't self-report via DA1 (e.g. Windows Terminal over
     SSH), or force it off for someone who doesn't want it.

Fixed by promoting the detector to a general-purpose
_detect_sixel_support() method that checks a new persisted
User.sixel_mode preference ('auto'/'forced_on'/'forced_off') first,
and replacing the RSS reader's unconditional prompt with a real call
to it.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SixelModePreferenceTests(unittest.TestCase):
    """User.sixel_mode field + profile edit form."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.sixel_detection_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_default_is_auto(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username='sixelmodedefault', email='smd@example.com')
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            self.assertEqual(u.sixel_mode, 'auto')

    def test_persists_through_profile_edit(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username='sixelmodesave', email='sms@example.com')
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            uid = u.id

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True

        resp = client.post('/profile/edit', data={
            'email': 'sms@example.com', 'sixel_mode': 'forced_off',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            u2 = User.query.get(uid)
            self.assertEqual(u2.sixel_mode, 'forced_off')


class DetectSixelSupportTests(unittest.TestCase):
    """_detect_sixel_support()'s three branches, in
    anetbbs/features/bbs_ui.py."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.sixel_detect_test.db')
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

    def _make_user(self, username, sixel_mode):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username=username, email=f'{username}@example.com',
                    sixel_mode=sixel_mode)
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            return u.id

    class _FakeSession:
        def __init__(self, uid):
            self.user = {'id': uid}

    def _ui_for(self, uid):
        from anetbbs.features.bbs_ui import BBSMenuUI
        ui = BBSMenuUI.__new__(BBSMenuUI)
        ui.session = self._FakeSession(uid)
        return ui

    def test_forced_off_never_queries_da1(self):
        import asyncio
        uid = self._make_user('detectoff', 'forced_off')
        # If DA1 were queried, session.write would be called -- give it
        # a stub that fails the test if invoked. img2sixel is mocked as
        # present specifically so that IF the forced_off branch fails
        # to short-circuit and falls through to 'auto' mode, that
        # path's own img2sixel fast-path check can't mask the bug by
        # coincidentally also returning False for an unrelated reason
        # (this sandbox has no img2sixel installed, which without this
        # mock would let a real forced_off regression slip through
        # undetected -- confirmed by deliberately breaking the
        # forced_off branch and re-running this test before fixing it).
        async def _fail_if_called(*a, **kw):
            raise AssertionError('DA1 query should never run in forced_off mode')
        with patch('shutil.which', return_value='/usr/bin/img2sixel'):
            ui = self._ui_for(uid)
            ui.session.write = _fail_if_called
            result = asyncio.run(ui._detect_sixel_support())
        self.assertFalse(result)

    def test_forced_on_skips_da1_but_checks_img2sixel(self):
        import asyncio
        uid = self._make_user('detecton', 'forced_on')

        async def _fail_if_called(*a, **kw):
            raise AssertionError('DA1 query should never run in forced_on mode')

        with patch('shutil.which', return_value='/usr/bin/img2sixel'):
            ui = self._ui_for(uid)
            ui.session.write = _fail_if_called
            result = asyncio.run(ui._detect_sixel_support())
            self.assertTrue(result)

        with patch('shutil.which', return_value=None):
            ui2 = self._ui_for(uid)
            ui2.session.write = _fail_if_called
            result2 = asyncio.run(ui2._detect_sixel_support())
            self.assertFalse(result2, 'forced_on with no img2sixel installed must be False')

    def test_auto_runs_da1_detection(self):
        """'auto' (default) mode must actually attempt DA1 detection --
        this is the real behavior fix: it used to never run because the
        RSS reader's manual prompt always set the cache flag first."""
        import asyncio
        uid = self._make_user('detectauto', 'auto')
        ui = self._ui_for(uid)

        write_calls = []
        async def _capture_write(text):
            write_calls.append(text)
        ui.session.write = _capture_write

        class _FakeReader:
            async def read(self, n):
                return b''  # immediate EOF -- detection should fail gracefully, not hang

        ui.session.reader = _FakeReader()

        with patch('shutil.which', return_value='/usr/bin/img2sixel'):
            result = asyncio.run(ui._detect_sixel_support())

        self.assertTrue(any('\x1b[0c' in c for c in write_calls),
                        'auto mode must send the DA1 query (ESC[0c)')
        self.assertFalse(result)  # no real response -> no sixel support detected

    def test_auto_xterm_style_da1_detects_sixel(self):
        """A real xterm-family reply (?-prefixed flag list, flag 4
        present) must be detected directly, no follow-up query."""
        import asyncio
        uid = self._make_user('detectxterm', 'auto')
        ui = self._ui_for(uid)

        write_calls = []
        async def _capture_write(text):
            write_calls.append(text)
        ui.session.write = _capture_write

        # xterm/mlterm/wezterm-style DA1 response: ESC[?65;1;2;4;9c
        resp_bytes = iter(b'\x1b[?65;1;2;4;9c')
        class _FakeReader:
            async def read(self, n):
                try:
                    return bytes([next(resp_bytes)])
                except StopIteration:
                    return b''
        ui.session.reader = _FakeReader()

        with patch('shutil.which', return_value='/usr/bin/img2sixel'):
            result = asyncio.run(ui._detect_sixel_support())

        self.assertEqual(write_calls, ['\x1b[0c'],
                          'non-CTerm terminals must not trigger a CTDA follow-up query')
        self.assertTrue(result)

    def test_auto_syncterm_da1_alone_is_not_enough(self):
        """SyncTerm/CTerm's real DA1 reply spells 'CTerm' in decimal
        ASCII (CSI = 67;84;101;114;109;rev c) and never carries a
        standalone sixel flag -- this was the actual bug: auto-detect
        could never succeed on SyncTerm even though it supports sixel,
        because there's no '4' token to find in that reply."""
        import asyncio
        uid = self._make_user('detectctermbare', 'auto')
        ui = self._ui_for(uid)

        async def _capture_write(text):
            pass
        ui.session.write = _capture_write

        # CTerm's real DA1 reply, no CTDA follow-up available (times out).
        resp_bytes = iter(b'\x1b[=67;84;101;114;109;1;156c')
        class _FakeReader:
            async def read(self, n):
                try:
                    return bytes([next(resp_bytes)])
                except StopIteration:
                    return b''
        ui.session.reader = _FakeReader()

        with patch('shutil.which', return_value='/usr/bin/img2sixel'):
            result = asyncio.run(ui._detect_sixel_support())

        self.assertFalse(result, 'a bare CTerm DA1 reply must not be misread as sixel support')

    def test_auto_syncterm_ctda_followup_detects_sixel(self):
        """When the primary DA1 reply identifies the client as
        SyncTerm/CTerm, the detector must follow up with CTerm's
        extended-DA query (CSI < 0 c) and read flag 4 (pixel/sixel
        graphics) from CSI < 0 ; Ps... c -- this is the actual fix."""
        import asyncio
        uid = self._make_user('detectctermfull', 'auto')
        ui = self._ui_for(uid)

        write_calls = []
        async def _capture_write(text):
            write_calls.append(text)
        ui.session.write = _capture_write

        # One continuous byte stream: the first read loop stops as soon as
        # it hits the 'c' that ends the primary DA1 reply, leaving the CTDA
        # reply's bytes still queued up for the second read loop.
        stream = iter(b'\x1b[=67;84;101;114;109;1;156c' + b'\x1b[<0;1;2;4;7c')
        async def _capture_write(text):
            write_calls.append(text)
        ui.session.write = _capture_write

        class _SeqReader:
            async def read(self, n):
                try:
                    return bytes([next(stream)])
                except StopIteration:
                    return b''
        ui.session.reader = _SeqReader()

        with patch('shutil.which', return_value='/usr/bin/img2sixel'):
            result = asyncio.run(ui._detect_sixel_support())

        self.assertEqual(write_calls, ['\x1b[0c', '\x1b[<0c'],
                          'CTerm signature in the DA1 reply must trigger the CTDA follow-up query')
        self.assertTrue(result, 'flag 4 in the CTDA reply must be detected as sixel support')


if __name__ == '__main__':
    unittest.main()
