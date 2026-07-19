"""Regression test: a real sysop reported the TIC log showing an error on
files that were never supposed to be nodelists.

Root cause (anetbbs/echomail/tic.py, `process_tic()`'s nodelist
auto-import step): the TIC transfer itself always succeeds first
(`tic.status = 'filed'`, committed) before this runs -- nodelist
auto-import is a secondary, best-effort enrichment for areas flagged
`is_nodelist_source`. When the incoming file doesn't happen to look
nodelist-shaped (which is normal for a mixed-use area, or just a day
with no nodelist release), the failure used to be appended straight into
`tic.error_message` -- the same field used for genuine transfer failures
-- so a perfectly successful file transfer displayed as an error in the
TIC log. Fixed by logging it (debug level) instead of flagging the
TicFile record; the transfer's own status/error_message now only ever
reflect the transfer itself.
"""
import os
import sys
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class TicNodelistFalsePositiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tic_nodelist_test.db')
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

    def test_non_nodelist_file_in_a_nodelist_area_is_not_flagged_as_an_error(self):
        from anetbbs.models import db, FileArea
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_nodelist_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                area = FileArea(
                    tag='NLTEST', name='Nodelist Test Area',
                    storage_path=storage_dir,
                    is_active=True, is_subscribed=True,
                    is_nodelist_source=True, nodelist_domain='testnet',
                )
                db.session.add(area)
                db.session.commit()

            # An ordinary ZIP whose only member does NOT match any
            # nodelist-shaped naming convention (_looks_like_nodelist).
            bin_name = 'general_file.zip'
            bin_path = os.path.join(inbound_dir, bin_name)
            with zipfile.ZipFile(bin_path, 'w') as zf:
                zf.writestr('random_data.bin', b'not a nodelist at all')

            tic_path = os.path.join(inbound_dir, 'general_file.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea NLTEST\nDesc just a regular file\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed',
                                 'the transfer itself succeeded and must still '
                                 'show as filed, not error')
                self.assertNotIn('nodelist auto-import failed', tic.error_message or '',
                                 'a file that simply is not nodelist-shaped must not '
                                 'be recorded as a transfer error')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
