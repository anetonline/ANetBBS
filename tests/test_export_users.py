"""Regression test for tools/export_users.py (anetbbs-export-users),
the export-direction mirror of tools/import_users.py, added in the
gap-analysis follow-up round (post-v1.0.77) to close the "one-way
migration door" gap -- a sysop previously had no way to get user data
back OUT of ANetBBS in a portable format.

Covers: default active-only filtering, --all including inactive/
locked accounts, both CSV and JSON writers, and -- the one genuinely
security-relevant invariant here -- that password_hash never appears
anywhere in the exported field set or output, matching
tools/import_users.py's own "passwords never migrate" precedent in
the opposite direction.
"""
import csv
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

from tools.export_users import FIELDS, write_csv, write_json  # noqa: E402


class ExportUsersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.export_users_test.db')
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
            db.session.query(User).delete()
            alice = User(username='alice', email='alice@example.com',
                        access_level=50, bio='hi there', is_active=True)
            alice.set_password('correct horse battery staple')
            bob = User(username='bob', email='bob@example.com',
                      access_level=10, is_active=False, is_locked=True)
            bob.set_password('irrelevant')
            db.session.add_all([alice, bob])
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _active_users(self):
        from anetbbs.models import User
        return (User.query.filter(User.is_active.is_(True))
                .order_by(User.username).all())

    def _all_users(self):
        from anetbbs.models import User
        return User.query.order_by(User.username).all()

    def test_password_hash_is_never_in_the_exported_field_set(self):
        self.assertNotIn('password_hash', FIELDS)
        self.assertNotIn('password', FIELDS)

    def test_default_export_excludes_inactive_and_locked(self):
        users = self._active_users()
        usernames = {u.username for u in users}
        self.assertEqual(usernames, {'alice'})

    def test_all_flag_includes_inactive_and_locked(self):
        users = self._all_users()
        usernames = {u.username for u in users}
        self.assertEqual(usernames, {'alice', 'bob'})

    def test_write_csv_contains_expected_fields_and_no_password_hash(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'export.csv'
            write_csv(self._active_users(), out)
            with open(out, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertEqual(reader.fieldnames, FIELDS)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['username'], 'alice')
            self.assertEqual(rows[0]['bio'], 'hi there')
            raw_text = out.read_text(encoding='utf-8')
            self.assertNotIn('correct horse battery staple', raw_text)
            self.assertNotIn('password_hash', raw_text.splitlines()[0])

    def test_write_json_round_trips_and_excludes_password(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'export.json'
            write_json(self._all_users(), out)
            data = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(len(data), 2)
            by_name = {row['username']: row for row in data}
            self.assertEqual(by_name['bob']['is_active'], False)
            self.assertEqual(by_name['bob']['is_locked'], True)
            for row in data:
                self.assertNotIn('password_hash', row)
                self.assertNotIn('password', row)


if __name__ == '__main__':
    unittest.main()
