"""Regression tests for the new "Security Questions" / "Password
Recovery Settings" cfg (anetbbs-cfg TUI) screens under Users &
Security, added 2026-09-25 alongside the matching web admin page --
see tests/test_security_questions_admin_toggle.py for that side.

Only the pure-Python DB helper functions in
anetbbs/cfg/sections/users.py are exercised here (list/create/update/
delete + the settings singleton), not the curses UI glue (ui.run_list/
ui.run_form) -- same scope the rest of this test suite uses for cfg
sections (no existing test file drives the curses layer itself).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CfgSecurityQuestionsSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.cfg_security_questions_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def test_list_reflects_the_web_seeded_defaults(self):
        from anetbbs.cfg.sections.users import list_security_questions
        with self.app.app_context():
            rows = list_security_questions()
            self.assertEqual(len(rows), 10)
            self.assertTrue(all(r.is_active for r in rows))

    def test_create_edit_delete_round_trip(self):
        from anetbbs.cfg.sections.users import (
            create_security_question, values_from_security_question,
            update_security_question, delete_security_question,
            list_security_questions)
        with self.app.app_context():
            before = len(list_security_questions())
            q = create_security_question(
                {'text': 'What was your first username?',
                 'sort_order': 5, 'is_active': True})
            self.assertEqual(len(list_security_questions()), before + 1)

            values = values_from_security_question(q)
            self.assertEqual(values['text'], 'What was your first username?')

            update_security_question(q, {'text': 'What was your very first username?',
                                         'sort_order': 5, 'is_active': False})
            self.assertEqual(q.text, 'What was your very first username?')
            self.assertFalse(q.is_active)

            delete_security_question(q)
            self.assertEqual(len(list_security_questions()), before)

    def test_password_recovery_settings_singleton_updates(self):
        from anetbbs.models import PasswordRecoverySettings, db
        with self.app.app_context():
            settings = PasswordRecoverySettings.get()
            settings.security_questions_enabled = False
            db.session.commit()

            # A fresh .get() call must see the same row, not a new one.
            again = PasswordRecoverySettings.get()
            self.assertEqual(again.id, settings.id)
            self.assertFalse(again.security_questions_enabled)

            again.security_questions_enabled = True
            db.session.commit()
            self.assertTrue(PasswordRecoverySettings.get().security_questions_enabled)


if __name__ == '__main__':
    unittest.main()
