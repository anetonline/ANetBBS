"""Regression test for a second real disk-space incident, on top of the
one v1.0b2.87 fixed: on a Pi where /tmp is a RAM-backed tmpfs (separate
from, and much smaller than, the actual disk), update.sh's disk-space
check correctly reported the real disk had 108GB free while still
refusing to proceed, because the backup itself couldn't fit in /tmp's
much smaller RAM allocation. Moved backups from /tmp to
INSTALL_DIR/data/backups/ in update.sh, deploy/run_restore.sh (the
privileged helper's security allowlist), and anetbbs/web/backups_admin.py
(the admin UI that lists/deletes/restores them) -- this covers the
Python side, confirming _backup_root() actually resolves to the new
location instead of a stale hardcoded /tmp.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class BackupRootResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.backups_admin_root_test.db')
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

    def test_backup_root_is_under_install_dir_not_hardcoded(self):
        """The bug was _BACKUP_ROOT being a hardcoded '/tmp' literal,
        independent of where the install actually lives. Using two very
        different INSTALL_DIR values and confirming _backup_root() tracks
        each one exactly (rather than a real /tmp check, which would be
        confounded by tempfile.TemporaryDirectory() itself living under
        /tmp on this system) is what actually proves that's fixed."""
        from anetbbs.web.backups_admin import _backup_root
        for install_dir in ('/opt/anetbbs', '/home/stingray/anetbbs'):
            with self.app.app_context():
                self.app.config['INSTALL_DIR'] = install_dir
                root = _backup_root()
            self.assertEqual(root, os.path.join(install_dir, 'data', 'backups'))

    def test_scan_finds_backups_under_the_new_root(self):
        from anetbbs.web.backups_admin import _scan
        with self.app.app_context(), tempfile.TemporaryDirectory() as install_dir:
            self.app.config['INSTALL_DIR'] = install_dir
            backups_dir = os.path.join(install_dir, 'data', 'backups')
            snap = os.path.join(backups_dir, 'anetbbs-backup-20260712103000')
            os.makedirs(snap)
            with open(os.path.join(snap, 'MANIFEST'), 'w') as f:
                f.write('from_version = v1.0b2.87\nto_version = v1.0b2.88\n')
            rows = _scan()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'anetbbs-backup-20260712103000')
        self.assertEqual(rows[0]['from_version'], 'v1.0b2.87')

    def test_safe_backup_dir_rejects_path_traversal(self):
        from anetbbs.web.backups_admin import _safe_backup_dir
        with self.app.app_context(), tempfile.TemporaryDirectory() as install_dir:
            self.app.config['INSTALL_DIR'] = install_dir
            os.makedirs(os.path.join(install_dir, 'data', 'backups'))
            # Doesn't match the glob at all -- rejected before any path join.
            self.assertIsNone(_safe_backup_dir('../../../etc'))
            self.assertIsNone(_safe_backup_dir('anetbbs-backup-..'))
            # Matches the glob but the directory doesn't exist.
            self.assertIsNone(_safe_backup_dir('anetbbs-backup-20260712103000'))

    def test_safe_backup_dir_accepts_real_backup(self):
        from anetbbs.web.backups_admin import _safe_backup_dir
        with self.app.app_context(), tempfile.TemporaryDirectory() as install_dir:
            self.app.config['INSTALL_DIR'] = install_dir
            name = 'anetbbs-backup-20260712103000'
            os.makedirs(os.path.join(install_dir, 'data', 'backups', name))
            result = _safe_backup_dir(name)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith(name))


if __name__ == '__main__':
    unittest.main()
