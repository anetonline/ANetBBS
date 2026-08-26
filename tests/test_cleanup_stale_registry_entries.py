"""Regression tests for the new cleanup_stale_registry_entries scheduled
handler (anetbbs/events/handlers.py) -- the MEDIUM fix for unbounded
RegistryEntry growth via unauthenticated registration churn
(POST /registry/api/v1/register requires no auth by design; the only
brake was a per-source-IP rate cap, with no ceiling on total rows and
no cleanup anywhere).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CleanupStaleRegistryEntriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.cleanup_registry_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            from anetbbs.models import db
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _add_entry(self, host, is_verified, days_old):
        from anetbbs.models import db, RegistryEntry
        entry = RegistryEntry(
            host=host, name='Test', contact_email='x@example.com',
            is_verified=is_verified,
            registered_at=datetime.utcnow() - timedelta(days=days_old),
            last_heartbeat_at=datetime.utcnow() - timedelta(days=days_old),
        )
        db.session.add(entry)
        db.session.commit()

    def test_noop_on_non_hub_install(self):
        from anetbbs.events.handlers import cleanup_stale_registry_entries
        self.app.config['REGISTRY_MODE_ENABLED'] = False
        with self.app.app_context():
            self._add_entry('old-unverified.example.com', False, 10)
            ok, msg = cleanup_stale_registry_entries(self.app, {})
            self.assertTrue(ok)
            self.assertIn('not a federation hub', msg)
            from anetbbs.models import RegistryEntry
            self.assertIsNotNone(
                RegistryEntry.query.filter_by(
                    host='old-unverified.example.com').first(),
                'non-hub install must not touch RegistryEntry rows at all')

    def test_deletes_old_unverified_rows_on_hub_install(self):
        from anetbbs.events.handlers import cleanup_stale_registry_entries
        self.app.config['REGISTRY_MODE_ENABLED'] = True
        with self.app.app_context():
            self._add_entry('stale1.example.com', False, 10)
            self._add_entry('stale2.example.com', False, 30)
            ok, msg = cleanup_stale_registry_entries(
                self.app, {'stale_days': 3})
            self.assertTrue(ok)
            from anetbbs.models import RegistryEntry
            self.assertIsNone(RegistryEntry.query.filter_by(
                host='stale1.example.com').first())
            self.assertIsNone(RegistryEntry.query.filter_by(
                host='stale2.example.com').first())

    def test_keeps_recent_unverified_rows(self):
        from anetbbs.events.handlers import cleanup_stale_registry_entries
        self.app.config['REGISTRY_MODE_ENABLED'] = True
        with self.app.app_context():
            self._add_entry('fresh-unverified.example.com', False, 1)
            cleanup_stale_registry_entries(self.app, {'stale_days': 3})
            from anetbbs.models import RegistryEntry
            self.assertIsNotNone(RegistryEntry.query.filter_by(
                host='fresh-unverified.example.com').first())

    def test_never_deletes_verified_rows_regardless_of_age(self):
        from anetbbs.events.handlers import cleanup_stale_registry_entries
        self.app.config['REGISTRY_MODE_ENABLED'] = True
        with self.app.app_context():
            self._add_entry('old-but-verified.example.com', True, 365)
            cleanup_stale_registry_entries(self.app, {'stale_days': 3})
            from anetbbs.models import RegistryEntry
            self.assertIsNotNone(RegistryEntry.query.filter_by(
                host='old-but-verified.example.com').first())


if __name__ == '__main__':
    unittest.main()
