"""Regression test: tools/import_users.py's CSV report holds every
migrated user's TEMPORARY PASSWORD in the clear (that's the report's
whole documented purpose -- so the sysop can notify each user). It used
to be written with plain open()'s default mode (0o666 & umask, normally
0644 on a typical Linux box) -- world-readable for as long as the file
exists, not just during a brief race window. Found in a security/
performance audit.

Fixed by pre-creating the file with owner-only (0600) permissions via
os.open()'s explicit mode instead of writing then chmod'ing afterward.
"""
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.import_users import run_import, ImportedUser  # noqa: E402


class ImportReportPermissionsTests(unittest.TestCase):
    def test_report_csv_is_owner_only(self):
        users = [
            ImportedUser(handle='alice', email='alice@example.com',
                         real_name='Alice Example', access_level=10),
            ImportedUser(handle='bob', email='bob@example.com',
                         real_name='Bob Example', access_level=10),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / 'import_report.csv'
            # dry_run=True still writes the report (see run_import) and
            # needs no real database session -- exactly what we want to
            # exercise here, the file-creation path itself.
            run_import(
                iter(users),
                dry_run=True,
                on_conflict='skip',
                email_domain='test.local',
                require_verify=False,
                report_path=report_path,
            )
            self.assertTrue(report_path.exists())
            mode = stat.S_IMODE(report_path.stat().st_mode)
            self.assertEqual(
                mode, 0o600,
                f'report CSV (contains plaintext temp passwords) has mode '
                f'{oct(mode)}, expected 0o600 (owner-only)')


if __name__ == '__main__':
    unittest.main()
