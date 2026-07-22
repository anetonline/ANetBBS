"""Regression tests for sysop-built custom PETSCII menus (Jerry:
"work on the custom menus for sysops... like regular ansi modes").

Deliberately a SEPARATE menu tree from the ANSI custom-menu system
(BbsMenu/BbsMenuItem) -- see models.PetsciiMenu's docstring for why
(most ANSI actions have no PETSCII equivalent, and a sysop building a
PETSCII menu wants full layout control rather than an ANSI tree with
items silently missing). Covers:
  - run_petscii_menu() falls back to the hardcoded Phase 1 menu when no
    PetsciiMenu has is_default=True (opt-in, not a replacement).
  - _run_custom_petscii_menu()'s interpreter: rendering, hotkey
    dispatch, goto, action-type -> handler dispatch, access-level
    filtering, and fail-safe fallback on a broken goto target.
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


class PetsciiCustomMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_custom_menu_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100)
            db.session.add(alice)
            bob = User(username='bob', email='bob@example.com',
                      password_hash='x', access_level=10)
            db.session.add(bob)
            db.session.commit()
            cls.alice_id = alice.id
            cls.bob_id = bob.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def tearDown(self):
        # Each test manages its own PetsciiMenu rows -- clean the table
        # between tests so is_default/name collisions don't leak across.
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu
            PetsciiMenu.query.delete()
            db.session.commit()

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _alice(self):
        return {'id': self.alice_id, 'username': 'alice', 'access_level': 100}

    def _bob(self):
        return {'id': self.bob_id, 'username': 'bob', 'access_level': 10}

    # ------------------------------------------------------------------
    # Fallback behavior
    # ------------------------------------------------------------------

    def test_no_default_custom_menu_falls_back_to_hardcoded_menu(self):
        from anetbbs.features.petscii_ui import run_petscii_menu
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(run_petscii_menu(session))
        self.assertIn('ANetBBS Main Menu', session.transcript())

    def test_default_custom_menu_is_used_instead_of_hardcoded(self):
        from anetbbs.features.petscii_ui import run_petscii_menu
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            m = PetsciiMenu(name='main', title='My Custom Menu',
                            prompt='Pick: ', is_default=True)
            db.session.add(m); db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='Q', label='Logoff', action_type='logoff'))
            db.session.commit()

        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(run_petscii_menu(session))
        txt = session.transcript()
        self.assertIn('My Custom Menu', txt)
        self.assertNotIn('ANetBBS Main Menu', txt)

    # ------------------------------------------------------------------
    # Interpreter mechanics
    # ------------------------------------------------------------------

    def test_hotkey_dispatches_to_boards_and_back_reaches_prompt_again(self):
        from anetbbs.features.petscii_ui import _run_custom_petscii_menu
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            m = PetsciiMenu(name='main', title='Main', is_default=True)
            db.session.add(m); db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='B', label='Boards', action_type='boards',
                sort_order=1))
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='Q', label='Logoff', action_type='logoff',
                sort_order=2))
            db.session.commit()

        # B -> boards menu (access_level 100 so bob-below-min wouldn't see
        # any boards, but that's a separate concern) -> Q (leave boards,
        # which has no boards seeded so just exits immediately) -> Q (logoff)
        session = _FakeSession(self._alice(), ['B', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_run_custom_petscii_menu(session, 'main'))
        txt = session.transcript()
        self.assertIn('Boards', txt)
        self.assertIn('Goodbye!', txt)

    def test_goto_jumps_to_another_menu(self):
        from anetbbs.features.petscii_ui import _run_custom_petscii_menu
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            main = PetsciiMenu(name='main', title='Main Menu')
            sub = PetsciiMenu(name='sub', title='Sub Menu')
            db.session.add_all([main, sub]); db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=main.id, hotkey='S', label='Go to sub',
                action_type='goto', action_args='sub'))
            db.session.add(PetsciiMenuItem(
                menu_id=sub.id, hotkey='Q', label='Logoff', action_type='logoff'))
            db.session.commit()

        session = _FakeSession(self._alice(), ['S', 'Q'])
        with self._patched_app():
            asyncio.run(_run_custom_petscii_menu(session, 'main'))
        txt = session.transcript()
        self.assertIn('Main Menu', txt)
        self.assertIn('Sub Menu', txt)

    def test_action_with_no_petscii_handler_shows_unavailable_not_crash(self):
        """A stray action_type the interpreter doesn't know (shouldn't
        happen via the admin UI's restricted dropdown, but the runtime
        must not trust that) must degrade gracefully."""
        from anetbbs.features.petscii_ui import _run_custom_petscii_menu
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            m = PetsciiMenu(name='main', title='Main', is_default=True)
            db.session.add(m); db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='C', label='Chat', action_type='chat'))
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='Q', label='Logoff', action_type='logoff'))
            db.session.commit()

        session = _FakeSession(self._alice(), ['C', '', 'Q'])
        with self._patched_app():
            asyncio.run(_run_custom_petscii_menu(session, 'main'))
        self.assertIn('not available', session.transcript())

    def test_item_below_min_access_is_hidden_from_the_listing(self):
        from anetbbs.features.petscii_ui import _run_custom_petscii_menu
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            m = PetsciiMenu(name='main', title='Main', is_default=True)
            db.session.add(m); db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='S', label='Sysop Only', action_type='logoff',
                min_access=100))
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='Q', label='Logoff', action_type='logoff'))
            db.session.commit()

        session = _FakeSession(self._bob(), ['Q'])  # bob is access_level 10
        with self._patched_app():
            asyncio.run(_run_custom_petscii_menu(session, 'main'))
        self.assertNotIn('Sysop Only', session.transcript())

    def test_invisible_item_is_never_shown(self):
        from anetbbs.features.petscii_ui import _run_custom_petscii_menu
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            m = PetsciiMenu(name='main', title='Main', is_default=True)
            db.session.add(m); db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='H', label='Hidden Item',
                action_type='logoff', is_visible=False))
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='Q', label='Logoff', action_type='logoff'))
            db.session.commit()

        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_run_custom_petscii_menu(session, 'main'))
        self.assertNotIn('Hidden Item', session.transcript())

    def test_broken_goto_target_fails_safe_into_hardcoded_menu(self):
        from anetbbs.features.petscii_ui import _run_custom_petscii_menu
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            m = PetsciiMenu(name='main', title='Main', is_default=True)
            db.session.add(m); db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='X', label='Broken link',
                action_type='goto', action_args='does_not_exist'))
            db.session.commit()

        session = _FakeSession(self._alice(), ['X', 'Q'])
        with self._patched_app():
            asyncio.run(_run_custom_petscii_menu(session, 'main'))
        self.assertIn('ANetBBS Main Menu', session.transcript(),
                      'a broken goto target must fail safe into the '
                      'always-working hardcoded menu, not dead-end the session')

    def test_games_action_reaches_number_guessing(self):
        from anetbbs.features.petscii_ui import _run_custom_petscii_menu
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            m = PetsciiMenu(name='main', title='Main', is_default=True)
            db.session.add(m); db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='G', label='Games', action_type='games'))
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='Q', label='Logoff', action_type='logoff'))
            db.session.commit()

        # G(games) -> Q(leave games menu) -> Q(logoff)
        session = _FakeSession(self._alice(), ['G', 'Q', 'Q'])
        with self._patched_app():
            asyncio.run(_run_custom_petscii_menu(session, 'main'))
        self.assertIn('Number Guessing', session.transcript())


if __name__ == '__main__':
    unittest.main()
