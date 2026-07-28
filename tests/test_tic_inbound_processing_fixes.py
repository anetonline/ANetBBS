"""Regression tests for a real live report: "getting files piled up in
inbound that are not being processed correctly" (a newly-subscribed
ANotherNetwork ansi-art TIC feed specifically named as an example).
Three real gaps in anetbbs/echomail/tic.py's process_tic(), all of
which cause a TIC to fail identically on every retry (never files,
binary sits in inbound forever) or to leave inbound growing unbounded
even on success:

1. CRC comparison did a raw string-equality check against a manifest
   Crc: field with no zero-padding -- _crc32_file() always formats its
   own result as exactly 8 hex digits, but some real-world TIC
   generators (older DOS-era tools especially) write the value without
   leading zeros (e.g. "a1b2c3" instead of "00a1b2c3"). A file whose
   real CRC was 00a1b2c3 then NEVER matched, no matter how many times
   it was retried.
2. The binary lookup used an exact-case match against the manifest's
   File: field -- Linux filesystems are case-sensitive, and plenty of
   FTN mailers re-case filenames on the wire regardless of what the
   TIC's own File: field says. A binary genuinely sitting right there
   under a different case was never found.
3. On success, the binary was only ever COPIED into the file area's
   storage -- the original .tic manifest and binary were never removed
   from inbound_dir, so inbound grew forever with every TIC ever
   received, successes and failures indistinguishable from a directory
   listing alone.
"""
import os
import sys
import shutil
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class ParseTicCrcPaddingTests(unittest.TestCase):
    """Pure function, no DB needed."""

    def test_unpadded_crc_is_zero_padded_to_eight_digits(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('File x.zip\nArea TEST\nCrc a1b2c3\n')
        self.assertEqual(parsed['crc'], '00a1b2c3')

    def test_already_padded_crc_is_unchanged(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('File x.zip\nArea TEST\nCrc 00a1b2c3\n')
        self.assertEqual(parsed['crc'], '00a1b2c3')

    def test_0x_prefixed_crc_is_stripped_and_padded(self):
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('File x.zip\nArea TEST\nCrc 0xa1b2c3\n')
        self.assertEqual(parsed['crc'], '00a1b2c3')

    def test_missing_crc_field_stays_empty_not_all_zeros(self):
        """A genuinely absent Crc: field must not become a fake
        '00000000' -- that would make process_tic() think a CRC check
        was requested when the manifest never asked for one."""
        from anetbbs.echomail.tic import parse_tic
        parsed = parse_tic('File x.zip\nArea TEST\n')
        self.assertEqual(parsed['crc'], '')


class TicInboundProcessingFixesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tic_inbound_fixes_test.db')
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

    # ---- case-insensitive binary lookup ----

    def test_uppercase_wire_filename_matches_lowercase_manifest_file_field(self):
        """Real-world shape: mailer delivered ANSI0042.ZIP on the wire
        (all-caps, common FTN convention) but the TIC's own File: field
        says the lowercase name -- must still find and file it."""
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_case_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('CASETEST', storage_dir)

            # Binary on disk is UPPERCASE (as the wire delivered it).
            with open(os.path.join(inbound_dir, 'ANSI0042.ZIP'), 'wb') as f:
                f.write(b'ansi art contents')

            tic_path = os.path.join(inbound_dir, 'ansi0042.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                # Manifest's File: field is lowercase.
                f.write('File ansi0042.zip\nArea CASETEST\nDesc ANSI art\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed',
                                 f'expected filed, got {tic.status}: {tic.error_message}')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_still_errors_when_truly_no_matching_binary_exists(self):
        """Baseline / guard against a too-broad fix: the fallback must
        not paper over a genuinely missing binary."""
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_case_missing_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('MISSINGTEST', storage_dir)

            tic_path = os.path.join(inbound_dir, 'nofile.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write('File doesnotexist.zip\nArea MISSINGTEST\nDesc x\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'error')
                self.assertIn('not found', tic.error_message or '')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ---- CRC zero-padding tolerance (end to end through process_tic) ----

    def test_unpadded_crc_from_manifest_still_matches_real_file(self):
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_crc_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('CRCTEST', storage_dir)

            # Search for content whose CRC32 genuinely starts with a
            # zero nibble, so stripping it produces a real unpadded
            # value rather than a no-op.
            for i in range(1000):
                content = f'some ansi art bytes here {i}'.encode()
                real_crc = f'{zlib.crc32(content) & 0xffffffff:08x}'
                if real_crc.startswith('0'):
                    break
            else:
                self.fail('could not find fixture content with a '
                         'leading-zero CRC32 -- test bug')

            bin_name = 'crcfile.zip'
            with open(os.path.join(inbound_dir, bin_name), 'wb') as f:
                f.write(content)

            # Simulate a generator that doesn't zero-pad.
            unpadded = real_crc.lstrip('0') or '0'
            self.assertNotEqual(unpadded, real_crc,
                                "test fixture didn't actually produce an "
                                "unpadded CRC -- adjust the content bytes")

            tic_path = os.path.join(inbound_dir, 'crcfile.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea CRCTEST\nCrc {unpadded}\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed',
                                 f'expected filed, got {tic.status}: {tic.error_message}')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_genuinely_wrong_crc_still_rejected(self):
        """Baseline / guard against a too-broad fix: padding tolerance
        must not disable the CRC check entirely."""
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_crc_wrong_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('CRCWRONGTEST', storage_dir)

            bin_name = 'wrongcrc.zip'
            with open(os.path.join(inbound_dir, bin_name), 'wb') as f:
                f.write(b'actual content')

            tic_path = os.path.join(inbound_dir, 'wrongcrc.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea CRCWRONGTEST\nCrc deadbeef\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'error')
                self.assertIn('crc', (tic.error_message or '').lower())
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ---- inbound cleanup on success ----

    def test_successful_filing_moves_originals_out_of_inbound_root(self):
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_cleanup_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('CLEANTEST', storage_dir)

            bin_name = 'cleanfile.zip'
            bin_path = os.path.join(inbound_dir, bin_name)
            with open(bin_path, 'wb') as f:
                f.write(b'contents')
            tic_path = os.path.join(inbound_dir, 'cleanfile.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea CLEANTEST\nDesc x\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'filed')

            # Originals must be gone from inbound_dir's top level...
            self.assertFalse(os.path.exists(bin_path))
            self.assertFalse(os.path.exists(tic_path))
            # ...moved to processed/, not deleted outright.
            processed_dir = os.path.join(inbound_dir, 'processed')
            self.assertTrue(os.path.isfile(os.path.join(processed_dir, bin_name)))
            self.assertTrue(os.path.isfile(os.path.join(processed_dir, 'cleanfile.tic')))
            # The area's own copy is untouched.
            self.assertTrue(os.path.isfile(os.path.join(storage_dir, bin_name)))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_failed_filing_leaves_originals_in_place_for_retry(self):
        """A TIC that errors out must NOT be moved -- scan_inbound()'s
        retry-on-next-scan behavior depends on the .tic still being
        there, and a sysop diagnosing a stuck TIC needs the binary to
        still be exactly where the error message says it looked."""
        from anetbbs.echomail.tic import process_tic

        work_dir = tempfile.mkdtemp(prefix='tic_cleanup_fail_test_')
        try:
            storage_dir = os.path.join(work_dir, 'storage')
            inbound_dir = os.path.join(work_dir, 'inbound')
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(inbound_dir, exist_ok=True)

            with self.app.app_context():
                self._make_area('CLEANFAILTEST', storage_dir)

            bin_name = 'failfile.zip'
            bin_path = os.path.join(inbound_dir, bin_name)
            with open(bin_path, 'wb') as f:
                f.write(b'contents')
            tic_path = os.path.join(inbound_dir, 'failfile.tic')
            with open(tic_path, 'w', encoding='cp437') as f:
                f.write(f'File {bin_name}\nArea CLEANFAILTEST\nCrc baadf00d\n')

            with self.app.app_context():
                tic = process_tic(tic_path, inbound_dir)
                self.assertEqual(tic.status, 'error')

            self.assertTrue(os.path.isfile(bin_path))
            self.assertTrue(os.path.isfile(tic_path))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
