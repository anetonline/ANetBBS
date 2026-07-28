"""Regression test for a real live report: 7 TIC files from the same
peer showed "binary not found" errors right after the v1.0b2.225
extension-collision fix started letting .tic manifests through
correctly for the first time. Live diagnosis (`ls` on the actual
inbound directory) showed 6 of the 7 binaries WERE present, just under
their real, longer filenames -- the manifest's File: field held a
classic DOS 8.3-truncated name instead (first 8 chars of the base name
+ the extension), e.g. manifest says "white_pa.zip" but the file that
actually arrived is "white_paper_3.0.zip" (byte-identical size).

This is a different gap than the case-insensitive fallback added in
v1.0b2.224 (test_tic_inbound_processing_fixes.py) -- that one only
handles a pure case difference on an otherwise-identical filename, not
a length truncation.
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class Dos83TruncateHelperTests(unittest.TestCase):
    """Pure function, no DB needed -- exact real-world cases from the
    live report, confirmed via ls output on the actual server."""

    def test_matches_all_confirmed_live_examples(self):
        from anetbbs.echomail.tic import _dos83_truncate
        cases = [
            ('blndr2025d.zip', 'blndr202.zip'),
            ('chromeintro.zip', 'chromein.zip'),
            ('corrupteddmp.zip', 'corrupte.zip'),
            ('fire-2026-ansi-calendar.zip', 'fire-202.zip'),
            ('slackpack002.zip', 'slackpac.zip'),
            ('white_paper_3.0.zip', 'white_pa.zip'),
        ]
        for real_name, expected in cases:
            self.assertEqual(_dos83_truncate(real_name), expected,
                             f'{real_name!r} should truncate to {expected!r}')

    def test_short_name_already_within_8_3_is_unchanged(self):
        from anetbbs.echomail.tic import _dos83_truncate
        self.assertEqual(_dos83_truncate('zal.zip'), 'zal.zip')

    def test_no_extension_just_truncates_base(self):
        from anetbbs.echomail.tic import _dos83_truncate
        self.assertEqual(_dos83_truncate('averylongfilename'), 'averylon')


class TicDos83TruncatedFilenameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tic_dos83_test.db')
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

    def _make_area(self, tag, storage_dir):
        from anetbbs.models import db, FileArea
        area = FileArea(tag=tag, name=tag, storage_path=storage_dir,
                        is_active=True, is_subscribed=True)
        db.session.add(area)
        db.session.commit()
        return area

    def test_real_world_white_paper_example_still_files_correctly(self):
        """The exact live example: manifest says white_pa.zip, the
        actual binary on disk is white_paper_3.0.zip."""
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_dos83_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('DOS83TEST', storage_dir)

            content = b'ansi art contents, whatever length'
            with open(os.path.join(inbound_dir, 'white_paper_3.0.zip'), 'wb') as f:
                f.write(content)

            tic_path = os.path.join(inbound_dir, 'ti_00001.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File white_pa.zip\nArea DOS83TEST\nDesc test\n'
                        f'Size {len(content)}\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed',
                                 f'expected filed, got {tic.status}: {tic.error_message}')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_exact_match_still_preferred_over_truncated_fallback(self):
        """Baseline / guard against a too-broad fix: if a file with the
        EXACT manifest name exists, it must be used -- never let the
        truncation fallback shadow a real exact match."""
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_dos83_exact_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('DOS83EXACT', storage_dir)

            exact_content = b'the real exact-match file'
            with open(os.path.join(inbound_dir, 'short.zip'), 'wb') as f:
                f.write(exact_content)

            tic_path = os.path.join(inbound_dir, 'ti_00002.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File short.zip\nArea DOS83EXACT\nDesc test\n'
                        f'Size {len(exact_content)}\n')

            with self.app.app_context():
                from anetbbs.models import TicFile
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed')
                filed = TicFile.query.filter_by(filename='short.zip').first()
                self.assertIsNotNone(filed)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_still_errors_when_no_truncation_match_exists_either(self):
        """Baseline / guard: a genuinely missing binary (like the live
        report's mist0226.zip, absent from both inbound/ and
        inbound/processed/) must still error, not silently misfile
        some unrelated file."""
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_dos83_missing_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('DOS83MISSING', storage_dir)

            # A completely unrelated file present -- must not get
            # coincidentally picked as a match.
            with open(os.path.join(inbound_dir, 'unrelated.zip'), 'wb') as f:
                f.write(b'not the file you are looking for')

            tic_path = os.path.join(inbound_dir, 'ti_00003.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write('File mist0226.zip\nArea DOS83MISSING\nDesc test\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'error')
                self.assertIn('binary not found', tic.error_message)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
