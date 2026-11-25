"""update.sh's backup_sqlite(): a consistent, integrity-checked database
backup made by the service user, never by root.

History: a raw cp(1) of a live WAL database can silently drop committed
transactions, and the sqlite3(1) CLI was an undocumented dependency (a real
bug report: sysops without it could not update). The function now always
uses Python's sqlite3 backup API (the same Online Backup API the CLI's
.backup uses), requires PRAGMA integrity_check to be exactly "ok", and runs
entirely through as_service, so root never opens the service user's files
(audit H-1).

The REAL function is extracted from update.sh and run in bash with a stand-in
as_service that records each call and runs it as the current user.
"""
import re
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _extract():
    content = (REPO_ROOT / 'update.sh').read_text()
    m = re.search(r'^backup_sqlite\(\) \{.*?\n\}\n', content, re.DOTALL | re.MULTILINE)
    assert m, 'could not find backup_sqlite() in update.sh'
    return m.group(0)


class BackupSqliteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fn = _extract()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.calls = self.tmp / 'as_service.calls'

    def _run(self, src, dst):
        script = (
            'set -e -o pipefail\n'
            'warn() { echo "WARN: $*"; }\n'
            'ok() { echo "OK: $*"; }\n'
            f'as_service() {{ printf "%s\\n" "$1" >> "{self.calls}"; "$@"; }}\n'
            + self.fn +
            f'if backup_sqlite "{src}" "{dst}" "test DB"; then echo RESULT=0; else echo RESULT=1; fi\n')
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=60)

    def _make_db(self, path, rows=50, wal=False):
        c = sqlite3.connect(path)
        if wal:
            c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE t (x INTEGER)')
        c.executemany('INSERT INTO t VALUES (?)', [(i,) for i in range(rows)])
        c.commit()
        return c

    def test_backup_is_complete_and_integrity_checked(self):
        src, dst = self.tmp / 'a.db', self.tmp / 'a.db.bak'
        conn = self._make_db(src, wal=True)        # held open: data still in the WAL
        r = self._run(src, dst)
        conn.close()
        self.assertIn('RESULT=0', r.stdout, r.stdout + r.stderr)
        self.assertIn('integrity_check ok', r.stdout)
        self.assertEqual(sqlite3.connect(dst).execute('SELECT count(*) FROM t').fetchone()[0], 50)

    def test_everything_runs_through_as_service(self):
        src, dst = self.tmp / 'a.db', self.tmp / 'a.db.bak'
        self._make_db(src).close()
        self._run(src, dst)
        calls = self.calls.read_text().split()
        self.assertEqual(calls, ['test', 'env'])   # existence check, then the backup
        code = self.fn
        for line in code.splitlines():
            s = line.strip()
            if s.startswith(('sqlite3 ', 'python3 ', 'cp ', 'cat ')):
                self.fail(f'root-side file access left in backup_sqlite: {s}')

    def test_missing_source_is_not_a_failure(self):
        r = self._run(self.tmp / 'missing.db', self.tmp / 'x.bak')
        self.assertIn('RESULT=0', r.stdout)

    def test_non_database_source_fails(self):
        src = self.tmp / 'junk.db'
        src.write_bytes(b'this is not sqlite' * 100)
        r = self._run(src, self.tmp / 'junk.bak')
        self.assertIn('RESULT=1', r.stdout, r.stdout + r.stderr)
        self.assertIn("expected integrity_check 'ok'", r.stdout)

    def test_unwritable_destination_fails(self):
        src = self.tmp / 'a.db'
        self._make_db(src).close()
        r = self._run(src, self.tmp / 'no-such-dir' / 'a.bak')
        self.assertIn('RESULT=1', r.stdout)

    def test_caller_aborts_on_failure(self):
        content = (REPO_ROOT / 'update.sh').read_text()
        self.assertRegex(content, r'if ! backup_sqlite "\$INSTALL_DIR/data/anetbbs\.db"[^\n]*; then\n'
                                  r'\s+fail "Could not back up data/anetbbs\.db[^\n]*\n\s+exit 1')


if __name__ == '__main__':
    unittest.main()
