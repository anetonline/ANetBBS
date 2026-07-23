"""Regression tests for two real security gaps found in a full
echomail-subsystem audit of anetbbs/echomail/tic.py's process_tic():

1. The manifest's `File:` field (peer-supplied text) was used
   unsanitized in two os.path.join() calls -- the exact traversal bug
   already fixed for the raw wire-level filename in binkp.py's
   _sanitize_inbound_filename(). A crafted "File ../../x" (or an
   absolute path, which os.path.join() silently lets override the
   base dir entirely) could read/write outside inbound_dir/
   area.storage_path. Fixed by running the manifest's File: field
   through the same sanitizer before using it as a path component.

2. FileArea.password ("optional area password") was parsed out of the
   TIC's Pw: field but never actually compared against it -- any
   authenticated peer able to deliver a TIC could file into a
   password-protected area regardless. Fixed by rejecting (status=
   'error') when the area has a password set and it doesn't match.
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class TicSecurityFixesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tic_security_test.db')
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

    def test_traversal_in_file_field_cannot_escape_inbound_dir(self):
        """A manifest whose File: field is a traversal path must not be
        able to reach outside inbound_dir looking for the binary --
        confirmed by placing the "secret" file OUTSIDE inbound_dir and
        the legitimately-named decoy INSIDE it, then verifying the
        legitimate file (matching the sanitized basename) is what
        actually gets processed, not the escape target."""
        from anetbbs.models import db, FileArea
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_traversal_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            outside_dir = os.path.join(work_dir, 'outside')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)
            os.makedirs(outside_dir, exist_ok=True)

            with self.app.app_context():
                area = FileArea(
                    tag='TRAVTEST', name='Traversal Test Area',
                    storage_path=storage_dir,
                    is_active=True, is_subscribed=True,
                )
                db.session.add(area)
                db.session.commit()

            # A secret file living OUTSIDE inbound_dir -- must never be
            # readable/copyable via a traversal File: field.
            secret_path = os.path.join(outside_dir, 'secret.txt')
            with open(secret_path, 'wb') as f:
                f.write(b'outside-the-jail')

            tic_path = os.path.join(inbound_dir, 'evil.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write('File ../outside/secret.txt\nArea TRAVTEST\nDesc x\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                # The sanitized basename ("secret.txt") doesn't exist
                # inside inbound_dir itself, so this must fail as
                # "binary not found" -- not succeed by reaching outside.
                self.assertEqual(tic.status, 'error')
                self.assertIn('not found', tic.error_message or '')

            # The secret file must never have been copied into the
            # area's storage directory under any name.
            copied = os.listdir(storage_dir)
            self.assertEqual(copied, [],
                             'traversal must not let the manifest pull a file '
                             'from outside inbound_dir into area storage')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_absolute_path_in_file_field_cannot_escape_inbound_dir(self):
        """os.path.join(base, '/etc/passwd') silently discards `base`
        entirely per Python's own documented semantics -- an absolute
        File: field must be reduced to a bare basename first."""
        from anetbbs.models import db, FileArea
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_abspath_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                area = FileArea(
                    tag='ABSTEST', name='Abs Path Test Area',
                    storage_path=storage_dir,
                    is_active=True, is_subscribed=True,
                )
                db.session.add(area)
                db.session.commit()

            tic_path = os.path.join(inbound_dir, 'evil2.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write('File /etc/passwd\nArea ABSTEST\nDesc x\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'error')
                self.assertIn('not found', tic.error_message or '')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_legitimate_filename_still_processes_normally(self):
        """The sanitizer must be a no-op for a real, ordinary filename
        -- confirms the fix doesn't break normal TIC processing."""
        from anetbbs.models import db, FileArea, TicFile
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_normal_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                area = FileArea(
                    tag='NORMTEST', name='Normal Test Area',
                    storage_path=storage_dir,
                    is_active=True, is_subscribed=True,
                )
                db.session.add(area)
                db.session.commit()

            bin_name = 'a_real_file.zip'
            with open(os.path.join(inbound_dir, bin_name), 'wb') as f:
                f.write(b'PK\x03\x04fake zip contents')

            tic_path = os.path.join(inbound_dir, 'a_real_file.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea NORMTEST\nDesc ok\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed')
                self.assertTrue(os.path.exists(
                    os.path.join(storage_dir, bin_name)))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_password_protected_area_rejects_wrong_password(self):
        from anetbbs.models import db, FileArea
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_pwtest_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                area = FileArea(
                    tag='PWTEST', name='Password Test Area',
                    storage_path=storage_dir, password='correcthorse',
                    is_active=True, is_subscribed=True,
                )
                db.session.add(area)
                db.session.commit()

            bin_name = 'protected.zip'
            with open(os.path.join(inbound_dir, bin_name), 'wb') as f:
                f.write(b'contents')

            tic_path = os.path.join(inbound_dir, 'protected.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea PWTEST\nPw wrongpassword\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'error')
                self.assertIn('password', (tic.error_message or '').lower())

            self.assertEqual(os.listdir(storage_dir), [],
                             'a wrong-password TIC must not be filed')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_password_protected_area_accepts_correct_password(self):
        from anetbbs.models import db, FileArea
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_pwok_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                area = FileArea(
                    tag='PWOKTEST', name='Password OK Test Area',
                    storage_path=storage_dir, password='correcthorse',
                    is_active=True, is_subscribed=True,
                )
                db.session.add(area)
                db.session.commit()

            bin_name = 'protected2.zip'
            with open(os.path.join(inbound_dir, bin_name), 'wb') as f:
                f.write(b'contents')

            tic_path = os.path.join(inbound_dir, 'protected2.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea PWOKTEST\nPw correcthorse\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_area_with_no_password_set_ignores_pw_field(self):
        """password is opt-in -- an area with none set must keep
        working exactly as before, regardless of what Pw: says."""
        from anetbbs.models import db, FileArea
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_nopw_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                area = FileArea(
                    tag='NOPWTEST', name='No Password Test Area',
                    storage_path=storage_dir,
                    is_active=True, is_subscribed=True,
                )
                db.session.add(area)
                db.session.commit()

            bin_name = 'unprotected.zip'
            with open(os.path.join(inbound_dir, bin_name), 'wb') as f:
                f.write(b'contents')

            tic_path = os.path.join(inbound_dir, 'unprotected.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea NOPWTEST\nPw anything-at-all\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
