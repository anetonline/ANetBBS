"""Regression tests filling a test-coverage gap found in a full
echomail-subsystem audit: anetbbs/echomail/tic.py's parse_tic() (raw
manifest parsing, no DB/app context needed) and scan_inbound() (the
directory-scan/dedup entry point real BinkP sessions call) had no
direct tests at all -- the only existing TIC test file covered one
narrow nodelist-detection edge case. Both handle peer-supplied data
landing on disk, a realistic surface for malformed input.
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ParseTicTests(unittest.TestCase):
    """Pure function, no DB needed."""

    def test_well_formed_tic_parses_all_fields(self):
        from anetbbs.echomail.tic import parse_tic
        content = (
            'File testfile.zip\n'
            'Area TESTAREA\n'
            'Desc A test file\n'
            'Ldesc Extra description line\n'
            'Crc deadbeef\n'
            'Size 12345\n'
            'Origin 1:1/1\n'
            'From 1:1/1\n'
            'To 1:1/2\n'
            'Pw secret\n'
        )
        parsed = parse_tic(content)
        self.assertEqual(parsed['file'], 'testfile.zip')
        self.assertEqual(parsed['area'], 'TESTAREA')
        self.assertEqual(parsed['desc'], 'A test file')
        self.assertEqual(parsed['ldesc'], ['Extra description line'])
        self.assertEqual(parsed['crc'], 'deadbeef')
        self.assertEqual(parsed['size'], 12345)
        self.assertEqual(parsed['origin'], '1:1/1')
        self.assertEqual(parsed['from'], '1:1/1')
        self.assertEqual(parsed['to'], '1:1/2')
        self.assertEqual(parsed['pw'], 'secret')

    def test_empty_content_returns_defaults_not_a_crash(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('')
        self.assertEqual(parsed['file'], '')
        self.assertEqual(parsed['area'], '')
        self.assertEqual(parsed['size'], 0)
        self.assertEqual(parsed['seenby'], [])

    def test_completely_unrelated_garbage_content_does_not_crash(self):
        """A .tic file that's actually something else entirely (binary
        data, a corrupted transfer, a peer's bug) must never raise --
        just yield mostly-empty defaults for whatever it can't parse."""
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('\x00\x01\xffbinary garbage\nnot a tic at all\n')
        self.assertEqual(parsed['file'], '')

    def test_malformed_size_field_is_ignored_not_crashed(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('File x.zip\nArea TEST\nSize not-a-number\n')
        self.assertEqual(parsed['size'], 0)

    def test_duplicate_scalar_fields_last_value_wins(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('File first.zip\nFile second.zip\n')
        self.assertEqual(parsed['file'], 'second.zip')

    def test_multiple_seenby_and_path_lines_accumulate(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic(
            'File x.zip\nArea TEST\n'
            'Seenby 1:1/1\nSeenby 1:1/2\n'
            'Path 1:1/1 1739000000 x\nPath 1:1/2 1739000001 y\n')
        self.assertEqual(parsed['seenby'], ['1:1/1', '1:1/2'])
        self.assertEqual(len(parsed['path']), 2)

    def test_area_field_is_uppercased(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('File x.zip\nArea lowercase.tag\n')
        self.assertEqual(parsed['area'], 'LOWERCASE.TAG')

    def test_unrecognized_lines_are_silently_ignored(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('File x.zip\nSomeWeirdField whatever\nArea TEST\n')
        self.assertEqual(parsed['file'], 'x.zip')
        self.assertEqual(parsed['area'], 'TEST')


class ScanInboundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tic_scan_inbound_test.db')
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

    def test_nonexistent_directory_returns_zero_not_a_crash(self):
        from anetbbs.echomail.tic import scan_inbound
        with self.app.app_context():
            self.assertEqual(scan_inbound('/no/such/directory/at/all'), 0)

    def test_empty_directory_returns_zero(self):
        from anetbbs.echomail.tic import scan_inbound
        work_dir = tempfile.mkdtemp(prefix='tic_scan_empty_')
        try:
            with self.app.app_context():
                self.assertEqual(scan_inbound(work_dir), 0)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_non_tic_files_are_ignored(self):
        from anetbbs.echomail.tic import scan_inbound
        work_dir = tempfile.mkdtemp(prefix='tic_scan_nontic_')
        try:
            with open(os.path.join(work_dir, 'somefile.zip'), 'wb') as f:
                f.write(b'not a tic')
            with open(os.path.join(work_dir, 'readme.txt'), 'w') as f:
                f.write('hello')
            with self.app.app_context():
                self.assertEqual(scan_inbound(work_dir), 0)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_a_real_tic_gets_processed_once(self):
        from anetbbs.models import db, FileArea, TicFile
        from anetbbs.echomail.tic import scan_inbound

        work_dir = tempfile.mkdtemp(prefix='tic_scan_real_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            os.makedirs(storage_dir, exist_ok=True)
            with self.app.app_context():
                area = FileArea(tag='SCANTEST', name='Scan Test',
                                storage_path=storage_dir,
                                is_active=True, is_subscribed=True)
                db.session.add(area)
                db.session.commit()

            with open(os.path.join(work_dir, 'realfile.zip'), 'wb') as f:
                f.write(b'contents')
            with open(os.path.join(work_dir, 'realfile.tic'), 'w', encoding='cp437') as f:
                f.write('File realfile.zip\nArea SCANTEST\nDesc x\n')

            with self.app.app_context():
                processed = scan_inbound(work_dir)
                self.assertEqual(processed, 1)
                self.assertEqual(
                    TicFile.query.filter_by(filename='realfile.zip').count(), 1)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_already_filed_tic_is_not_reprocessed_on_second_scan(self):
        """Real dedup mechanism: scan_inbound() peeks at each .tic's
        File: field and skips re-processing if that binary was already
        successfully filed -- confirms a re-run over the same inbound
        dir (e.g. a retried/duplicate scheduled scan) doesn't double-
        file the same real content."""
        from anetbbs.models import db, FileArea, TicFile
        from anetbbs.echomail.tic import scan_inbound

        work_dir = tempfile.mkdtemp(prefix='tic_scan_dedup_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            os.makedirs(storage_dir, exist_ok=True)
            with self.app.app_context():
                area = FileArea(tag='DEDUPTEST', name='Dedup Test',
                                storage_path=storage_dir,
                                is_active=True, is_subscribed=True)
                db.session.add(area)
                db.session.commit()

            with open(os.path.join(work_dir, 'dedupfile.zip'), 'wb') as f:
                f.write(b'contents')
            with open(os.path.join(work_dir, 'dedupfile.tic'), 'w', encoding='cp437') as f:
                f.write('File dedupfile.zip\nArea DEDUPTEST\nDesc x\n')

            with self.app.app_context():
                first = scan_inbound(work_dir)
                second = scan_inbound(work_dir)
                self.assertEqual(first, 1)
                self.assertEqual(second, 0,
                                 'a TIC whose binary was already filed must '
                                 'not be re-processed on a subsequent scan')
                self.assertEqual(
                    TicFile.query.filter_by(filename='dedupfile.zip').count(), 1)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_redelivered_already_filed_tic_is_swept_not_left_stuck(self):
        """Real bug found live: a sysop saw a real .tic sitting in
        inbound_dir, but "Rescan" reported none found and it just
        stayed there on every subsequent rescan (some file echoes
        redeliver an unchanged binary on a schedule; confirmed live --
        the exact same binary refiled weeks after its first successful
        filing). Different from test_already_filed_tic_is_not_
        reprocessed_on_second_scan above: THAT test's second .tic
        never actually exists on disk anymore by the second scan (the
        first scan already moved it into processed/) -- it doesn't
        exercise the dedup-skip branch's cleanup at all. This one
        drops a genuinely NEW copy of an already-filed TIC back into
        inbound_dir (a real redelivery) and confirms scan_inbound()
        both declines to re-file it AND sweeps it into processed/,
        rather than leaving it sitting in inbound_dir forever."""
        from anetbbs.models import db, FileArea, TicFile
        from anetbbs.echomail.tic import scan_inbound

        work_dir = tempfile.mkdtemp(prefix='tic_scan_redeliver_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            os.makedirs(storage_dir, exist_ok=True)
            with self.app.app_context():
                area = FileArea(tag='REDELIVER', name='Redeliver Test',
                                storage_path=storage_dir,
                                is_active=True, is_subscribed=True)
                db.session.add(area)
                db.session.commit()

            tic_body = 'File redeliver.zip\nArea REDELIVER\nDesc x\n'
            with open(os.path.join(work_dir, 'redeliver.zip'), 'wb') as f:
                f.write(b'contents')
            with open(os.path.join(work_dir, 'redeliver.tic'), 'w', encoding='cp437') as f:
                f.write(tic_body)

            with self.app.app_context():
                first = scan_inbound(work_dir)
                self.assertEqual(first, 1)

            # First filing's own cleanup already swept the originals --
            # confirm inbound_dir is genuinely empty of them before
            # simulating the redelivery, so the next scan_inbound()
            # call is unambiguously exercising a FRESH copy.
            self.assertFalse(os.path.exists(os.path.join(work_dir, 'redeliver.tic')))
            self.assertFalse(os.path.exists(os.path.join(work_dir, 'redeliver.zip')))

            # Simulate a real redelivery: the exact same manifest (and,
            # as some mailers do, the binary alongside it) lands in
            # inbound_dir again.
            with open(os.path.join(work_dir, 'redeliver.zip'), 'wb') as f:
                f.write(b'contents')
            with open(os.path.join(work_dir, 'redeliver.tic'), 'w', encoding='cp437') as f:
                f.write(tic_body)

            with self.app.app_context():
                second = scan_inbound(work_dir)
                # Not re-filed as a second real FileUpload/TicFile row...
                self.assertEqual(
                    TicFile.query.filter_by(filename='redeliver.zip').count(), 1)
                # ...but the sysop still sees it was handled, not silence.
                self.assertEqual(second, 1)

            processed_dir = os.path.join(work_dir, 'processed')
            self.assertTrue(os.path.isfile(os.path.join(processed_dir, 'redeliver.tic')))
            self.assertTrue(os.path.isfile(os.path.join(processed_dir, 'redeliver.zip')))
            self.assertFalse(os.path.exists(os.path.join(work_dir, 'redeliver.tic')),
                             'redelivered .tic must not be left stuck in inbound_dir')
            self.assertFalse(os.path.exists(os.path.join(work_dir, 'redeliver.zip')),
                             'redelivered binary must not be left stuck in inbound_dir')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_unset_storage_path_defaults_under_data_dir_not_var_lib(self):
        """Real bug found live: an area with no storage_path used to
        default to /var/lib/anetbbs/file_areas/<TAG> -- a path a
        non-root service user has no permission to create in any real
        install. Confirmed live: a legitimate tqwinfo.zip TIC failed
        with EACCES on every retry until someone manually built that
        path by hand as root. The default must instead live under the
        app's own DATA_DIR, same convention as uploads/avatars/echomail,
        so a freshly auto-created (or admin-created-without-a-path) area
        can actually file its first delivery unattended."""
        from anetbbs.models import db, FileArea, TicFile
        from anetbbs.echomail.tic import scan_inbound

        work_dir = tempfile.mkdtemp(prefix='tic_scan_nopath_')
        data_dir = tempfile.mkdtemp(prefix='tic_scan_datadir_')
        try:
            with self.app.app_context():
                area = FileArea(tag='NOPATHTEST', name='No Path Test',
                                storage_path=None,
                                is_active=True, is_subscribed=True)
                db.session.add(area)
                db.session.commit()

            with open(os.path.join(work_dir, 'nopathfile.zip'), 'wb') as f:
                f.write(b'contents')
            with open(os.path.join(work_dir, 'nopathfile.tic'), 'w', encoding='cp437') as f:
                f.write('File nopathfile.zip\nArea NOPATHTEST\nDesc x\n')

            orig_data_dir = self.app.config.get('DATA_DIR')
            self.app.config['DATA_DIR'] = data_dir
            try:
                with self.app.app_context():
                    processed = scan_inbound(work_dir)
                    self.assertEqual(processed, 1)
                    tic = TicFile.query.filter_by(filename='nopathfile.zip').first()
                    self.assertEqual(tic.status, 'filed')
                    area = FileArea.query.filter_by(tag='NOPATHTEST').first()
                    self.assertTrue(area.storage_path.startswith(data_dir))
                    self.assertNotIn('/var/lib/anetbbs', area.storage_path)
            finally:
                self.app.config['DATA_DIR'] = orig_data_dir
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            shutil.rmtree(data_dir, ignore_errors=True)

    def test_garbage_tic_file_is_processed_as_an_error_not_a_crash(self):
        """A .tic that peek-parsing can't make sense of must still be
        handed to process_tic() (which records a proper error status),
        never silently skipped or allowed to crash the whole scan."""
        from anetbbs.models import db, TicFile
        from anetbbs.echomail.tic import scan_inbound

        work_dir = tempfile.mkdtemp(prefix='tic_scan_garbage_')
        try:
            with open(os.path.join(work_dir, 'garbage.tic'), 'wb') as f:
                f.write(b'\x00\x01\xffnot a real tic at all')

            with self.app.app_context():
                processed = scan_inbound(work_dir)
                self.assertEqual(processed, 1)
                tic = TicFile.query.order_by(TicFile.id.desc()).first()
                self.assertEqual(tic.status, 'error')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
