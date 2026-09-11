"""Regression test for a real finding from a security/performance
audit: web/admin.py's edit_user()/delete_user()/toggle_ban() all
refuse to let an admin demote, deactivate, or delete their OWN
account (checking user.id == current_user.id), with the explicit
reasoning that a stray keypress could lock the whole admin panel out
with no other admin necessarily around to undo it -- but
BBSMenuUI._sysop_edit_user() (anetbbs/features/bbs_ui.py), the
terminal counterpart reachable by any sysop over telnet/SSH, had none
of those three guards: a sysop could toggle off their own is_admin,
deactivate their own account, or permanently delete it, from their own
terminal session.

Fixed by adding the same self-check (keyed off the terminal session's
own user id) to all three actions ('A' toggle-admin, 'T' toggle-active,
'D' delete).
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

    def transcript(self):
        return ''.join(self.written)


class BbsUiSysopEditUserSelfGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.bbs_ui_edituser_selfguard_test.db')
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
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _make_admin(self, username):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username=username, email=f'{username}@example.com',
                     password_hash='x', is_admin=True, access_level=255,
                     is_active=True)
            db.session.add(u)
            db.session.commit()
            return u.id

    def test_cannot_toggle_own_admin_flag(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        admin_id = self._make_admin('selfguard_admin1')
        session = _FakeSession({'id': admin_id, 'username': 'selfguard_admin1'},
                               responses=['A', 'Q'])
        ui = BBSMenuUI(session)

        with self._patched_app():
            asyncio.run(ui._sysop_edit_user(admin_id))

        self.assertIn('cannot change your own admin status', session.transcript())
        with self.app.app_context():
            from anetbbs.models import User
            self.assertTrue(User.query.get(admin_id).is_admin,
                            'own admin flag must be unchanged')

    def test_cannot_deactivate_own_account(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        admin_id = self._make_admin('selfguard_admin2')
        session = _FakeSession({'id': admin_id, 'username': 'selfguard_admin2'},
                               responses=['T', 'Q'])
        ui = BBSMenuUI(session)

        with self._patched_app():
            asyncio.run(ui._sysop_edit_user(admin_id))

        self.assertIn('cannot deactivate your own account', session.transcript())
        with self.app.app_context():
            from anetbbs.models import User
            self.assertTrue(User.query.get(admin_id).is_active,
                            'own active flag must be unchanged')

    def test_cannot_delete_own_account(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        admin_id = self._make_admin('selfguard_admin3')
        # 'D' then (if not guarded) would prompt for DELETE confirmation --
        # scripted 'DELETE' too, to prove the guard fires BEFORE that
        # prompt even happens (no extra read_line consumed).
        session = _FakeSession({'id': admin_id, 'username': 'selfguard_admin3'},
                               responses=['D', 'Q'])
        ui = BBSMenuUI(session)

        with self._patched_app():
            asyncio.run(ui._sysop_edit_user(admin_id))

        self.assertIn('cannot delete your own account', session.transcript())
        with self.app.app_context():
            from anetbbs.models import User
            self.assertIsNotNone(User.query.get(admin_id),
                                 'own account must still exist')

    def test_can_still_edit_a_different_user(self):
        """Guard must not be so broad it blocks admin-on-someone-else
        actions -- only self-targeting is refused."""
        from anetbbs.features.bbs_ui import BBSMenuUI

        admin_id = self._make_admin('selfguard_admin4')
        other_id = self._make_admin('selfguard_other4')
        session = _FakeSession({'id': admin_id, 'username': 'selfguard_admin4'},
                               responses=['T', 'Q'])
        ui = BBSMenuUI(session)

        with self._patched_app():
            asyncio.run(ui._sysop_edit_user(other_id))

        with self.app.app_context():
            from anetbbs.models import User
            self.assertFalse(User.query.get(other_id).is_active,
                             'editing a DIFFERENT user must still work')


if __name__ == '__main__':
    unittest.main()
